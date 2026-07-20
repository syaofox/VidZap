import asyncio
import logging
import mimetypes
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse
from nicegui import app, ui

# 添加 src 到 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.db import init_db
from core.ytdlp_handler import get_download_by_id
from pages import history, home, settings

logger = logging.getLogger(__name__)

# 创建必要的目录
Path("downloads").mkdir(exist_ok=True)

Path(os.environ.get("NICEVID_DATA_DIR", "data")).mkdir(parents=True, exist_ok=True)

# 初始化数据库
init_db()


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


# =============================================================================
# Android 分享 API
# =============================================================================


async def _do_share(url: str) -> dict:
    """处理分享 URL 的核心逻辑：分类、创建记录、入队下载。"""
    from core.cookie_manager import get_cookie_for_url
    from core.ytdlp_handler import create_download_record
    from pages.home import classify_urls

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

    # 视频下载（默认最佳画质）
    from core.download_queue import download_queue
    from core.ytdlp_handler import extract_info as _extract_info

    title = url
    thumbnail = ""
    try:
        info = await asyncio.wait_for(_extract_info(url, cookie), timeout=30)
        title = info.get("title") or url
        thumbnail = info.get("thumbnail") or ""
    except Exception:
        pass

    format_id = "bestvideo+bestaudio/best"
    dl_id = create_download_record(url, title, thumbnail, format_id)
    await download_queue.enqueue(
        url=url,
        format_id=format_id,
        cookie_file=cookie,
        write_thumbnail=True,
        download_id=dl_id,
    )
    return {"status": "ok", "download_id": dl_id, "title": title, "type": "video"}


@app.post("/api/share")
async def share_url(request: Request) -> JSONResponse:
    """Android 分享 API：接受 JSON {"url": "..."}，入队下载后返回 download_id。"""
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

    try:
        result = await _do_share(url)
        return JSONResponse(result)
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=422,
        )
    except Exception as e:
        logger.exception("分享 API 处理失败")
        return JSONResponse(
            {"status": "error", "message": str(e)},
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
def settings_page(edit: str = "", delete: str = "") -> None:
    ui.query('.nicegui-content').style('max-width: 1200px; margin: 0 auto;')
    settings.render(edit_domain=edit, delete_domain=delete)


@ui.page("/history")
def history_page() -> None:
    ui.query('.nicegui-content').style('max-width: 1200px; margin: 0 auto;')
    history.render()


if __name__ in {"__main__", "__mp_main__"}:
    _reload = os.environ.get("NICEVID_RELOAD", "").lower() == "true"
    _storage_secret = os.environ.get(
        "NICEVID_STORAGE_SECRET", "nicevid-secret-key-change-in-production"
    )

    ui.run(
        host="0.0.0.0",
        port=8080,
        title="VidZap",
        reload=_reload,
        favicon="🎬",
        storage_secret=_storage_secret,
    )
