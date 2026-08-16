"""Douban personage photo extraction and download.

支持豆瓣人物照片列表页：
    https://www.douban.com/personage/{pid}/photos/

流程：
  1. 抓取列表页（含分页，每页 30 张），提取缩略图 URL 与照片 ID
  2. 缩略图 URL 直接变换为原图 (xl) URL：/view/photo/photo/ → /view/photo/xl/
     （等价于单页"查看大图"按钮的目标地址，实测无需签名参数、无需 Cookie）
  3. 若某张 xl 下载失败（4xx），抓取对应照片单页解析"查看大图"链接重试一次

注意：
  - 列表页/单页需要 douban.com Cookie：无 Cookie 时 302 跳转
    sec.douban.com 安全校验页，视为未配置/已失效。
  - 图片 CDN（img*.doubanio.com）只接受 douban.com / doubanio.com 来源的
    Referer，其余一律 418：httpx 下载必须携带 Referer: https://www.douban.com/；
    浏览器预览无法伪造豆瓣 Referer，一律走应用内代理 /douban-image?url=...
    （main.py，服务端带 Referer 代抓）。
"""
import asyncio
import logging
import re
from html import unescape as html_unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from core.browser_extractor import _CancelledError, _download_media
from core.ytdlp_handler import (
    DOWNLOADS_DIR,
    DownloadCancelledError,
    _format_speed,
    update_download_status,
)

logger = logging.getLogger(__name__)

# 人物照片列表页（尾斜杠可选，允许带 query 参数）
DOUBAN_PHOTO_LIST_PATTERN = re.compile(
    r"https?://(?:www\.)?douban\.com/personage/(\d+)/photos(?:/|\?|$)"
)

# 人物主页（不含 /photos 或 /photo/{id} 等子路径；query 可选）
DOUBAN_PERSONAGE_PATTERN = re.compile(
    r"https?://(?:www\.)?douban\.com/personage/(\d+)/?(?:\?.*)?$"
)

# 每页照片数（豆瓣固定 30）
_PAGE_SIZE = 30
# 防御性上限：最多翻 100 页（3000 张），防止解析异常导致死循环
_MAX_PAGES = 100

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_DOUBAN_REFERER = "https://www.douban.com/"

# 无 Cookie / Cookie 失效时豆瓣 302 到安全校验域名
_SEC_DOMAIN = "sec.douban.com"

# 反爬风控页（机器人检测）: https://www.douban.com/misc/sorry?...（含腾讯云验证码）
_SORRY_MARKER = "misc/sorry"
_CAPTCHA_MARKER = "turing.captcha"

# 请求间隔（秒）：防触发豆瓣反爬风控（高频连续请求易被 /misc/sorry 拦截）
_REQUEST_DELAY = 0.3

# 列表页缩略图条目：<li data-id="..."><a href="/personage/{pid}/photo/{photoid}"><img src="...">
_LI_ITEM_PATTERN = re.compile(
    r'<li data-id="(\d+)"[^>]*>.*?'
    r'<a href="(/personage/\d+/photo/\d+)"[^>]*>\s*'
    r'<img src="(https?://[^"]+)"',
    re.DOTALL,
)

# 分页器总张数：(共67张)
_TOTAL_COUNT_PATTERN = re.compile(r"\(共(\d+)张\)")

# 单页"查看大图"链接（xl 原图，带签名参数）
_PHOTO_ZOOM_PATTERN = re.compile(
    r'<a class="photo-zoom"[^>]*href="([^"]+)"'
)


class DoubanAccessError(Exception):
    """豆瓣页面访问失败（无 Cookie / Cookie 失效 / 被安全校验拦截）。"""


def is_douban_photo_url(url: str) -> bool:
    """Check if URL is a Douban personage photo list page or personage home page."""
    return bool(
        DOUBAN_PHOTO_LIST_PATTERN.match(url)
        or DOUBAN_PERSONAGE_PATTERN.match(url)
    )


def _extract_person_id(url: str) -> str | None:
    """Extract the personage ID from a Douban personage URL."""
    m = DOUBAN_PHOTO_LIST_PATTERN.match(url) or DOUBAN_PERSONAGE_PATTERN.match(url)
    return m.group(1) if m else None


def _parse_cookies(cookie_file: str | None) -> dict[str, str]:
    """Parse a Netscape cookie file into a {name: value} dict."""
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


async def _fetch_page(url: str, cookie_file: str | None = None) -> str:
    """Fetch a Douban page HTML with cookie.

    Raises:
        DoubanAccessError: 无 Cookie / Cookie 失效（302 跳转 sec.douban.com）
            或直接 403 时抛出，消息区分未配置与已失效两种场景。
        httpx.HTTPStatusError: 其他非 2xx 状态码。
    """
    httpx_cookies = _parse_cookies(cookie_file) if cookie_file else {}
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        cookies=httpx_cookies or None,
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": _DOUBAN_REFERER,
        },
    ) as client:
        resp = await client.get(url)
        resp_url = str(resp.url)
        # 反爬风控页（机器人检测，HTTP 200 但跳转 /misc/sorry 或含腾讯云验证码）
        if _SORRY_MARKER in resp_url or _CAPTCHA_MARKER in resp.text[:2000]:
            raise DoubanAccessError(
                "豆瓣触发反爬风控（机器人检测）：请稍后再试，或降低下载频率"
            )
        redirected_to_sec = _SEC_DOMAIN in resp_url
        if resp.status_code in (403, 418) or redirected_to_sec:
            if cookie_file:
                raise DoubanAccessError(
                    "豆瓣页面访问失败：Cookie 已失效，"
                    "请重新从浏览器导出并在 Cookie 设置中更新"
                )
            raise DoubanAccessError(
                "豆瓣页面访问失败：未配置豆瓣 Cookie，请先在 Cookie 设置中配置"
            )
        resp.raise_for_status()
        return resp.text


async def verify_cookie(cookie_file: str | None) -> bool:
    """验证豆瓣 Cookie 是否有效。

    请求 https://www.douban.com/mine/（我的豆瓣，需登录）：返回 200
    且未跳转 sec.douban.com 即视为有效；403 / 网络异常 / 未传 cookie
    文件 / 文件无可解析 cookie 时返回 False。
    """
    if not cookie_file:
        return False
    httpx_cookies = _parse_cookies(cookie_file)
    if not httpx_cookies:
        return False
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            cookies=httpx_cookies,
            headers={
                "User-Agent": _USER_AGENT,
                "Referer": _DOUBAN_REFERER,
            },
        ) as client:
            resp = await client.get("https://www.douban.com/mine/")
            resp_url = str(resp.url)
            return (
                resp.status_code == 200
                and _SEC_DOMAIN not in resp_url
                and _SORRY_MARKER not in resp_url
            )
    except httpx.HTTPError:
        return False


# =============================================================================
# 列表页解析
# =============================================================================


def _parse_photo_items(html: str) -> list[dict]:
    """Extract (photo_id, detail_url, thumbnail) tuples from a photo list page."""
    items: list[dict] = []
    for m in _LI_ITEM_PATTERN.finditer(html):
        photo_id = m.group(1)
        detail_path = m.group(2)
        thumbnail = m.group(3)
        items.append(
            {
                "photo_id": photo_id,
                "detail_url": "https://www.douban.com" + detail_path,
                "thumbnail": thumbnail,
            }
        )
    return items


def _parse_total_count(html: str) -> int | None:
    """Extract the total photo count from the paginator, e.g. (共67张)."""
    m = _TOTAL_COUNT_PATTERN.search(html)
    return int(m.group(1)) if m else None


def _extract_title(html: str, person_id: str) -> str:
    m = re.search(r"<h1>([^<]+)</h1>", html)
    if m:
        return m.group(1).strip()
    m = re.search(r"<title>([^<]+)</title>", html)
    if m:
        return m.group(1).strip()
    return f"豆瓣人物图片 {person_id}"


def _to_original_url(thumbnail_url: str) -> str:
    """Convert a photo-list thumbnail URL to the original (xl) image URL.

    缩略图: https://img9.doubanio.com/view/photo/photo/public/p{id}.jpg
    原图:   https://img9.doubanio.com/view/photo/xl/public/p{id}.jpg
    （等价于单页"查看大图"链接，无需签名参数）
    """
    return thumbnail_url.replace("/view/photo/photo/", "/view/photo/xl/")


def _build_page_url(base_url: str, start: int, sortby: str) -> str:
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}start={start}&sortby={sortby}"


# =============================================================================
# 提取
# =============================================================================


async def extract_douban_photos(
    url: str, cookie_file: str | None = None
) -> dict:
    """Extract original-quality image URLs from a Douban personage photo list.

    Follows pagination (30 photos per page) until all photos are collected.

    Returns dict with keys:
        title: str
        thumbnail: str (first thumbnail URL or empty)
        image_urls: list[str]  (original xl image URLs)
        detail_urls: list[str] (per-photo detail page URL, parallel to image_urls)
        thumb_urls: list[str] (thumbnail URLs, parallel to image_urls; 供预览)
        image_count: int
    """
    person_id = _extract_person_id(url)
    if person_id is None:
        raise ValueError(f"Not a Douban personage photo URL: {url}")

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    sortby = query.get("sortby", ["like"])[0]
    # 主页 / 列表页统一走照片列表页提取（含分页）
    base_url = f"https://www.douban.com/personage/{person_id}/photos/"

    seen: set[str] = set()
    image_urls: list[str] = []
    detail_urls: list[str] = []
    thumbnails: list[str] = []
    title = ""
    total: int | None = None
    start = 0

    while True:
        page_url = _build_page_url(base_url, start, sortby)
        html = await _fetch_page(page_url, cookie_file)
        if not title:
            title = _extract_title(html, person_id)
        if total is None:
            total = _parse_total_count(html)

        items = _parse_photo_items(html)
        if not items:
            break

        new_count = 0
        for item in items:
            photo_id = item["photo_id"]
            if photo_id in seen:
                continue
            seen.add(photo_id)
            new_count += 1
            image_urls.append(_to_original_url(item["thumbnail"]))
            detail_urls.append(item["detail_url"])
            thumbnails.append(item["thumbnail"])

        # 页容量 30：少于 30 说明是最后一页；或已收齐总数
        if len(items) < _PAGE_SIZE or (total is not None and len(seen) >= total):
            break
        if new_count == 0:
            break
        start += _PAGE_SIZE
        if start >= _PAGE_SIZE * _MAX_PAGES:
            break
        await asyncio.sleep(_REQUEST_DELAY)

    return {
        "title": title or f"豆瓣人物图片 {person_id}",
        "thumbnail": thumbnails[0] if thumbnails else "",
        "image_urls": image_urls,
        "detail_urls": detail_urls,
        "thumb_urls": thumbnails,
        "image_count": len(image_urls),
    }


def subset_note_info(note_info: dict, indexes: list[int] | set[int]) -> dict:
    """按索引子集裁剪豆瓣 note_info（供分析页勾选部分图片下载）。

    image_urls / detail_urls / thumb_urls 是并行列表，按下标同步裁剪；
    image_count 同步为选中数量；越界索引安全忽略。
    """
    idx_set = set(indexes)
    image_urls = [
        u for i, u in enumerate(note_info["image_urls"]) if i in idx_set
    ]
    return {
        "title": note_info["title"],
        "thumbnail": note_info.get("thumbnail", ""),
        "image_urls": image_urls,
        "detail_urls": [
            u
            for i, u in enumerate(note_info.get("detail_urls") or [])
            if i in idx_set
        ],
        "thumb_urls": [
            u
            for i, u in enumerate(note_info.get("thumb_urls") or [])
            if i in idx_set
        ],
        "image_count": len(image_urls),
    }


async def _resolve_original_from_detail(
    detail_url: str, cookie_file: str | None
) -> str | None:
    """Fetch a photo detail page and parse the '查看大图' (photo-zoom) link.

    Used as a fallback when the transformed xl URL fails to download.
    Returns the original image URL or None on failure.
    """
    try:
        html = await _fetch_page(detail_url, cookie_file)
    except (DoubanAccessError, httpx.HTTPError):
        return None
    m = _PHOTO_ZOOM_PATTERN.search(html)
    if not m:
        return None
    href = html_unescape(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.douban.com" + href
    return href


# =============================================================================
# 下载
# =============================================================================


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


async def download_douban_images(
    url: str,
    cookie_file: str | None = None,
    progress_callback=None,
    cancel_event: asyncio.Event | None = None,
    download_id: int | None = None,
    note_info: dict | None = None,
) -> str:
    """Download all original-quality images from a Douban personage photo list.

    If note_info is provided (pre-extracted), skip HTTP fetching
    and reuse the existing data.

    Returns the path to the output directory.
    """
    if note_info is not None:
        info = note_info
    else:
        info = await extract_douban_photos(url, cookie_file)

    image_urls = info["image_urls"]
    detail_urls = info.get("detail_urls") or []
    title = info["title"]

    if not image_urls:
        raise ValueError("未找到可下载的图片")

    person_id = _extract_person_id(url) or "unknown"

    safe_title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title)[:80]
    output_dir = DOWNLOADS_DIR / "douban" / f"personage_{person_id}_{safe_title}"
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
            "Referer": _DOUBAN_REFERER,
        },
    ) as client:
        for i, img_url in enumerate(image_urls):
            if cancel_event and cancel_event.is_set():
                raise DownloadCancelledError("用户取消下载")

            ext = _guess_extension(img_url)
            img_count = i + 1
            filename = f"img_{img_count:03d}{ext}"
            filepath = output_dir / filename
            detail_url = detail_urls[i] if i < len(detail_urls) else None

            try:
                try:
                    await _download_media(
                        img_url, filepath, "image", client, cancel_event
                    )
                except httpx.HTTPStatusError:
                    # 兜底：xl 变换失败时，走单页"查看大图"链接
                    if detail_url:
                        fallback = await _resolve_original_from_detail(
                            detail_url, cookie_file
                        )
                        if fallback and fallback != img_url:
                            filepath.unlink(missing_ok=True)
                            await _download_media(
                                fallback, filepath, "image", client, cancel_event
                            )
                        else:
                            raise
                    else:
                        raise
                downloaded += 1
                try:
                    total_bytes += filepath.stat().st_size
                except OSError:
                    pass

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

            # 节流：高频连续请求易触发豆瓣反爬风控
            await asyncio.sleep(_REQUEST_DELAY)

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
