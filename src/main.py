import asyncio
import logging
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response
from nicegui import app, ui

# 添加 src 到 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.db import init_db
from core.ytdlp_handler import get_download_by_id, get_suggested_formats
from pages import history, home, settings

logger = logging.getLogger(__name__)

# 创建必要的目录
Path("downloads").mkdir(exist_ok=True)

Path(os.environ.get("NICEVID_DATA_DIR", "data")).mkdir(parents=True, exist_ok=True)

# 初始化数据库
init_db()


def _env_bool(name: str, default: bool = False) -> bool:
    """解析布尔环境变量：1/true/yes/on（不区分大小写）视为真，其余为假。"""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@app.get("/downloads-file/{download_id}/{filename:path}")
def serve_download_file(download_id: int, filename: str):
    """按下载记录 ID 提供文件下载/预览"""
    rec = get_download_by_id(download_id)
    if not rec or not rec.get("file_path"):
        return {"error": "记录不存在"}, 404
    file_path = Path(rec["file_path"])
    # If file_path is a directory (e.g. douyin note downloads), resolve the file inside it
    if file_path.is_dir():
        file_path = file_path / filename
    if not file_path.is_file():
        return {"error": "文件不存在"}, 404
    media_type, _ = mimetypes.guess_type(file_path.name)
    return FileResponse(
        str(file_path),
        filename=file_path.name,
        media_type=media_type or "application/octet-stream",
    )


@app.get("/douban-image")
async def douban_image(url: str):
    """代理豆瓣图片（预览用）。

    豆瓣 CDN（img*.doubanio.com）只接受 douban.com / doubanio.com 来源的
    Referer，本地部署的浏览器页面无法伪造，故由服务端带 Referer 代抓后返回。

    仅允许 doubanio.com 域名，防止 SSRF。
    """
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith("doubanio.com"):
        return {"error": "仅允许 doubanio.com 图片 URL"}, 400
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.douban.com/",
            },
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError:
        return {"error": "图片获取失败"}, 502
    if resp.status_code != 200:
        return {"error": "图片获取失败"}, resp.status_code
    return Response(
        content=resp.content,
        media_type=resp.headers.get("content-type", "image/jpeg"),
    )


# =============================================================================
# Android 分享 API
# =============================================================================


async def _do_share(url: str, format_id: str | None = None) -> dict:
    """处理分享 URL。

    两阶段设计：
    - 无 format_id（阶段一）：分析 URL，返回可用格式（视频）/ 直接下载（笔记）
    - 有 format_id（阶段二）：用指定格式入队下载。
    """
    from core.cookie_manager import get_cookie_for_url
    from core.ytdlp_handler import create_download_record
    from core.ytdlp_handler import extract_info as _extract_info
    from pages.home import classify_urls

    # 豆瓣安全校验页链接（浏览器未登录/被风控重定向后地址栏的 URL）无法直接下载
    if "sec.douban.com" in url:
        raise ValueError(
            "检测到豆瓣安全校验页链接（sec.douban.com）："
            "请复制人物主页或照片列表页链接"
        )

    url_type = classify_urls([url])
    if url_type == "mixed":
        raise ValueError("不支持的链接类型")

    cookie = get_cookie_for_url(url)

    if url_type == "douyin_note":
        from core.douyin_note import extract_note_images
        from core.download_queue import download_queue

        note_info = await asyncio.wait_for(
            extract_note_images(url, cookie),
            timeout=60,
        )
        title = note_info.get("title") or url
        thumbnail = note_info.get("thumbnail") or ""
        dl_id = create_download_record(url, title, thumbnail, "images")
        await download_queue.enqueue(
            url=url,
            format_id="images",
            cookie_file=cookie,
            download_id=dl_id,
            task_type="douyin_note",
            note_info=note_info,
        )
        return {"status": "ok", "download_id": dl_id, "title": title, "type": "douyin_note"}

    if url_type == "zhihu_answer":
        from core.download_queue import download_queue
        from core.zhihu_answer import ZhihuAccessError, extract_zhihu_answer

        try:
            zhihu_info = await asyncio.wait_for(
                extract_zhihu_answer(url, cookie),
                timeout=30,
            )
        except ValueError:
            raise
        except ZhihuAccessError as e:
            raise ValueError(str(e)) from e
        except Exception as e:
            raise ValueError(f"知乎页面访问失败，请检查 Cookie 是否有效: {e}") from e

        if zhihu_info.get("image_count", 0) > 0:
            title = zhihu_info.get("title") or url
            thumbnail = zhihu_info.get("thumbnail") or ""
            try:
                dl_id = create_download_record(url, title, thumbnail, "images")
                await download_queue.enqueue(
                    url=url,
                    format_id="images",
                    cookie_file=cookie,
                    download_id=dl_id,
                    task_type="zhihu_image",
                    note_info=zhihu_info,
                )
            except Exception as e:
                raise ValueError(f"创建下载任务失败: {e}") from e
            return {"status": "ok", "download_id": dl_id, "title": title, "type": "zhihu_image"}

        raise ValueError("未能从知乎回答提取到可下载内容")

    if url_type == "douban_photo":
        from core.douban_photo import DoubanAccessError, extract_douban_photos
        from core.download_queue import download_queue

        try:
            douban_info = await asyncio.wait_for(
                extract_douban_photos(url, cookie),
                timeout=60,
            )
        except ValueError:
            raise
        except DoubanAccessError as e:
            raise ValueError(str(e)) from e
        except Exception as e:
            raise ValueError(f"豆瓣页面访问失败，请检查 Cookie 是否有效: {e}") from e

        if douban_info.get("image_count", 0) > 0:
            title = douban_info.get("title") or url
            thumbnail = douban_info.get("thumbnail") or ""
            try:
                dl_id = create_download_record(url, title, thumbnail, "images")
                await download_queue.enqueue(
                    url=url,
                    format_id="images",
                    cookie_file=cookie,
                    download_id=dl_id,
                    task_type="douban_photo",
                    note_info=douban_info,
                )
            except Exception as e:
                raise ValueError(f"创建下载任务失败: {e}") from e
            return {"status": "ok", "download_id": dl_id, "title": title, "type": "douban_photo"}

        raise ValueError("未能从豆瓣人物页面提取到可下载内容")

    # ---- 视频 ----
    if format_id:
        # 阶段二：用指定格式入队下载
        from core.download_queue import download_queue

        title = url
        thumbnail = ""
        try:
            info = await asyncio.wait_for(_extract_info(url, cookie), timeout=30)
            title = info.get("title") or url
            thumbnail = info.get("thumbnail") or ""
        except Exception:
            pass

        dl_id = create_download_record(url, title, thumbnail, format_id)
        await download_queue.enqueue(
            url=url,
            format_id=format_id,
            cookie_file=cookie,
            write_thumbnail=True,
            download_id=dl_id,
        )
        return {"status": "ok", "download_id": dl_id, "title": title, "type": "video"}

    # 阶段一：分析 URL，返回可用格式
    info = await asyncio.wait_for(_extract_info(url, cookie), timeout=30)
    title = info.get("title") or url
    thumbnail = info.get("thumbnail") or ""
    formats = get_suggested_formats(info.get("formats", []))
    return {
        "status": "analyzed",
        "type": "video",
        "title": title,
        "thumbnail": thumbnail,
        "duration": info.get("duration"),
        "formats": formats,
    }


@app.post("/api/share")
async def share_url(request: Request) -> JSONResponse:
    """Android 分享 API。

    两阶段流程：
    1. POST {"url": "..."} → 分析 URL，返回可用格式列表（`status: "analyzed"`）
    2. POST {"url": "...", "format_id": "..."} → 用指定格式入队下载（`status: "ok"`）
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "error", "message": "请求体必须是 JSON"},
            status_code=400,
        )

    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse(
            {"status": "error", "message": "缺少 URL"},
            status_code=422,
        )

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return JSONResponse(
            {"status": "error", "message": "无效的 URL"},
            status_code=422,
        )

    format_id = body.get("format_id")
    if format_id is not None:
        format_id = str(format_id).strip()

    try:
        result = await _do_share(url, format_id=format_id)
        return JSONResponse(result)
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=422,
        )
    except Exception as e:
        logger.exception("分享 API 处理失败")
        return JSONResponse(
            {"status": "error", "message": str(e), "type": type(e).__name__},
            status_code=500,
        )


# =============================================================================
# 页面路由
# =============================================================================


@ui.page("/")
def index() -> None:
    ui.query('.nicegui-content').style('max-width: 1200px; margin: 0 auto;')
    home.render()


@ui.page("/settings")
def settings_page(edit: str = "", delete: str = "", test: str = "") -> None:
    ui.query('.nicegui-content').style('max-width: 1200px; margin: 0 auto;')
    settings.render(edit_domain=edit, delete_domain=delete, test_domain=test)


@ui.page("/history")
def history_page() -> None:
    ui.query('.nicegui-content').style('max-width: 1200px; margin: 0 auto;')
    history.render()


if __name__ in {"__main__", "__mp_main__"}:
    _reload = _env_bool("NICEVID_RELOAD")
    _storage_secret = os.environ.get(
        "NICEVID_STORAGE_SECRET", "nicevid-secret-key-change-in-production"
    )
    _host = os.environ.get("NICEVID_HOST", "0.0.0.0")
    _port = int(os.environ.get("NICEVID_PORT", "8080"))

    _icon_path = Path(__file__).parent / 'static' / 'favicon.png'

    ui.run(
        host=_host,
        port=_port,
        title="VidZap",
        reload=_reload,
        favicon=str(_icon_path),
        storage_secret=_storage_secret,
    )
