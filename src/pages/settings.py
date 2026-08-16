from datetime import datetime

from nicegui import Client, background_tasks, run, ui

from core.cookie_manager import (
    delete_cookie,
    extract_domain_from_input,
    get_cookie,
    get_cookie_dir,
    is_valid_domain,
    list_cookies_with_expiry,
    save_cookie,
)


def _is_zhihu_domain(domain: str) -> bool:
    """zhihu.com 及其子域（www.zhihu.com / zhuanlan.zhihu.com 等）。"""
    return domain == "zhihu.com" or domain.endswith(".zhihu.com")


def _is_douban_domain(domain: str) -> bool:
    """douban.com 及其子域（www.douban.com 等）。"""
    return domain == "douban.com" or domain.endswith(".douban.com")


def _expiry_display(expiry: dict) -> tuple[str, str]:
    """返回 (显示文本, 颜色 class)（供表格过期状态列使用）。"""
    status = expiry.get("status")
    if status == "expired":
        return f"已过期 ({expiry['expired']})", "text-negative"
    if status == "session":
        return f"含会话级 cookie ({expiry['session']})", "text-grey-7"
    if status == "valid":
        until = expiry.get("valid_until")
        if until:
            date_str = datetime.fromtimestamp(until).strftime("%Y-%m-%d")
            return f"有效至 {date_str}", "text-positive"
        return "有效", "text-positive"
    return "—", "text-grey-7"


def render(edit_domain: str = "", delete_domain: str = "", test_domain: str = "") -> None:
    """渲染 Cookie 设置页面"""
    ui.on_exception(lambda e: ui.notify(f"页面错误: {e}", type="negative"))

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

        _cookies_client = ui.context.client

        async def _load_cookies() -> None:
            if getattr(_cookies_client, "_deleted", False):
                return
            cookie_table_container.clear()
            rows = await run.io_bound(list_cookies_with_expiry)
            if getattr(_cookies_client, "_deleted", False):
                return
            for row in rows:
                text, color = _expiry_display(row["expiry"])
                row["expiry"]["display"] = text
                row["expiry"]["class"] = color
            with cookie_table_container:
                cookie_table_ref["table"] = ui.table(
                    columns=[
                        {"name": "domain", "label": "域名", "field": "domain"},
                        {"name": "created_at", "label": "添加时间", "field": "created_at"},
                        {"name": "expiry", "label": "过期状态", "field": "expiry"},
                        {"name": "actions", "label": "操作", "field": "actions", "align": "right"},
                    ],
                    rows=rows,
                    row_key="domain",
                    selection="multiple",
                    pagination=10,
                ).classes("w-full")

                cookie_table_ref["table"].add_slot('body-cell-expiry', '''
                    <td :props="props">
                        <span :class="props.value ? props.value.class : 'text-grey-7'">
                            {{ props.value ? props.value.display : '—' }}
                        </span>
                    </td>
                ''')

                cookie_table_ref["table"].add_slot('body-cell-actions', '''
                    <td :props="props"
                        style="white-space: nowrap; padding: 0 4px 0 0; text-align: right;">
                        <a :href="'/settings?test=' + encodeURIComponent(props.row.domain)"
                           v-if="props.row.domain === 'zhihu.com' ||
                                 props.row.domain.endsWith('.zhihu.com') ||
                                 props.row.domain === 'douban.com' ||
                                 props.row.domain.endsWith('.douban.com')"
                           class="text-primary q-ml-xs" style="text-decoration: none;"
                           title="验证 Cookie 是否有效">
                            <q-icon name="verified_user" size="xs"></q-icon>
                        </a>
                        <a :href="'/settings?edit=' + encodeURIComponent(props.row.domain)"
                           class="text-primary" style="text-decoration: none;">
                            <q-icon name="edit" size="xs"></q-icon>
                        </a>
                        <a :href="'/settings?delete=' + encodeURIComponent(props.row.domain)"
                           class="text-negative q-ml-xs" style="text-decoration: none;">
                            <q-icon name="delete" size="xs"></q-icon>
                        </a>
                    </td>
                ''')

                with ui.row().classes("w-full justify-end gap-2 mt-4"):
                    ui.button("添加 Cookie", on_click=lambda: show_add_dialog()).props(
                        "color=primary"
                    )
                    ui.button("删除选中", on_click=lambda: delete_selected()).props(
                        "color=negative"
                    )

        background_tasks.create(_load_cookies())

    async def _run_cookie_test(domain: str, client: Client) -> None:
        """验证指定域名的知乎/豆瓣 Cookie 并提示结果。

        client 必须由调用方在创建 background task 之前捕获：
        background task 内无 slot context（ui.context.client 会抛 RuntimeError），
        需用 `with cookie_table_container:` 显式进入 slot 才能 notify。
        """
        if _is_douban_domain(domain):
            from core.douban_photo import verify_cookie as verify_fn

            site_name = "豆瓣"
        else:
            from core.zhihu_answer import verify_cookie as verify_fn

            site_name = "知乎"

        cookie_data = get_cookie(domain)
        cookie_path = (
            get_cookie_dir() / cookie_data["cookie_file"] if cookie_data else None
        )
        if cookie_path is None:
            if getattr(client, "_deleted", False):
                return
            with cookie_table_container:
                ui.notify(f"Cookie 不存在: {domain}", type="negative")
            return
        ok = await verify_fn(str(cookie_path))
        if getattr(client, "_deleted", False):
            return
        with cookie_table_container:
            if ok:
                ui.notify(
                    f"Cookie 验证成功（{domain}），{site_name}可正常访问",
                    type="positive",
                )
            else:
                ui.notify(
                    f"Cookie 验证失败（{domain}）：可能已失效，请重新从浏览器导出",
                    type="negative",
                )

    def show_add_dialog() -> None:
        """显示添加 Cookie 对话框"""
        with ui.dialog() as dialog, ui.card().classes("w-[28rem]"):
            ui.label("添加 Cookie").classes("text-h6 mb-2")

            ui.label(
                "输入域名或完整 URL，系统会自动提取并规范化域名。"
                "Cookie 将对所有子域名生效（如 youtube.com 同时适用于 www.youtube.com）。"
            ).classes("text-sm text-grey-7 mb-4")

            domain_input = (
                ui.input(
                    label="域名或 URL",
                    placeholder="youtube.com 或 https://www.youtube.com/...",
                )
                .props("outlined")
                .classes("w-full")
            )

            preview_label = ui.label("").classes("text-sm text-grey-6 mt-[-0.25rem] mb-2")

            def _update_preview() -> None:
                raw = domain_input.value or ""
                if not raw.strip():
                    preview_label.text = ""
                    preview_label.classes(remove="text-negative", add="text-grey-6")
                    return
                normalized = extract_domain_from_input(raw)
                if not is_valid_domain(normalized):
                    preview_label.text = f"⚠️ 无效域名：{normalized}"
                    preview_label.classes(remove="text-grey-6", add="text-negative")
                else:
                    preview_label.text = f"将保存为：{normalized}"
                    preview_label.classes(remove="text-negative", add="text-grey-6")

            domain_input.on("update:model-value", _update_preview)

            cookie_input = (
                ui.textarea("Cookie 内容（Netscape 格式）")
                .props("outlined rows=10")
                .classes("w-full mt-2")
            )

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("取消", on_click=dialog.close).props("flat")

                def save_and_close() -> None:
                    raw_domain = (domain_input.value or "").strip()
                    cookie_content = cookie_input.value or ""

                    if not raw_domain or not cookie_content:
                        ui.notify("请填写完整", type="warning")
                        return

                    domain = extract_domain_from_input(raw_domain)
                    if not is_valid_domain(domain):
                        ui.notify(f"无效的域名格式：{domain}", type="negative")
                        return

                    if not save_cookie(domain, cookie_content):
                        ui.notify(
                            "Cookie 内容无效，无法解析为有效格式。请检查内容是否正确。",
                            type="negative",
                        )
                        return
                    ui.notify(f"Cookie 已保存（{domain}）", type="positive")
                    if _is_zhihu_domain(domain) or _is_douban_domain(domain):
                        background_tasks.create(
                            _run_cookie_test(domain, ui.context.client)
                        )
                    dialog.close()
                    ui.navigate.to("/settings")

                ui.button("保存", on_click=save_and_close).props("color=positive")

        dialog.open()

    def show_modify_dialog(domain: str) -> None:
        """显示修改 Cookie 对话框"""
        cookie_data = get_cookie(domain)
        if cookie_data is None:
            ui.notify(f"Cookie 不存在: {domain}", type="negative")
            ui.navigate.to("/settings")
            return

        original_domain = cookie_data["domain"]
        content = cookie_data.get("content", "")

        with ui.dialog() as dialog, ui.card().classes("w-[28rem]"):
            ui.label("修改 Cookie").classes("text-h6 mb-2")

            ui.label(
                "修改域名或 Cookie 内容。修改域名将重新保存 Cookie。"
            ).classes("text-sm text-grey-7 mb-4")

            domain_input = (
                ui.input(
                    label="域名或 URL",
                    value=original_domain,
                    placeholder="youtube.com 或 https://www.youtube.com/...",
                )
                .props("outlined")
                .classes("w-full")
            )

            preview_label = ui.label("").classes("text-sm text-grey-6 mt-[-0.25rem] mb-2")

            def _update_preview() -> None:
                raw = domain_input.value or ""
                if not raw.strip():
                    preview_label.text = ""
                    preview_label.classes(remove="text-negative", add="text-grey-6")
                    return
                normalized = extract_domain_from_input(raw)
                if not is_valid_domain(normalized):
                    preview_label.text = f"⚠️ 无效域名：{normalized}"
                    preview_label.classes(remove="text-grey-6", add="text-negative")
                else:
                    preview_label.text = f"将保存为：{normalized}"
                    preview_label.classes(remove="text-negative", add="text-grey-6")

            domain_input.on("update:model-value", _update_preview)
            _update_preview()

            cookie_input = (
                ui.textarea("Cookie 内容（Netscape 格式）", value=content)
                .props("outlined rows=10")
                .classes("w-full mt-2")
            )

            with ui.row().classes("w-full justify-end gap-2"):
                def cancel_and_close() -> None:
                    dialog.close()
                    ui.navigate.to("/settings")

                ui.button("取消", on_click=cancel_and_close).props("flat")

                def save_changes() -> None:
                    raw_domain = (domain_input.value or "").strip()
                    new_content = cookie_input.value or ""

                    if not raw_domain or not new_content:
                        ui.notify("请填写完整", type="warning")
                        return

                    new_domain = extract_domain_from_input(raw_domain)
                    if not is_valid_domain(new_domain):
                        ui.notify(f"无效的域名格式：{new_domain}", type="negative")
                        return

                    if not save_cookie(new_domain, new_content):
                        ui.notify(
                            "Cookie 内容无效，无法解析为有效格式。请检查内容是否正确。",
                            type="negative",
                        )
                        return

                    if new_domain != original_domain:
                        delete_cookie(original_domain)

                    ui.notify(f"Cookie 已更新（{new_domain}）", type="positive")
                    if _is_zhihu_domain(new_domain) or _is_douban_domain(new_domain):
                        background_tasks.create(
                            _run_cookie_test(new_domain, ui.context.client)
                        )
                    dialog.close()
                    ui.navigate.to("/settings")

                ui.button("保存", on_click=save_changes).props("color=positive")

        dialog.open()

    if edit_domain:
        show_modify_dialog(edit_domain)

    if test_domain:
        background_tasks.create(_run_cookie_test(test_domain, ui.context.client))

    def show_delete_confirm_dialog(domain: str) -> None:
        with ui.dialog() as dialog, ui.card().classes("w-80"):
            ui.label("确认删除").classes("text-h6 mb-2")
            ui.label(f"确定要删除 {domain} 的 Cookie 吗？").classes("mb-4")
            with ui.row().classes("w-full justify-end gap-2"):
                def cancel_delete() -> None:
                    dialog.close()
                    ui.navigate.to("/settings")

                ui.button("取消", on_click=cancel_delete).props("flat")

                def confirm_delete() -> None:
                    delete_cookie(domain)
                    dialog.close()
                    ui.notify(f"Cookie 已删除（{domain}）", type="positive")
                    ui.navigate.to("/settings")

                ui.button("确认删除", on_click=confirm_delete).props("color=negative")

        dialog.open()

    if delete_domain:
        show_delete_confirm_dialog(delete_domain)

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
