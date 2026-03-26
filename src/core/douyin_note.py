import asyncio
import logging
import os
import re
import subprocess
from pathlib import Path

import httpx
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from core.db import get_connection
from core.ytdlp_handler import (
    DOWNLOADS_DIR,
    DownloadCancelledError,
    _format_speed,
    update_download_status,
)

logger = logging.getLogger(__name__)

_XVFB_DISPLAY = ":99"
_XVFB_STARTED = False


def _ensure_xvfb() -> str:
    """Start Xvfb if needed and return the DISPLAY value to use."""
    global _XVFB_STARTED
    if _XVFB_STARTED:
        return _XVFB_DISPLAY
    # Check if Xvfb is already running on the target display
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"Xvfb {_XVFB_DISPLAY}"],
            capture_output=True,
            timeout=3,
        )
        if result.returncode == 0:
            _XVFB_STARTED = True
            os.environ["DISPLAY"] = _XVFB_DISPLAY
            return _XVFB_DISPLAY
    except Exception:
        pass
    # Start Xvfb
    try:
        subprocess.Popen(
            ["Xvfb", _XVFB_DISPLAY, "-screen", "0", "1280x720x24", "-nolisten", "tcp"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import time

        time.sleep(1)
        _XVFB_STARTED = True
        os.environ["DISPLAY"] = _XVFB_DISPLAY
        logger.info("Started Xvfb on %s", _XVFB_DISPLAY)
    except Exception as e:
        logger.warning("Failed to start Xvfb: %s", e)
    return _XVFB_DISPLAY


DOUYIN_NOTE_PATTERN = re.compile(r"https?://(?:www\.)?douyin\.com/note/(\d+)")


def is_douyin_note_url(url: str) -> bool:
    """Check if the URL is a Douyin note (image slideshow) URL."""
    return bool(DOUYIN_NOTE_PATTERN.match(url))


def _parse_netscape_cookies(cookie_file: str) -> list[dict[str, str | bool]]:
    """Parse Netscape cookie file into Playwright-compatible cookie dicts."""
    cookies: list[dict[str, str | bool]] = []
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
        domain, _flag, path, secure, _expires, name, value = parts[:7]
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": secure.upper() == "TRUE",
            }
        )
    return cookies


def _extract_media_from_api(api_data: list[dict], note_id: str) -> tuple[str, list[str], list[str]]:
    """Extract title, image_urls, and video_urls from intercepted API responses.

    Returns (title, image_urls, video_urls).
    """
    for data in api_data:
        if not isinstance(data, dict):
            continue
        aweme_list = data.get("aweme_list", [])
        if not isinstance(aweme_list, list):
            continue
        for item in aweme_list:
            if not isinstance(item, dict):
                continue
            if str(item.get("aweme_id", "")) != note_id:
                continue

            title = item.get("desc", "") or item.get("caption", "")

            # Extract images from images[] field
            image_urls: list[str] = []
            for img in item.get("images", []) or []:
                if not isinstance(img, dict):
                    continue
                url_list = img.get("url_list", [])
                if url_list and url_list[0].startswith("http"):
                    image_urls.append(url_list[0])

            # Extract video from video.play_addr.url_list
            video_urls: list[str] = []
            video = item.get("video", {})
            if isinstance(video, dict):
                play_addr = video.get("play_addr", {})
                if isinstance(play_addr, dict):
                    for u in play_addr.get("url_list", []):
                        if u.startswith("http"):
                            video_urls.append(u)

            return title, image_urls, video_urls

    return "", [], []


async def _extract_images_from_dom(page) -> tuple[str, list[str], list[str]]:
    """Extract note image and video URLs from the rendered DOM.

    Returns (title, image_urls, video_urls).
    """
    result = await page.evaluate("""() => {
        const imgUrls = new Set();
        const vidUrls = new Set();
        let title = '';

        // Get page title
        title = document.title.replace(/ - 抖音$/, '').trim();

        // Helper: check if URL is a note slideshow image
        function isNoteImg(src) {
            return src && src.includes('tplv-dy-aweme-images');
        }

        // Helper: check if URL is a note video
        function isNoteVideo(src) {
            return src && (src.includes('douyinvod') || src.includes('douyinstatic'))
                   && !src.includes('.js') && !src.includes('.css');
        }

        // Strategy 1: <img> elements with aweme-images marker
        const imgs = document.querySelectorAll('img');
        for (const img of imgs) {
            if (isNoteImg(img.src) && (img.naturalWidth >= 200 || img.naturalHeight >= 200)) {
                imgUrls.add(img.src);
            }
        }

        // Strategy 2: CSS background images
        const allEls = document.querySelectorAll('*');
        for (const el of allEls) {
            try {
                const bg = getComputedStyle(el).backgroundImage;
                if (!bg || bg === 'none') continue;
                const match = bg.match(/url\\(["']?(.+?)["']?\\)/);
                if (match && isNoteImg(match[1])) {
                    imgUrls.add(match[1]);
                }
            } catch(e) {}
        }

        // Strategy 3: data-src lazy loaded images
        const lazyImgs = document.querySelectorAll('img[data-src]');
        for (const img of lazyImgs) {
            const src = img.getAttribute('data-src');
            if (isNoteImg(src)) {
                imgUrls.add(src);
            }
        }

        // Strategy 4: srcset images
        for (const img of imgs) {
            if (!img.srcset) continue;
            const parts = img.srcset.split(',');
            for (const part of parts) {
                const url = part.trim().split(' ')[0];
                if (isNoteImg(url)) {
                    imgUrls.add(url);
                }
            }
        }

        // Strategy 5: <video> and <source> elements
        document.querySelectorAll('video, video source').forEach(el => {
            const src = el.src || el.currentSrc || '';
            if (isNoteVideo(src)) {
                vidUrls.add(src);
            }
        });

        return {
            title: title,
            imgUrls: Array.from(imgUrls),
            vidUrls: Array.from(vidUrls),
        };
    }""")

    # Deduplicate images by stripping query params
    seen = set()
    unique_imgs: list[str] = []
    for u in result.get("imgUrls", []):
        base = u.split("?")[0]
        if base not in seen:
            seen.add(base)
            unique_imgs.append(u)

    # Deduplicate videos
    seen_v = set()
    unique_vids: list[str] = []
    for u in result.get("vidUrls", []):
        base = u.split("?")[0]
        if base not in seen_v:
            seen_v.add(base)
            unique_vids.append(u)

    return result.get("title", ""), unique_imgs, unique_vids


async def extract_note_images(url: str, cookie_file: str | None = None) -> dict:
    """Extract image URLs from a Douyin note page via Playwright.

    Visits Douyin homepage first to acquire fresh anti-bot cookies, then loads
    the note page. Prefers non-headless mode (Xvfb) to avoid bot detection.
    Returns dict with keys: id, title, thumbnail, image_urls, image_count.
    """
    match = DOUYIN_NOTE_PATTERN.match(url)
    if not match:
        raise ValueError(f"Not a Douyin note URL: {url}")
    note_id = match.group(1)

    # Ensure Xvfb is running for non-headless mode (avoids Douyin bot detection)
    _ensure_xvfb()

    title = ""
    image_urls: list[str] = []
    video_urls: list[str] = []

    async with async_playwright() as p:
        launched = False
        browser = None
        # Prefer non-headless (Xvfb) — headless is easily detected by Douyin
        for use_headless in (False, True):
            try:
                browser = await p.chromium.launch(
                    headless=use_headless,
                    args=[
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
                launched = True
                break
            except Exception:
                continue
        if not launched or browser is None:
            raise ValueError(
                "无法启动浏览器，请确保已安装 Playwright Chromium: "
                "运行: playwright install chromium"
            )
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

            page = await context.new_page()
            stealth = Stealth()
            await stealth.apply_stealth_async(page)

            # Intercept API responses for note data
            api_data: list[dict] = []

            async def handle_response(response) -> None:
                if response.status != 200:
                    return
                req_url = response.url
                if "aweme" not in req_url:
                    return
                ct = response.headers.get("content-type", "")
                if "json" not in ct:
                    return
                try:
                    data = await response.json()
                    if isinstance(data, dict) and "aweme_list" in data:
                        api_data.append(data)
                except Exception:
                    pass

            page.on("response", handle_response)

            # Visit homepage first to get fresh anti-bot cookies (__ac_signature, ttwid)
            try:
                await page.goto(
                    "https://www.douyin.com/",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await asyncio.sleep(5)
            except Exception as e:
                logger.warning("Homepage visit failed: %s", e)

            # Now visit the note page
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                logger.warning("Note page goto failed: %s", e)

            await asyncio.sleep(8)

            # Scroll to trigger lazy loading
            try:
                for _ in range(3):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await asyncio.sleep(1)
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning("Scroll failed: %s", e)

            # Primary: extract from API responses (most reliable, includes video)
            if api_data:
                title, image_urls, video_urls = _extract_media_from_api(api_data, note_id)
                logger.info(
                    "API extraction: title=%r, images=%d, videos=%d",
                    title,
                    len(image_urls),
                    len(video_urls),
                )

            # Fallback: extract from DOM
            if not image_urls and not video_urls:
                title_dom, image_urls, video_urls = await _extract_images_from_dom(page)
                logger.info(
                    "DOM extraction: title=%r, images=%d, videos=%d",
                    title_dom,
                    len(image_urls),
                    len(video_urls),
                )
                if title_dom:
                    title = title_dom

            if not title:
                title = await page.title()
                title = title.replace(" - 抖音", "").strip()

            if not image_urls and not video_urls:
                logger.warning("No media found. Page URL: %s", page.url)
                debug_dir = Path("downloads") / "debug"
                debug_dir.mkdir(parents=True, exist_ok=True)
                try:
                    await page.screenshot(path=str(debug_dir / f"note_{note_id}.png"))
                    logger.info("Debug screenshot saved to %s", debug_dir / f"note_{note_id}.png")
                except Exception:
                    pass

        finally:
            await browser.close()

    if not image_urls and not video_urls:
        raise ValueError("未能提取到媒体链接，页面可能需要登录或已被限制")

    # Determine thumbnail
    thumbnail = ""
    if image_urls:
        thumbnail = image_urls[0]
    elif video_urls:
        thumbnail = video_urls[0]

    return {
        "id": note_id,
        "title": title or f"Douyin Note {note_id}",
        "thumbnail": thumbnail,
        "image_urls": image_urls,
        "image_count": len(image_urls),
        "video_urls": video_urls,
        "video_count": len(video_urls),
    }


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

    # Extract media URLs via Playwright
    info = await extract_note_images(url, cookie_file)
    image_urls = info["image_urls"]
    video_urls = info.get("video_urls", [])
    title = info["title"]

    # Build download list: images first, then videos
    media_list: list[tuple[str, str]] = []  # (url, type)
    for u in image_urls:
        media_list.append((u, "image"))
    for u in video_urls:
        media_list.append((u, "video"))
    total = len(media_list)

    if total == 0:
        raise ValueError("未找到可下载的媒体文件")

    # Sanitize title for directory name
    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:80]
    output_dir = DOWNLOADS_DIR / "douyin" / f"note_{note_id}_{safe_title}"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download all media files
    downloaded = 0
    img_count = 0
    vid_count = 0
    total_bytes = 0
    start_time = asyncio.get_event_loop().time()

    async with httpx.AsyncClient(
        timeout=60,
        follow_redirects=True,
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
                async with client.stream("GET", media_url) as response:
                    response.raise_for_status()
                    with open(filepath, "wb") as f:
                        async for chunk in response.aiter_bytes(8192):
                            if cancel_event and cancel_event.is_set():
                                f.close()
                                filepath.unlink(missing_ok=True)
                                raise DownloadCancelledError("用户取消下载")
                            f.write(chunk)
                            total_bytes += len(chunk)

                downloaded += 1

                # Calculate progress
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

                logger.info("Downloaded %s %d/%d: %s", media_type, downloaded, total, filename)

                if progress_callback:
                    try:
                        progress_callback(percent, speed_str, eta_str)
                    except Exception:
                        pass

            except DownloadCancelledError:
                raise
            except Exception as e:
                logger.warning("Failed to download %s %d: %s", media_type, i + 1, e)

    if downloaded == 0:
        raise ValueError("所有文件下载失败")

    # Save metadata file
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
