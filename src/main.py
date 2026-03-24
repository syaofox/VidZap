import sys
from pathlib import Path

from fastapi.responses import FileResponse
from nicegui import app, ui

# 添加 src 到 sys.path
sys.path.insert(0, str(Path(__file__).parent))

from core.db import init_db
from core.ytdlp_handler import get_download_by_id
from pages import history, home, settings

# 创建必要的目录
Path("downloads").mkdir(exist_ok=True)
Path("cookies").mkdir(exist_ok=True)

# 初始化数据库
init_db()


@app.get("/downloads-file/{download_id}/{filename}")
def serve_download_file(download_id: int, filename: str):
    """按下载记录 ID 提供文件下载/预览"""
    rec = get_download_by_id(download_id)
    if not rec or not rec.get("file_path"):
        return {"error": "记录不存在"}, 404
    file_path = Path(rec["file_path"])
    if not file_path.is_file():
        return {"error": "文件不存在"}, 404
    return FileResponse(
        str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@ui.page("/")
def index() -> None:
    ui.add_head_html("""
    <style>
        .nicegui-content { max-width: 1200px; margin: 0 auto; }
    </style>
    """)
    home.render()


@ui.page("/settings")
def settings_page() -> None:
    ui.add_head_html("""
    <style>
        .nicegui-content { max-width: 1200px; margin: 0 auto; }
    </style>
    """)
    settings.render()


@ui.page("/history")
def history_page() -> None:
    ui.add_head_html("""
    <style>
        .nicegui-content { max-width: 1200px; margin: 0 auto; }
    </style>
    """)
    history.render()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        host="0.0.0.0",
        port=8080,
        title="NiceVid",
        reload=True,
        favicon="🎬",
        storage_secret="nicevid-secret-key-change-in-production",
    )
