"""Browser abstraction for Douyin note extraction.

Defines NoteExtractor ABC that encapsulates browser operations.
Currently only PlaywrightNoteExtractor is implemented.
CloakBrowserNoteExtractor is reserved for future use.
"""

import asyncio
import logging
import os
import re
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DOUYIN_NOTE_PATTERN = re.compile(r"https?://(?:www\.)?douyin\.com/note/(\d+)")

_XVFB_DISPLAY = ":99"
_XVFB_STARTED = False


def _ensure_xvfb() -> str:
    """Start Xvfb if needed and return the DISPLAY value to use."""
    global _XVFB_STARTED
    if _XVFB_STARTED:
        return _XVFB_DISPLAY
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


def is_douyin_note_url(url: str) -> bool:
    """Check if the URL is a Douyin note (image slideshow) URL."""
    return bool(DOUYIN_NOTE_PATTERN.match(url))


def _parse_netscape_cookies(cookie_file: str) -> list[dict[str, str | bool]]:
    """Parse Netscape cookie file into browser-compatible cookie dicts."""
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


class NoteExtractor(ABC):
    """Abstract base for Douyin note media extraction."""

    @abstractmethod
    async def extract(self, url: str, cookie_file: str | None = None) -> dict:
        """Extract note images/videos from a Douyin note URL.

        Returns dict with keys: id, title, thumbnail, image_urls, image_count,
        video_urls, video_count.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release browser resources."""
        ...


class PlaywrightNoteExtractor(NoteExtractor):
    """Playwright-based Douyin note extractor.

    Uses Xvfb (non-headless) to avoid Douyin bot detection.
    """

    def __init__(self) -> None:
        self._browser = None
        self._playwright = None

    async def extract(self, url: str, cookie_file: str | None = None) -> dict:
        from playwright.async_api import async_playwright
        from playwright_stealth import Stealth

        match = DOUYIN_NOTE_PATTERN.match(url)
        if not match:
            raise ValueError(f"Not a Douyin note URL: {url}")
        note_id = match.group(1)

        _ensure_xvfb()

        title = ""
        image_urls: list[str] = []
        video_urls: list[str] = []

        async with async_playwright() as pw:
            launched = False
            browser = None
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
                if cookie_file:
                    parsed = _parse_netscape_cookies(cookie_file)
                    if parsed:
                        await context.add_cookies(parsed)  # type: ignore[arg-type]

                page = await context.new_page()
                stealth = Stealth()
                await stealth.apply_stealth_async(page)

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

                try:
                    await page.goto(
                        "https://www.douyin.com/",
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )
                except Exception as e:
                    logger.warning("Homepage visit failed: %s", e)

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                except Exception as e:
                    logger.warning("Note page goto failed: %s", e)

                try:
                    for _ in range(12):
                        if api_data:
                            break
                        has_images = await page.evaluate(
                            """() => document.querySelector(
                                'img[src*="tplv-dy-aweme-images"]'
                            ) !== null"""
                        )
                        if has_images:
                            break
                        await asyncio.sleep(1)
                except Exception:
                    await asyncio.sleep(1)

                try:
                    for _ in range(2):
                        await page.evaluate("window.scrollBy(0, window.innerHeight)")
                        await asyncio.sleep(0.5)
                    await page.evaluate("window.scrollTo(0, 0)")
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.warning("Scroll failed: %s", e)

                if api_data:
                    title, image_urls, video_urls = _extract_media_from_api(api_data, note_id)
                    logger.info(
                        "API extraction: title=%r, images=%d, videos=%d",
                        title,
                        len(image_urls),
                        len(video_urls),
                    )

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
                        logger.info(
                            "Debug screenshot saved to %s",
                            debug_dir / f"note_{note_id}.png",
                        )
                    except Exception:
                        pass

            finally:
                await browser.close()

        if not image_urls and not video_urls:
            raise ValueError("未能提取到媒体链接，页面可能需要登录或已被限制")

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

    async def close(self) -> None:
        pass  # Playwright context manager handles cleanup


class CloakBrowserNoteExtractor(NoteExtractor):
    """CloakBrowser-based Douyin note extractor (placeholder).

    Drop-in replacement for PlaywrightNoteExtractor.
    Uses CloakBrowser's source-level stealth patches for better anti-bot.
    TODO: implement when cloakbrowser is adopted.
    """

    def __init__(self) -> None:
        self._browser = None

    async def extract(self, url: str, cookie_file: str | None = None) -> dict:
        raise NotImplementedError(
            "CloakBrowser extractor not yet implemented. "
            "Use PlaywrightNoteExtractor or set VIDZAP_BROWSER=playwright."
        )

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()


# Factory / module-level active extractor
_active_extractor: NoteExtractor | None = None


def _get_extractor() -> NoteExtractor:
    """Return the active extractor based on environment config."""
    global _active_extractor
    if _active_extractor is not None:
        return _active_extractor
    engine = os.environ.get("VIDZAP_BROWSER", "playwright").lower()
    if engine == "cloakbrowser":
        _active_extractor = CloakBrowserNoteExtractor()
    else:
        _active_extractor = PlaywrightNoteExtractor()
    return _active_extractor


# =============================================================================
# Pure functions (no browser needed)
# =============================================================================


def _extract_media_from_api(api_data: list[dict], note_id: str) -> tuple[str, list[str], list[str]]:
    """Extract title, image_urls, and video_urls from intercepted API responses."""
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

            image_urls: list[str] = []
            for img in item.get("images", []) or []:
                if not isinstance(img, dict):
                    continue
                url_list = img.get("url_list", [])
                if url_list and url_list[0].startswith("http"):
                    image_urls.append(url_list[0])

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

        title = document.title.replace(/ - 抖音$/, '').trim();

        function isNoteImg(src) {
            return src && src.includes('tplv-dy-aweme-images');
        }

        function isNoteVideo(src) {
            return src && (src.includes('douyinvod') || src.includes('douyinstatic'))
                   && !src.includes('.js') && !src.includes('.css');
        }

        const imgs = document.querySelectorAll('img');
        for (const img of imgs) {
            if (isNoteImg(img.src) && (img.naturalWidth >= 200 || img.naturalHeight >= 200)) {
                imgUrls.add(img.src);
            }
        }

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

        const lazyImgs = document.querySelectorAll('img[data-src]');
        for (const img of lazyImgs) {
            const src = img.getAttribute('data-src');
            if (isNoteImg(src)) {
                imgUrls.add(src);
            }
        }

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

    seen = set()
    unique_imgs: list[str] = []
    for u in result.get("imgUrls", []):
        base = u.split("?")[0]
        if base not in seen:
            seen.add(base)
            unique_imgs.append(u)

    seen_v = set()
    unique_vids: list[str] = []
    for u in result.get("vidUrls", []):
        base = u.split("?")[0]
        if base not in seen_v:
            seen_v.add(base)
            unique_vids.append(u)

    return result.get("title", ""), unique_imgs, unique_vids


# =============================================================================
# Downloads (no browser needed — uses httpx directly)
# =============================================================================


async def _download_media(
    media_url: str,
    filepath: Path,
    media_type: str,
    client: httpx.AsyncClient,
    cancel_event: asyncio.Event | None = None,
) -> None:
    """Download a single media file."""
    async with client.stream("GET", media_url) as response:
        response.raise_for_status()
        with open(filepath, "wb") as f:
            async for chunk in response.aiter_bytes(8192):
                if cancel_event and cancel_event.is_set():
                    f.close()
                    filepath.unlink(missing_ok=True)
                    raise _CancelledError
                f.write(chunk)


class _CancelledError(Exception):
    pass
