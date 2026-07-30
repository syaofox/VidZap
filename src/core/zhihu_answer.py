"""Zhihu answer / pin image extraction and download.

支持两种 URL 格式：
  - 回答: question/{qid}/answer/{aid}
  - 想法: pin/{id}

只做图片提取与下载。视频无法通过静态 HTML 可靠提取
（yt-dlp 只支持 zvideo/{id} 格式），因此放弃视频支持。
"""
import asyncio
import json
import logging
import re
from pathlib import Path

import httpx

from core.browser_extractor import _CancelledError, _download_media
from core.ytdlp_handler import (
    DOWNLOADS_DIR,
    DownloadCancelledError,
    _format_speed,
    update_download_status,
)

logger = logging.getLogger(__name__)

ZHIHU_ANSWER_PATTERN = re.compile(
    r"https?://(?:www\.)?zhihu\.com/question/\d+/answer/\d+"
)

ZHIHU_PIN_PATTERN = re.compile(
    r"https?://(?:www\.)?zhihu\.com/pin/\d+"
)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_ZHIHU_REFERER = "https://www.zhihu.com/"

_ZHIHU_IMG_CDN = re.compile(
    r"https?://[a-zA-Z0-9.-]*?zhimg\.com/[^\s\"'<>]+"
)

# 知乎缩略图尺寸前缀：/80/v2-xxx.jpg → 去掉 /80/
_THUMB_SIZE_REPLACE = re.compile(r"/\d{2,3}/(v2-)")

# data-actual 属性含原图地址
_ATTR_PATTERNS = (
    re.compile(r'data-actual=["\'](https?://[^"\']+)["\']'),
    re.compile(r'data-original=["\'](https?://[^"\']+)["\']'),
    re.compile(r'<img[^>]+src=["\'](https?://[^"\']+)["\']'),
)


def is_zhihu_answer_url(url: str) -> bool:
    return bool(ZHIHU_ANSWER_PATTERN.match(url))


def is_zhihu_pin_url(url: str) -> bool:
    return bool(ZHIHU_PIN_PATTERN.match(url))


def is_zhihu_image_url(url: str) -> bool:
    """Check if URL is a Zhihu answer or pin (both contain extractable images)."""
    return is_zhihu_answer_url(url) or is_zhihu_pin_url(url)


def _parse_cookies(cookie_file: str | None) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if not cookie_file:
        return cookies
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


# =============================================================================
# 图片 URL 提取
# =============================================================================


_V2_HASH = re.compile(r"(v2-[a-zA-Z0-9]+)")


def _image_hash(url: str) -> str | None:
    """Extract stable image identifier from a Zhihu image URL.

    同一张图可能从不同 CDN 子域名或不同扩展名出现，但 v2-{hash} 相同。
    https://picx.zhimg.com/v2-abc.jpg  → v2-abc
    https://pic1.zhimg.com/80/v2-abc.webp → v2-abc
    """
    m = _V2_HASH.search(url)
    return m.group(1) if m else None


def _extract_images_from_html(html: str) -> list[str]:
    """Extract original-quality image URLs from Zhihu answer HTML.

    Combines all extraction strategies and normalizes URLs:
    1. data-actual / data-original 属性（优先，含原图地址）
    2. __INITIAL_STATE__ / __NEXT_DATA_INIT__ JSON
    3. <img src> 属性（兜底）
    """
    seen: set[str] = set()
    result: list[str] = []

    def _add(url: str) -> None:
        normal = _normalize_image_url(url)
        if normal is None:
            return
        key = _image_hash(normal) or normal
        if key not in seen:
            seen.add(key)
            result.append(normal)

    # 1. data-actual / data-original（含原图链接）
    for pat in (_ATTR_PATTERNS[0], _ATTR_PATTERNS[1]):
        for m in pat.finditer(html):
            u = m.group(1)
            if "zhimg.com" in u:
                _add(u)

    # 2. JSON 数据
    json_urls = _collect_json_image_urls(html)
    for u in json_urls:
        if "zhimg.com" in u:
            _add(u)

    # 3. <img src>（兜底，处理 data-actual 未覆盖的 img 元素）
    for m in _ATTR_PATTERNS[2].finditer(html):
        u = m.group(1)
        if "zhimg.com" in u:
            _add(u)

    return result


def _collect_json_image_urls(html: str) -> list[str]:
    """Collect image URLs from __INITIAL_STATE__ / __NEXT_DATA_INIT__ JSON."""
    urls: list[str] = []

    for pattern in (
        re.compile(
            r'<script[^>]+id=["\']js-initialData["\'][^>]*>(.*?)</script>',
            re.DOTALL,
        ),
        re.compile(
            r'<script[^>]+id=["\']__NEXT_DATA_INIT__["\'][^>]*>(.*?)</script>',
            re.DOTALL,
        ),
    ):
        m = pattern.search(html)
        if m:
            try:
                data = json.loads(m.group(1))
                urls.extend(_walk_json_for_images(data))
            except (json.JSONDecodeError, ValueError):
                pass

    # 经典格式
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.*?});", html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            urls.extend(_walk_json_for_images(data))
        except (json.JSONDecodeError, ValueError):
            pass

    return urls


def _walk_json_for_images(data: object) -> list[str]:
    """Recursively walk parsed JSON and collect zhimg.com URLs."""
    found: list[str] = []

    def _walk(obj: object) -> None:
        if isinstance(obj, str):
            for m in _ZHIHU_IMG_CDN.finditer(obj):
                found.append(m.group(0))
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(data)
    return found


def _normalize_image_url(url: str) -> str | None:
    """Convert a Zhihu image URL to the original (largest) version.

    缩略图 URL: https://picx.zhimg.com/80/v2-xxx.jpg
    原图 URL:   https://picx.zhimg.com/v2-xxx.jpg

    去除 /{size}/ 前缀即得原图。
    """
    url = url.split("?")[0].split("#")[0].rstrip("/")
    url = _THUMB_SIZE_REPLACE.sub(r"/\1", url)
    _exts = (".jpg", ".png", ".webp", ".gif", ".heic", ".bmp")
    if any(url.endswith(e) for e in _exts):
        return url
    return _guess_extension_url(url)


def _guess_extension_url(url: str) -> str | None:
    """Return the URL itself if we can guess an extension, otherwise None."""
    url_lower = url.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp"):
        if ext in url_lower:
            return url
    m = re.search(r"\.(jpe?g|png|webp|gif|heic|heif|bmp)(?:[\?/&#]|$)", url_lower)
    if m:
        return url
    return None


async def _fetch_answer_page(
    url: str, cookie_file: str | None = None
) -> str:
    """Fetch Zhihu answer page HTML."""
    httpx_cookies = _parse_cookies(cookie_file) if cookie_file else {}
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        cookies=httpx_cookies or None,
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": _ZHIHU_REFERER,
        },
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _extract_title(html: str, url: str) -> str:
    m = re.search(
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        html,
    )
    if m:
        return m.group(1)
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        title = m.group(1).strip()
        return re.sub(r"\s*-\s*知乎\s*$", "", title)
    m = re.search(r"/answer/(\d+)", url)
    aid = m.group(1) if m else "Unknown"
    return f"知乎回答 {aid}"


async def extract_zhihu_answer(
    url: str, cookie_file: str | None = None
) -> dict:
    """Extract original-quality image URLs and title from a Zhihu answer.

    Returns dict with keys:
        title: str
        thumbnail: str (first image URL or empty)
        image_urls: list[str]  (original-quality image URLs)
        image_count: int
    """
    html = await _fetch_answer_page(url, cookie_file)
    image_urls = _extract_images_from_html(html)
    title = _extract_title(html, url)
    thumbnail = image_urls[0] if image_urls else ""

    return {
        "title": title,
        "thumbnail": thumbnail,
        "image_urls": image_urls,
        "image_count": len(image_urls),
    }


# =============================================================================
# 图片下载
# =============================================================================


async def download_zhihu_images(
    url: str,
    cookie_file: str | None = None,
    progress_callback=None,
    cancel_event: asyncio.Event | None = None,
    download_id: int | None = None,
    note_info: dict | None = None,
) -> str:
    """Download all original-quality images from a Zhihu answer.

    If note_info is provided (pre-extracted), skip HTTP fetching
    and reuse the existing data.

    Returns the path to the output directory.
    """
    if note_info is not None:
        info = note_info
    else:
        info = await extract_zhihu_answer(url, cookie_file)

    image_urls = info["image_urls"]
    title = info["title"]

    if not image_urls:
        raise ValueError("未找到可下载的图片")

    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:80]
    answer_id_match = re.search(r"/answer/(\d+)", url)
    answer_id = answer_id_match.group(1) if answer_id_match else "unknown"
    output_dir = DOWNLOADS_DIR / "zhihu" / f"answer_{answer_id}_{safe_title}"
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(image_urls)
    downloaded = 0
    total_bytes = 0
    start_time = asyncio.get_event_loop().time()
    httpx_cookies = _parse_cookies(cookie_file) if cookie_file else {}

    async with httpx.AsyncClient(
        timeout=60,
        follow_redirects=True,
        cookies=httpx_cookies or None,
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": _ZHIHU_REFERER,
        },
    ) as client:
        for i, img_url in enumerate(image_urls):
            if cancel_event and cancel_event.is_set():
                raise DownloadCancelledError("用户取消下载")

            ext = _guess_extension(img_url)
            img_count = i + 1
            filename = f"img_{img_count:03d}{ext}"
            filepath = output_dir / filename

            try:
                await _download_media(
                    img_url, filepath, "image", client, cancel_event
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
                    "Downloaded image %d/%d: %s", downloaded, total, filename
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
                logger.warning(
                    "Failed to download image %d: %s", img_count, e
                )

    if downloaded == 0:
        raise ValueError("所有图片下载失败")

    meta_path = output_dir / "info.txt"
    meta_path.write_text(
        f"Title: {title}\n"
        f"URL: {url}\n"
        f"Downloaded: {downloaded}/{total}\n"
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


_KNOWN_IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif", ".bmp"}


def _guess_extension(img_url: str) -> str:
    """Guess file extension from image URL."""
    url_lower = img_url.lower()
    if ".jpg" in url_lower or ".jpeg" in url_lower:
        return ".jpg"
    if ".png" in url_lower:
        return ".png"
    if ".webp" in url_lower:
        return ".webp"
    if ".gif" in url_lower:
        return ".gif"
    if ".heic" in url_lower or ".heif" in url_lower:
        return ".heic"
    if ".bmp" in url_lower:
        return ".bmp"
    m = re.search(r"\.(jpe?g|png|webp|gif|heic|heif|bmp)(?:[\?/&#]|$)", url_lower)
    if m:
        return f".{m.group(1)}"
    return ".jpg"
