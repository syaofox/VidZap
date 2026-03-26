import asyncio
import os
from pathlib import Path

from nicegui import app, ui

from core.cookie_manager import get_cookie_for_url
from core.download_queue import download_queue
from core.ytdlp_handler import (
    clear_completed_records,
    delete_download_record,
    get_download_by_id,
    get_download_history,
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

    def rebuild(records: list[dict] | None = None) -> None:
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

            if records is None:
                with ui.row().classes("w-full items-center gap-2"):
                    ui.spinner(size="sm")
                    ui.label("加载中...").classes("text-grey")
                return

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

    async def _load_and_rebuild() -> None:
        records = await asyncio.get_event_loop().run_in_executor(None, get_download_history)
        rebuild(records)
        _start_timer()

    def switch(mode: str) -> None:
        layout["mode"] = mode
        app.storage.user["history_layout"] = mode
        ui.timer(0.1, _load_and_rebuild, once=True)

    def refresh_active() -> None:
        try:
            client = ui.context.client
            if getattr(client, "_deleted", False):
                if auto_timer:
                    auto_timer.deactivate()
                return
        except Exception:
            return
        try:
            active_count = 0
            need_rebuild = False
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
                            f"{percent:.2f}% - {speed} - ETA: {eta}" if speed else "等待中..."
                        )

                # 检测状态变化
                last_status = refs.get("last_status")
                if last_status != status:
                    refs["last_status"] = status
                    # 如果从下载中变为完成或失败，需要重建以移除进度条
                    if last_status == "downloading" and status in ("completed", "failed"):
                        need_rebuild = True
                    else:
                        refs["actions"].clear()
                        with refs["actions"]:
                            _render_actions(rec)

            if need_rebuild:
                auto_timer.deactivate()
                ui.navigate.to("/history")
                return
            elif active_count == 0:
                auto_timer.deactivate()
        except Exception:
            pass

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

    ui.timer(0.1, _load_and_rebuild, once=True)


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
                ui.link(short_title, rec.get("url", ""), new_tab=True).classes(
                    "text-body1 font-medium text-grey-9 no-underline hover:text-primary"
                )
                ui.label(rec.get("url", "")).classes("text-caption text-grey truncate w-full")
                with ui.row().classes("items-center gap-2 mt-1"):
                    status_label = ui.label(f"{icon} {label_text}").classes(
                        f"{color_class} text-body2"
                    )
                    if rec.get("format_id") == "images":
                        ui.label("类型: 图片").classes("text-caption text-grey")
                    elif rec.get("format_id"):
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
                progress_bar = ui.linear_progress(value=percent / 100, show_value=False)
                progress_label = ui.label(
                    f"{percent:.2f}% - {speed} - ETA: {eta}" if speed else "等待中..."
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
            ui.link(short_title, rec.get("url", ""), new_tab=True).classes(
                "text-body2 font-medium text-grey-9 no-underline hover:text-primary line-clamp-2"
            )
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
                progress_bar = ui.linear_progress(value=percent / 100, show_value=False)
                progress_label = ui.label(
                    f"{percent:.2f}% - {speed} - ETA: {eta}" if speed else "等待中..."
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
    is_note = rec.get("format_id") == "images"
    file_exists = file_path and (os.path.isfile(file_path) or os.path.isdir(file_path))

    if status in ("pending", "downloading"):
        ui.button(
            "停止",
            icon="stop",
            on_click=lambda r=rec: _stop_download(r),
        ).props("size=sm flat color=warning")

    if status == "failed":
        ui.button(
            "重试",
            icon="refresh",
            on_click=lambda r=rec: _retry_download(r),
        ).props("size=sm flat color=primary")

    if status == "completed" and file_exists:
        if is_note:
            ui.button(
                "查看图片",
                icon="photo_library",
                on_click=lambda r=rec: _preview_note(r),
            ).props("size=sm flat color=primary")
        else:
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


async def _retry_download(rec: dict) -> None:
    """重试失败的下载"""
    rec_id = rec["id"]
    update_download_status(rec_id, "downloading")

    def _make_callback(did: int):
        def cb(percent: float, speed: str, eta: str) -> None:
            _download_progress[did] = {
                "percent": percent,
                "speed": speed,
                "eta": eta,
            }

        return cb

    is_note = rec.get("format_id") == "images"
    await download_queue.enqueue(
        url=rec["url"],
        format_id=rec["format_id"] or "best",
        cookie_file=get_cookie_for_url(rec["url"]),
        progress_callback=_make_callback(rec_id),
        download_id=rec_id,
        task_type="douyin_note" if is_note else "video",
    )

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


def _preview_note(rec: dict) -> None:
    """预览已下载的抖音图文（幻灯片）图片和视频"""
    file_path = rec.get("file_path") or ""
    if not file_path or not os.path.isdir(file_path):
        ui.notify("目录不存在", type="warning")
        return

    # List media files in the directory
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}
    video_exts = {".mp4", ".webm", ".mov", ".mkv"}
    images = sorted(
        [f for f in Path(file_path).iterdir() if f.is_file() and f.suffix.lower() in image_exts]
    )
    videos = sorted(
        [f for f in Path(file_path).iterdir() if f.is_file() and f.suffix.lower() in video_exts]
    )

    if not images and not videos:
        ui.notify("目录中没有找到媒体文件", type="warning")
        return

    title = rec.get("title") or os.path.basename(file_path)
    parts = []
    if images:
        parts.append(f"{len(images)} 张图片")
    if videos:
        parts.append(f"{len(videos)} 个视频")

    with ui.dialog() as dialog:
        with ui.card().classes("w-[90vw] h-[90vh] flex flex-col"):
            ui.label(f"{title} ({' + '.join(parts)})").classes("text-h6 mb-2 shrink-0")
            with ui.scroll_area().classes("flex-1 min-h-0"):
                with ui.row().classes("w-full gap-2 flex-wrap"):
                    for img_path in images:
                        file_url = f"/downloads-file/{rec['id']}/{img_path.name}"
                        img = ui.image(file_url).classes(
                            "w-48 h-48 object-cover rounded cursor-pointer"
                        )

                        def _open_img(e=None, u=file_url):
                            ui.navigate.to(u, new_tab=True)

                        img.on("click", _open_img)
                    for vid_path in videos:
                        file_url = f"/downloads-file/{rec['id']}/{vid_path.name}"
                        with ui.card().classes("w-64 p-2"):
                            ui.video(file_url).classes("w-full rounded")
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
    """删除记录（下载中任务先停止再删除）"""
    rec_id = rec["id"]
    status = rec["status"]

    if status in ("pending", "downloading"):
        with ui.dialog() as dialog, ui.card():
            ui.label("该任务正在下载中，确定停止并删除吗？").classes("text-body1")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button("取消", on_click=dialog.close).props("flat")
                ui.button(
                    "确定",
                    on_click=lambda: _do_delete_with_stop(dialog, rec_id),
                ).props("color=negative")
        dialog.open()
    else:
        delete_download_record(rec_id)
        ui.notify("已删除", type="info")
        ui.navigate.to("/history")


async def _do_delete_with_stop(dialog, download_id: int) -> None:
    dialog.close()
    await download_queue.cancel(download_id)
    _download_progress.pop(download_id, None)
    delete_download_record(download_id)
    ui.notify("已删除", type="info")
    ui.navigate.to("/history")


def _stop_download(rec: dict) -> None:
    """停止下载任务"""
    rec_id = rec["id"]
    with ui.dialog() as dialog, ui.card():
        ui.label("确定停止该下载任务吗？").classes("text-body1")
        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            ui.button("取消", on_click=dialog.close).props("flat")
            ui.button(
                "确定",
                on_click=lambda: _do_stop(dialog, rec_id),
            ).props("color=negative")
    dialog.open()


async def _do_stop(dialog, download_id: int) -> None:
    """执行停止下载"""
    dialog.close()
    await download_queue.cancel(download_id)
    _download_progress.pop(download_id, None)
    ui.notify("已停止下载", type="info")
    ui.navigate.to("/history")
