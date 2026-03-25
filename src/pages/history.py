import os
from pathlib import Path

from nicegui import app, background_tasks, ui

from core.cookie_manager import get_cookie_for_url
from core.ytdlp_handler import (
    clear_completed_records,
    delete_download_record,
    get_download_by_id,
    get_download_history,
    start_download,
    update_download_status,
)

STATUS_MAP = {
    "pending": ("⏳", "排队中", "text-grey"),
    "downloading": ("⬇️", "下载中", "text-blue"),
    "completed": ("✅", "已完成", "text-positive"),
    "failed": ("❌", "失败", "text-negative"),
}

# 全局下载进度状态（用于历史页面显示）
_download_progress: dict[int, dict] = {}


def render() -> None:
    """渲染下载历史页面"""
    with ui.header().classes("justify-between items-center"):
        ui.label("下载历史").classes("text-h4 text-white")
        ui.button("返回首页", on_click=lambda: ui.navigate.to("/")).props("flat color=white")

    layout = {"mode": app.storage.user.get("history_layout", "list")}
    container = ui.column().classes("w-full max-w-5xl mx-auto mt-8 px-6")
    dynamic_refs: dict[int, dict] = {}

    def rebuild() -> None:
        dynamic_refs.clear()
        container.clear()
        with container:
            with ui.row().classes("w-full justify-between mb-2"):
                ui.button(
                    "清理已完成",
                    icon="cleaning_services",
                    on_click=lambda: _clear_completed(),
                ).props("flat size=sm color=grey")
                if layout["mode"] == "list":
                    ui.button(
                        icon="grid_view",
                        on_click=lambda: switch("grid"),
                    ).props("flat round size=sm").tooltip("切换为卡片视图")
                else:
                    ui.button(
                        icon="view_list",
                        on_click=lambda: switch("list"),
                    ).props("flat round size=sm").tooltip("切换为列表视图")

            records = get_download_history()
            if not records:
                ui.label("暂无下载记录").classes("text-center text-grey py-12 text-h6")
                return

            if layout["mode"] == "grid":
                with ui.grid(columns=3).classes("w-full gap-4"):
                    for rec in records:
                        _render_grid_card(rec, dynamic_refs)
            else:
                for rec in records:
                    _render_list_card(rec, dynamic_refs)

    def switch(mode: str) -> None:
        layout["mode"] = mode
        app.storage.user["history_layout"] = mode
        rebuild()
        _start_timer()

    def refresh_active() -> None:
        active_count = 0
        for rec_id, refs in dynamic_refs.items():
            rec = get_download_by_id(rec_id)
            if not rec:
                continue
            status = rec["status"]
            if status in ("pending", "downloading"):
                active_count += 1
            icon, label_text, color_class = STATUS_MAP.get(status, ("❓", "未知", "text-grey"))
            old = refs["status_label"]
            old.text = f"{icon} {label_text}"
            old.classes(replace=f"{color_class} text-body2")

            # 更新下载进度
            if status == "downloading" and "progress_bar" in refs:
                progress = _download_progress.get(rec_id, {})
                percent = progress.get("percent", 0)
                speed = progress.get("speed", "")
                eta = progress.get("eta", "")
                refs["progress_bar"].value = percent / 100
                if "progress_label" in refs:
                    refs["progress_label"].text = (
                        f"{percent:.1f}% - {speed} - ETA: {eta}" if speed else "等待中..."
                    )

            if refs.get("last_status") != status:
                refs["last_status"] = status
                refs["actions"].clear()
                with refs["actions"]:
                    _render_actions(rec)
        if active_count == 0:
            auto_timer.deactivate()

    auto_timer: ui.timer | None = None

    def _start_timer() -> None:
        nonlocal auto_timer
        if auto_timer:
            auto_timer.deactivate()
        has_active = any(
            get_download_by_id(rid)
            and get_download_by_id(rid)["status"] in ("pending", "downloading")
            for rid in dynamic_refs
        )
        if has_active:
            auto_timer = ui.timer(2.0, refresh_active)

    rebuild()
    _start_timer()


def _render_list_card(rec: dict, dynamic_refs: dict) -> None:
    """列表视图：水平卡片"""
    rec_id = rec["id"]
    status = rec["status"]
    icon, label_text, color_class = STATUS_MAP.get(status, ("❓", "未知", "text-grey"))
    title = rec.get("title") or rec.get("url", "Unknown")
    short_title = title[:60] + "..." if len(title) > 60 else title

    with ui.card().classes("w-full mb-3 p-4"):
        with ui.row().classes("w-full items-start gap-4"):
            thumb = rec.get("thumbnail")
            if thumb:
                ui.image(thumb).classes("w-32 h-20 object-cover rounded")

            with ui.column().classes("flex-1 min-w-0"):
                ui.label(short_title).classes("text-body1 font-medium")
                ui.label(rec.get("url", "")).classes("text-caption text-grey truncate w-full")
                with ui.row().classes("items-center gap-2 mt-1"):
                    status_label = ui.label(f"{icon} {label_text}").classes(
                        f"{color_class} text-body2"
                    )
                    if rec.get("format_id"):
                        ui.label(f"格式: {rec['format_id']}").classes("text-caption text-grey")
                    if rec.get("created_at"):
                        ui.label(rec["created_at"]).classes("text-caption text-grey")

                if status == "failed" and rec.get("error_msg"):
                    ui.label(f"错误: {rec['error_msg']}").classes("text-negative text-caption mt-1")

            actions_container = ui.column().classes("gap-1")
            with actions_container:
                _render_actions(rec)

        if status == "downloading":
            progress = _download_progress.get(rec_id, {})
            percent = progress.get("percent", 0)
            speed = progress.get("speed", "")
            eta = progress.get("eta", "")
            with ui.column().classes("w-full mt-2"):
                progress_bar = ui.linear_progress(value=percent / 100)
                progress_label = ui.label(
                    f"{percent:.1f}% - {speed} - ETA: {eta}" if speed else "等待中..."
                ).classes("text-caption text-grey")
            dynamic_refs[rec_id] = {
                "status_label": status_label,
                "actions": actions_container,
                "last_status": status,
                "progress_bar": progress_bar,
                "progress_label": progress_label,
            }
        else:
            dynamic_refs[rec_id] = {
                "status_label": status_label,
                "actions": actions_container,
                "last_status": status,
            }


def _render_grid_card(rec: dict, dynamic_refs: dict) -> None:
    """卡片视图：竖向紧凑卡片"""
    rec_id = rec["id"]
    status = rec["status"]
    icon, label_text, color_class = STATUS_MAP.get(status, ("❓", "未知", "text-grey"))
    title = rec.get("title") or rec.get("url", "Unknown")
    short_title = title[:40] + "..." if len(title) > 40 else title

    with ui.card().classes("p-0 overflow-hidden"):
        # 缩略图
        thumb = rec.get("thumbnail")
        if thumb:
            ui.image(thumb).classes("w-full h-40 object-cover")
        else:
            ui.label("").classes("w-full h-40 bg-grey-3")

        with ui.column().classes("p-3 gap-1"):
            ui.label(short_title).classes("text-body2 font-medium line-clamp-2")
            with ui.row().classes("items-center gap-1"):
                status_label = ui.label(f"{icon} {label_text}").classes(
                    f"{color_class} text-caption"
                )
            if rec.get("created_at"):
                ui.label(rec["created_at"]).classes("text-caption text-grey")

            if status == "failed" and rec.get("error_msg"):
                ui.label(rec["error_msg"][:30]).classes("text-negative text-caption truncate")

            actions_container = ui.row().classes("gap-0 mt-1")
            with actions_container:
                _render_actions(rec)

        if status == "downloading":
            progress = _download_progress.get(rec_id, {})
            percent = progress.get("percent", 0)
            speed = progress.get("speed", "")
            eta = progress.get("eta", "")
            with ui.column().classes("px-3 pb-3 w-full"):
                progress_bar = ui.linear_progress(value=percent / 100)
                progress_label = ui.label(
                    f"{percent:.1f}% - {speed} - ETA: {eta}" if speed else "等待中..."
                ).classes("text-caption text-grey")
            dynamic_refs[rec_id] = {
                "status_label": status_label,
                "actions": actions_container,
                "last_status": status,
                "progress_bar": progress_bar,
                "progress_label": progress_label,
            }
        else:
            dynamic_refs[rec_id] = {
                "status_label": status_label,
                "actions": actions_container,
                "last_status": status,
            }


def _render_actions(rec: dict) -> None:
    """渲染操作按钮（根据当前状态）"""
    status = rec["status"]
    file_path = rec.get("file_path") or ""
    file_exists = file_path and os.path.isfile(file_path)

    if status == "failed":
        ui.button(
            "重试",
            icon="refresh",
            on_click=lambda r=rec: _retry_download(r),
        ).props("size=sm flat color=primary")

    if status == "completed" and file_exists:
        ui.button(
            "预览",
            icon="play_circle",
            on_click=lambda r=rec: _preview(r),
        ).props("size=sm flat color=primary")
        ui.button(
            "取回本地",
            icon="download",
            on_click=lambda r=rec: _save_local(r),
        ).props("size=sm flat color=positive")

    ui.button(
        "删除",
        icon="delete",
        on_click=lambda r=rec: _delete_record(r),
    ).props("size=sm flat color=negative")


def _retry_download(rec: dict) -> None:
    """重试失败的下载"""
    rec_id = rec["id"]
    update_download_status(rec_id, "downloading")

    async def _run() -> None:
        try:
            await start_download(
                url=rec["url"],
                format_id=rec["format_id"] or "best",
                cookie_file=get_cookie_for_url(rec["url"]),
                download_id=rec_id,
            )
        except Exception:
            pass

    background_tasks.create(_run())
    ui.notify("已重新开始下载", type="info")
    ui.navigate.to("/history")


def _preview(rec: dict) -> None:
    """预览播放已下载的视频"""
    file_path = rec.get("file_path") or ""
    if not file_path or not os.path.isfile(file_path):
        ui.notify("文件不存在", type="warning")
        return

    filename = os.path.basename(file_path)
    ext = Path(file_path).suffix.lower()
    file_url = f"/downloads-file/{rec['id']}/{filename}"

    with ui.dialog() as dialog:
        with ui.card().classes("w-[90vw] h-[90vh] flex flex-col"):
            ui.label(f"预览: {rec.get('title', filename)}").classes("text-h6 mb-2 shrink-0")
            if ext in (".mp4", ".webm", ".mkv", ".avi", ".mov"):
                ui.video(file_url).classes("flex-1 min-h-0 w-full")
            elif ext in (".mp3", ".m4a", ".ogg", ".wav", ".flac"):
                ui.audio(file_url).classes("w-full")
            else:
                ui.label(f"不支持预览此格式: {ext}")
            with ui.row().classes("w-full justify-end mt-2 shrink-0"):
                ui.button("关闭", on_click=dialog.close).props("flat")
    dialog.open()


def _save_local(rec: dict) -> None:
    """取回本地：下载文件到浏览器"""
    file_path = rec.get("file_path") or ""
    if not file_path or not os.path.isfile(file_path):
        ui.notify("文件不存在", type="warning")
        return

    filename = os.path.basename(file_path)
    ui.download(f"/downloads-file/{rec['id']}/{filename}", filename)


def _clear_completed() -> None:
    """清理所有已完成记录（带确认）"""
    with ui.dialog() as dialog, ui.card():
        ui.label("确定清理所有已完成记录吗？").classes("text-body1")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button("确定", on_click=lambda: _do_clear(dialog)).props("color=negative")
    dialog.open()


def _do_clear(dialog) -> None:
    dialog.close()
    count = clear_completed_records()
    ui.notify(f"已清理 {count} 条记录", type="info")
    ui.navigate.to("/history")


def _delete_record(rec: dict) -> None:
    """删除记录"""
    delete_download_record(rec["id"])
    ui.notify("已删除", type="info")
    ui.navigate.to("/history")
