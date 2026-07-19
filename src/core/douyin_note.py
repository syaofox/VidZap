"""Douyin note (photo slideshow) extraction and download.

This module re-exports from browser_extractor and adds the download orchestration.
The NoteExtractor ABC and implementations live in browser_extractor.py.
"""
import asyncio
import logging
import re
from pathlib import Path

import httpx

from core.browser_extractor import (
    _CancelledError,
    _download_media,
    _get_extractor,
    is_douyin_note_url,  # noqa: F401 - re-exported
)
from core.db import get_connection
from core.ytdlp_handler import (
    DOWNLOADS_DIR,
    DownloadCancelledError,
    _format_speed,
    update_download_status,
)

logger = logging.getLogger(__name__)

DOUYIN_NOTE_PATTERN = re.compile(r"https?://(?:www\.)?douyin\.com/note/(\d+)")


# =============================================================================
# 提取（委托给 browser_extractor）
# =============================================================================


async def extract_note_images(url: str, cookie_file: str | None = None) -> dict:
    """Extract image URLs from a Douyin note page. Uses configured browser engine.

    Returns dict with keys: id, title, thumbnail, image_urls, image_count.
    """
    extractor = _get_extractor()
    try:
        return await extractor.extract(url, cookie_file)
    finally:
        await extractor.close()


# =============================================================================
# 下载
# =============================================================================


def _cookie_file_to_httpx_dict(cookie_file: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    try:
        content = Path(cookie_file).read_text()
    except (OSError, FileNotFoundError):
        return cookies
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name, value = parts[5], parts[6]
        if name:
            cookies[name] = value
    return cookies


async def download_note_images(
    url: str,
    cookie_file: str | None = None,
    progress_callback=None,
    cancel_event: asyncio.Event | None = None,
    download_id: int | None = None,
) -> str:
    """Download all images and videos from a Douyin note page.

    Returns the path to the output directory.
    """
    match = DOUYIN_NOTE_PATTERN.match(url)
    if not match:
        raise ValueError(f"Not a Douyin note URL: {url}")
    note_id = match.group(1)

    info = await extract_note_images(url, cookie_file)
    image_urls = info["image_urls"]
    video_urls = info.get("video_urls", [])
    title = info["title"]

    media_list: list[tuple[str, str]] = []
    for u in image_urls:
        media_list.append((u, "image"))
    for u in video_urls:
        media_list.append((u, "video"))
    total = len(media_list)

    if total == 0:
        raise ValueError("未找到可下载的媒体文件")

    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:80]
    output_dir = DOWNLOADS_DIR / "douyin" / f"note_{note_id}_{safe_title}"
    output_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    img_count = 0
    vid_count = 0
    total_bytes = 0
    start_time = asyncio.get_event_loop().time()

    httpx_cookies = _cookie_file_to_httpx_dict(cookie_file) if cookie_file else {}
    async with httpx.AsyncClient(
        timeout=60,
        follow_redirects=True,
        cookies=httpx_cookies or None,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
        },
    ) as client:
        for i, (media_url, media_type) in enumerate(media_list):
            if cancel_event and cancel_event.is_set():
                raise DownloadCancelledError("用户取消下载")

            if media_type == "image":
                ext = ".jpg"
                if ".webp" in media_url:
                    ext = ".webp"
                elif ".png" in media_url:
                    ext = ".png"
                elif ".heic" in media_url:
                    ext = ".heic"
                img_count += 1
                filename = f"img_{img_count:03d}{ext}"
            else:
                vid_count += 1
                filename = f"video_{vid_count:03d}.mp4"

            filepath = output_dir / filename

            try:
                await _download_media(
                    media_url, filepath, media_type, client, cancel_event
                )
                downloaded += 1

                percent = (downloaded / total) * 100
                elapsed = asyncio.get_event_loop().time() - start_time
                speed = total_bytes / elapsed if elapsed > 0 else 0
                speed_str = _format_speed(speed)
                remaining = total - downloaded
                eta_sec = (elapsed / downloaded * remaining) if downloaded > 0 else 0
                eta_str = (
                    f"{int(eta_sec)}s"
                    if eta_sec < 60
                    else f"{int(eta_sec // 60)}:{int(eta_sec % 60):02d}"
                )

                logger.info(
                    "Downloaded %s %d/%d: %s", media_type, downloaded, total, filename
                )

                if progress_callback:
                    try:
                        progress_callback(percent, speed_str, eta_str)
                    except Exception:
                        pass

            except _CancelledError:
                raise DownloadCancelledError("用户取消下载")
            except DownloadCancelledError:
                raise
            except Exception as e:
                logger.warning("Failed to download %s %d: %s", media_type, i + 1, e)

    if downloaded == 0:
        raise ValueError("所有文件下载失败")

    parts = []
    if img_count:
        parts.append(f"{img_count} images")
    if vid_count:
        parts.append(f"{vid_count} videos")
    meta_path = output_dir / "info.txt"
    meta_path.write_text(
        f"Title: {title}\nURL: {url}\nDownloaded: {downloaded}/{total} ({', '.join(parts)})\n"
    )

    if download_id is not None:
        update_download_status(
            download_id,
            "completed",
            file_path=str(output_dir),
        )

    if progress_callback:
        try:
            progress_callback(100, "完成", "0")
        except Exception:
            pass

    return str(output_dir)


def get_note_download_history() -> list[dict]:
    """Get download history entries for Douyin note (image) downloads."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM downloads WHERE format_id = 'images' ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]
