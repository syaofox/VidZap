"""Zhihu answer / pin / article image extraction and download.

支持三种 URL 格式：
  - 回答: question/{qid}/answer/{aid}
  - 想法: pin/{id}
  - 专栏文章: zhuanlan.zhihu.com/p/{id}

只做图片提取与下载。视频无法通过静态 HTML 可靠提取
（yt-dlp 只支持 zvideo/{id} 格式），因此放弃视频支持。

注意：知乎页面（含 zhuanlan）受 WAF 保护，无 Cookie 时返回 403，
需要用户先配置 zhihu.com 的 Cookie。
"""

import asyncio
import json
import logging
import re
import time
from http.cookiejar import Cookie as JarCookie
from pathlib import Path
from urllib.parse import urlparse

import httpx

from core.browser_extractor import _CancelledError, _download_media
from core.ytdlp_handler import (
    DOWNLOADS_DIR,
    DownloadCancelledError,
    _format_speed,
    update_download_status,
)

logger = logging.getLogger(__name__)


class ZhihuAccessError(Exception):
    """知乎页面访问失败（WAF 403 等），向上层传达 cookie 未配置/已失效信息。"""


ZHIHU_ANSWER_PATTERN = re.compile(r"https?://(?:www\.)?zhihu\.com/question/\d+/answer/\d+")

ZHIHU_PIN_PATTERN = re.compile(r"https?://(?:www\.)?zhihu\.com/pin/\d+")

ZHIHU_ARTICLE_PATTERN = re.compile(r"https?://(?:www\.)?zhuanlan\.zhihu\.com/p/\d+")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_ZHIHU_REFERER = "https://www.zhihu.com/"

_ZHIHU_HEADERS = {
    "User-Agent": _USER_AGENT,
    "Referer": _ZHIHU_REFERER,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

_ZHIHU_IMG_CDN = re.compile(r"https?://[a-zA-Z0-9.-]*?zhimg\.com/[^\s\"'<>]+")

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


def is_zhihu_article_url(url: str) -> bool:
    return bool(ZHIHU_ARTICLE_PATTERN.match(url))


def is_zhihu_image_url(url: str) -> bool:
    """Check if URL is a Zhihu answer, pin or article (all contain extractable images)."""
    return is_zhihu_answer_url(url) or is_zhihu_pin_url(url) or is_zhihu_article_url(url)


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


def _fallback_domain(url: str) -> str:
    """从 URL 提取 cookie 回写用的兜底域名（初始 cookie 无 domain 属性时使用）。"""
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return "." + host if host else ".zhihu.com"


def _cookies_to_netscape(cookies: httpx.Cookies, fallback_domain: str) -> str:
    """把 httpx cookie jar（含服务端 Set-Cookie 刷新）序列化为 Netscape 格式。

    跳过已过期与空值 cookie；会话级 cookie（无 expires）写 0。
    同名 cookie 可能并存两条（初始注入 + Set-Cookie 刷新，domain key 不同），
    优先保留服务端下发的条目（domain 带点前缀）。
    """
    now = time.time()
    best: dict[str, JarCookie] = {}
    for cookie in cookies.jar:
        if not cookie.name or cookie.value in (None, ""):
            continue
        if cookie.expires is not None and cookie.expires < now:
            continue
        prev = best.get(cookie.name)
        if prev is None:
            best[cookie.name] = cookie
        elif cookie.domain_initial_dot and not prev.domain_initial_dot:
            best[cookie.name] = cookie

    lines = ["# Netscape HTTP Cookie File"]
    for cookie in best.values():
        domain = cookie.domain or fallback_domain
        if domain and not domain.startswith("."):
            domain = "." + domain
        path = cookie.path or "/"
        secure = "TRUE" if cookie.secure else "FALSE"
        expiry = str(int(cookie.expires)) if cookie.expires else "0"
        lines.append(
            "\t".join([domain, "TRUE", path, secure, expiry, cookie.name, cookie.value or ""])
        )
    return "\n".join(lines) + "\n"


def _persist_cookie_updates(
    client: httpx.AsyncClient, cookie_file: str | None, fallback_domain: str
) -> None:
    """把 httpx cookie jar（含服务端 Set-Cookie 刷新）写回 Netscape cookie 文件。

    知乎会周期性通过 Set-Cookie 刷新部分 cookie（如 _xsrf / __zse_ck），
    回写可延长静态导出 cookie 的有效期。失败只记 warning，不影响请求流程。
    """
    if not cookie_file:
        return
    try:
        Path(cookie_file).write_text(_cookies_to_netscape(client.cookies, fallback_domain))
    except OSError as e:
        logger.warning("Cookie 回写失败 (%s): %s", cookie_file, e)


# =============================================================================
# Playwright 自动刷新（延长知乎 Cookie 有效期）
# =============================================================================

_BROWSER_REFRESH_TIMEOUT = 20  # 等待 __zse_ck 生成的最长时间（秒）


def _playwright_cookies_to_netscape(cookies: list[dict], fallback_domain: str) -> str:
    """把 Playwright context.cookies() 列表序列化为 Netscape 格式。

    跳过空值 cookie；session 级 cookie（expires == -1）写 0。
    domain 缺失时用 fallback_domain 兜底并补 "." 前缀。
    """
    lines = ["# Netscape HTTP Cookie File"]
    now = time.time()
    for c in cookies:
        name = c.get("name") or ""
        value = c.get("value") or ""
        if not name or value in (None, ""):
            continue
        expires = c.get("expires", -1)
        # Playwright 用 -1 表示会话级；httpx 用 None/0
        if isinstance(expires, (int, float)) and expires != -1 and expires < now:
            continue
        domain = c.get("domain") or fallback_domain
        if domain and not domain.startswith("."):
            domain = "." + domain
        path = c.get("path") or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        if expires is None or expires == -1:
            expiry = "0"
        else:
            try:
                expiry = str(int(expires))
            except (ValueError, TypeError):
                expiry = "0"
        lines.append("\t".join([domain, "TRUE", path, secure, expiry, name, value]))
    return "\n".join(lines) + "\n"


async def refresh_zhihu_cookie(cookie_file: str | None, timeout: float = 20) -> bool:
    """用 Playwright 刷新知乎 Cookie 的 ``__zse_ck`` 等 JS 生成项。

    流程：注入旧 Netscape cookie → 访问 ``/hot`` 触发知乎 JS 生成
    ``__zse_ck`` → 轮询 ``context.cookies()`` 至出现 ``__zse_ck`` →
    回写 Netscape 文件。

    仅在检测到 ``d_c0`` 等关键 cookie 存在时才尝试；失败返回 False，
    调用方决定是否重试请求。``verify_cookie`` 路径不应自动调用此函数，
    由设置页“刷新”按钮显式触发。

    Returns:
        True 表示刷新成功（文件已更新且含 __zse_ck），否则 False。
    """
    if not cookie_file:
        return False
    existing = _parse_cookies(cookie_file)
    if not existing:
        return False
    # 关键 cookie 缺失时无法刷新
    if "d_c0" not in existing:
        logger.warning("刷新知乎 Cookie 跳过：缺少 d_c0 (%s)", cookie_file)
        return False

    try:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth
    except ImportError as e:
        logger.warning("Playwright 未安装，无法刷新知乎 Cookie: %s", e)
        return False

    try:
        from core.browser_extractor import _ensure_xvfb, _parse_netscape_cookies
    except ImportError as e:
        logger.warning("browser_extractor 导入失败: %s", e)
        return False

    _ensure_xvfb()

    # 将 Netscape 文件转为 Playwright 可注入格式
    pw_cookies = _parse_netscape_cookies(cookie_file)
    if not pw_cookies:
        return False

    # 知乎域名兼容：.zhihu.com 与 www.zhihu.com
    fallback_domain = _fallback_domain("https://www.zhihu.com/")

    try:
        async with async_playwright() as pw:
            browser = None
            launched = False
            for use_headless in (False, True):
                try:
                    browser = await pw.chromium.launch(
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
                logger.warning("刷新知乎 Cookie：浏览器启动失败")
                return False
            try:
                context = await browser.new_context(
                    user_agent=_USER_AGENT,
                    viewport={"width": 1280, "height": 720},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                if pw_cookies:
                    try:
                        await context.add_cookies(pw_cookies)  # type: ignore[arg-type]
                    except Exception as e:
                        logger.warning("注入知乎 Cookie 失败: %s", e)

                page = await context.new_page()
                try:
                    stealth = Stealth()
                    await stealth.apply_stealth_async(page)
                except Exception:
                    pass

                # 触发 JS 生成 __zse_ck（与 zhihu-fisher 方案一致：访问 /hot）
                for target in (
                    "https://www.zhihu.com/hot",
                    "https://www.zhihu.com/",
                ):
                    try:
                        await page.goto(
                            target,
                            wait_until="domcontentloaded",
                            timeout=15000,
                        )
                        break
                    except Exception as e:
                        logger.warning("刷新知乎 Cookie 时访问 %s 失败: %s", target, e)

                # 轮询等待 __zse_ck 生成
                deadline = time.time() + timeout
                found = False
                while time.time() < deadline:
                    try:
                        all_cookies = await context.cookies()
                    except Exception:
                        all_cookies = []
                    names = {c.get("name") for c in all_cookies}
                    if "__zse_ck" in names and "d_c0" in names:
                        found = True
                        # 回写文件（保留全部 domain 的 cookie，至少含 zhihu）
                        # 过滤出 zhihu 相关，失败则全量回写
                        zhihu_cookies = [
                            c for c in all_cookies if "zhihu" in (c.get("domain") or "")
                        ]
                        to_write = zhihu_cookies if zhihu_cookies else all_cookies
                        if to_write:
                            Path(cookie_file).write_text(
                                _playwright_cookies_to_netscape(
                                    to_write,  # type: ignore[arg-type]
                                    fallback_domain,
                                )
                            )
                        break
                    await asyncio.sleep(1)

                if not found:
                    logger.warning("刷新知乎 Cookie 超时：未生成 __zse_ck")
                    return False
                return True
            finally:
                await browser.close()
    except Exception as e:
        logger.warning("刷新知乎 Cookie 异常: %s", e)
        return False


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


async def _fetch_answer_page(url: str, cookie_file: str | None = None) -> str:
    """Fetch Zhihu answer page HTML.

    403 时若已配置 cookie，会尝试用 Playwright 自动刷新 ``__zse_ck``
    并重试一次（仅一次），仍失败再抛 ``ZhihuAccessError``。

    Raises:
        ZhihuAccessError: WAF 返回 403 时抛出，消息区分未配置与已失效两种场景。
        httpx.HTTPStatusError: 其他非 2xx 状态码。
    """
    httpx_cookies = _parse_cookies(cookie_file) if cookie_file else {}
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=True,
        cookies=httpx_cookies or None,
        headers=dict(_ZHIHU_HEADERS),
    ) as client:
        resp = await client.get(url)
        if resp.status_code == 403 and cookie_file:
            # 按需自动刷新（最多一次），失败不影响原有错误语义
            try:
                refreshed = await refresh_zhihu_cookie(cookie_file)
            except Exception as e:
                logger.warning("自动刷新知乎 Cookie 异常: %s", e)
                refreshed = False
            if refreshed:
                httpx_cookies = _parse_cookies(cookie_file)
                async with httpx.AsyncClient(
                    timeout=30,
                    follow_redirects=True,
                    cookies=httpx_cookies or None,
                    headers=dict(_ZHIHU_HEADERS),
                ) as retry_client:
                    retry_resp = await retry_client.get(url)
                    if retry_resp.status_code != 403:
                        if retry_resp.status_code == 200:
                            _persist_cookie_updates(
                                retry_client,
                                cookie_file,
                                _fallback_domain(url),
                            )
                        retry_resp.raise_for_status()
                        return retry_resp.text
                    # 刷新后仍 403
                    raise ZhihuAccessError(
                        "知乎页面访问失败（403）：Cookie 已失效，自动刷新后仍无效，"
                        "请重新从浏览器导出并在 Cookie 设置中更新"
                    )
            # 未刷新或刷新失败
            raise ZhihuAccessError(
                "知乎页面访问失败（403）：Cookie 已失效，请重新从浏览器导出并在 Cookie 设置中更新"
            )
        if resp.status_code == 403:
            raise ZhihuAccessError(
                "知乎页面访问失败（403）：未配置知乎 Cookie，请先在 Cookie 设置中配置"
            )
        resp.raise_for_status()
        _persist_cookie_updates(client, cookie_file, _fallback_domain(url))
        return resp.text


async def verify_cookie(cookie_file: str | None) -> bool:
    """验证知乎 Cookie 是否有效。

    请求知乎首页，能通过 WAF（返回 200）即视为有效；403 / 网络异常 /
    未传 cookie 文件 / 文件无可解析 cookie 时返回 False。

    注意：验证路径不自动刷新，需用户在设置页手动点击“刷新”。
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
            headers=dict(_ZHIHU_HEADERS),
        ) as client:
            resp = await client.get("https://www.zhihu.com/")
            if resp.status_code == 200:
                _persist_cookie_updates(
                    client, cookie_file, _fallback_domain("https://www.zhihu.com/")
                )
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _extract_title(html: str, url: str) -> str:
    # og:title 可能带额外属性（如 data-rh），需兼容任意属性顺序
    m = re.search(
        r'<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']+)["\']',
        html,
    )
    if m:
        return m.group(1)
    # 备选：content 在前，property 在后
    m = re.search(
        r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:title["\']',
        html,
    )
    if m:
        return m.group(1)
    m = re.search(r"<title[^>]*>([^<]+)</title>", html)
    if m:
        title = m.group(1).strip()
        return re.sub(r"\s*-\s*知乎\s*$", "", title)
    aid_m = re.search(r"/answer/(\d+)", url)
    if aid_m:
        return f"知乎回答 {aid_m.group(1)}"
    pin_m = re.search(r"/pin/(\d+)", url)
    if pin_m:
        return f"知乎想法 {pin_m.group(1)}"
    article_m = re.search(r"/p/(\d+)", url)
    if article_m:
        return f"知乎专栏 {article_m.group(1)}"
    return "知乎内容"


async def extract_zhihu_answer(url: str, cookie_file: str | None = None) -> dict:
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


_ZHIHU_ID_PATTERN = re.compile(r"/(?:answer|pin|p)/(\d+)")


def _extract_zhihu_id(url: str) -> str:
    """Extract the Zhihu content ID from an answer / pin / article URL."""
    m = _ZHIHU_ID_PATTERN.search(url)
    return m.group(1) if m else "unknown"


def _zhihu_kind(url: str) -> str:
    """Return the URL kind prefix used for output directories: answer / pin / article."""
    if is_zhihu_pin_url(url):
        return "pin"
    if is_zhihu_article_url(url):
        return "article"
    return "answer"


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
    kind = _zhihu_kind(url)
    content_id = _extract_zhihu_id(url)
    output_dir = DOWNLOADS_DIR / "zhihu" / f"{kind}_{content_id}_{safe_title}"
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
        headers=dict(_ZHIHU_HEADERS),
    ) as client:
        for i, img_url in enumerate(image_urls):
            if cancel_event and cancel_event.is_set():
                raise DownloadCancelledError("用户取消下载")

            ext = _guess_extension(img_url)
            img_count = i + 1
            filename = f"img_{img_count:03d}{ext}"
            filepath = output_dir / filename

            try:
                await _download_media(img_url, filepath, "image", client, cancel_event)
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

                logger.info("Downloaded image %d/%d: %s", downloaded, total, filename)

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
                logger.warning("Failed to download image %d: %s", img_count, e)

    if downloaded == 0:
        raise ValueError("所有图片下载失败")

    meta_path = output_dir / "info.txt"
    meta_path.write_text(f"Title: {title}\nURL: {url}\nDownloaded: {downloaded}/{total}\n")

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
