import asyncio

from nicegui import ui

from core.cookie_manager import delete_cookie, list_cookies, save_cookie


def render() -> None:
    """渲染 Cookie 设置页面"""
    with ui.header().classes("justify-between items-center"):
        ui.label("Cookie 设置").classes("text-h4 text-white")
        ui.button("返回首页", on_click=lambda: ui.navigate.to("/")).props("flat color=white")

    with ui.card().classes("w-full max-w-4xl mx-auto mt-8 p-6"):
        ui.label("已保存的 Cookie").classes("text-h6 mb-4")

        cookie_table_container = ui.column().classes("w-full")
        cookie_table_ref: dict = {"table": None}

        with cookie_table_container:
            with ui.row().classes("w-full items-center gap-2"):
                ui.spinner(size="sm")
                ui.label("加载中...").classes("text-grey")

        async def _load_cookies() -> None:
            cookie_table_container.clear()
            rows = await asyncio.get_event_loop().run_in_executor(None, list_cookies)
            cookie_table_ref["table"] = ui.table(
                columns=[
                    {"name": "domain", "label": "域名", "field": "domain"},
                    {"name": "created_at", "label": "添加时间", "field": "created_at"},
                ],
                rows=rows,
                row_key="domain",
                selection="multiple",
                pagination=10,
            ).classes("w-full")

            with ui.row().classes("w-full justify-end gap-2 mt-4"):
                ui.button("添加 Cookie", on_click=lambda: show_add_dialog()).props("color=primary")
                ui.button("删除选中", on_click=lambda: delete_selected()).props("color=negative")

        ui.timer(0.1, _load_cookies, once=True)

    def show_add_dialog() -> None:
        """显示添加 Cookie 对话框"""
        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("添加 Cookie").classes("text-h6 mb-4")

            domain_input = (
                ui.input("域名（如 youtube.com）").props("outlined").classes("w-full mb-2")
            )
            cookie_input = (
                ui.textarea("Cookie 内容（Netscape 格式）")
                .props("outlined rows=10")
                .classes("w-full")
            )

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")

                def save_and_close() -> None:
                    if not domain_input.value or not cookie_input.value:
                        ui.notify("请填写完整", type="warning")
                        return

                    save_cookie(domain_input.value.strip(), cookie_input.value)
                    ui.notify("Cookie 已保存", type="positive")
                    dialog.close()
                    ui.navigate.to("/settings")

                ui.button("保存", on_click=save_and_close).props("color=positive")

        dialog.open()

    def delete_selected() -> None:
        """删除选中的 Cookie"""
        table = cookie_table_ref.get("table")
        if table is None:
            ui.notify("表格加载中，请稍后重试", type="warning")
            return
        selected = table.selected
        if not selected:
            ui.notify("请选择要删除的 Cookie", type="warning")
            return

        for row in selected:
            delete_cookie(row["domain"])

        ui.notify("已删除", type="positive")
        ui.navigate.to("/settings")
