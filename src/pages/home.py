import asyncio
from collections.abc import Callable

from nicegui import ui

from core.cookie_manager import get_cookie_for_url
from core.douyin_note import extract_note_images, is_douyin_note_url
from core.download_queue import download_queue
from core.version import get_app_version
from core.ytdlp_handler import (
    check_ffmpeg,
    create_download_record,
    delete_download_record,
    extract_info,
    find_existing_download,
    get_supported_sites,
    get_ytdlp_version,
    update_ytdlp,
)


def render() -> None:
    """渲染首页"""
    # 顶部导航
    with ui.header().classes("justify-between items-center"):
        with ui.row().classes("items-baseline gap-1"):
            ui.label("NiceVid").classes("text-h4 text-white")
            ui.label(f"v{get_app_version()}").classes("text-caption text-white/60")
        with ui.row():
            ui.button("首页", on_click=lambda: ui.navigate.to("/")).props("flat color=white")
            ui.button("Cookie 设置", on_click=lambda: ui.navigate.to("/settings")).props(
                "flat color=white"
            )
            ui.button("下载历史", on_click=lambda: ui.navigate.to("/history")).props(
                "flat color=white"
            )
            ui.button("支持网站", on_click=lambda: supported_sites_dialog.open()).props(
                "flat color=white"
            )

    # 常用网站列表（顶部展示用 chip）
    popular_sites = {
        "视频平台": [
            "YouTube",
            "Bilibili",
            "抖音",
            "西瓜视频",
            "爱奇艺",
            "优酷",
            "腾讯视频",
            "芒果TV",
            "AcFun",
            "Twitch",
            "Vimeo",
            "Dailymotion",
            "Nicovideo",
            "Rumble",
            "Odysee",
            "斗鱼",
            "虎牙",
            "快手",
            "好看视频",
        ],
        "社交媒体": [
            "Twitter/X",
            "Instagram",
            "Facebook",
            "TikTok",
            "Reddit",
            "Pinterest",
            "Tumblr",
            "Weibo",
            "小红书",
            "VK",
            "Threads",
            "Bluesky",
        ],
        "音乐平台": [
            "SoundCloud",
            "Bandcamp",
            "网易云音乐",
            "QQ音乐",
            "Spotify",
            "Apple Music",
            "Deezer",
            "Tidal",
            "酷狗音乐",
            "酷我音乐",
            "喜马拉雅",
            "荔枝FM",
        ],
        "新闻与教育": [
            "BBC",
            "CNN",
            "TED",
            "Coursera",
            "Udemy",
            "Khan Academy",
            "Arte",
            "France TV",
        ],
        "其他": [
            "Imgur",
            "Flickr",
            "Archive.org",
            "豆瓣",
            "知乎",
            "今日头条",
            "Giphy",
            "9GAG",
        ],
    }

    # 常用站点名称 -> URL 映射（用于 chip 跳转）
    _POPULAR_URLS = {  # noqa: N806
        "YouTube": "https://www.youtube.com",
        "Bilibili": "https://www.bilibili.com",
        "抖音": "https://www.douyin.com",
        "西瓜视频": "https://www.ixigua.com",
        "爱奇艺": "https://www.iqiyi.com",
        "优酷": "https://www.youku.com",
        "腾讯视频": "https://v.qq.com",
        "芒果TV": "https://www.mgtv.com",
        "AcFun": "https://www.acfun.cn",
        "Twitch": "https://www.twitch.tv",
        "Vimeo": "https://vimeo.com",
        "Dailymotion": "https://www.dailymotion.com",
        "Nicovideo": "https://www.nicovideo.jp",
        "Rumble": "https://rumble.com",
        "Odysee": "https://odysee.com",
        "斗鱼": "https://www.douyu.com",
        "虎牙": "https://www.huya.com",
        "快手": "https://www.kuaishou.com",
        "好看视频": "https://haokan.baidu.com",
        "Twitter/X": "https://x.com",
        "Instagram": "https://www.instagram.com",
        "Facebook": "https://www.facebook.com",
        "TikTok": "https://www.tiktok.com",
        "Reddit": "https://www.reddit.com",
        "Pinterest": "https://www.pinterest.com",
        "Tumblr": "https://www.tumblr.com",
        "Weibo": "https://weibo.com",
        "小红书": "https://www.xiaohongshu.com",
        "VK": "https://vk.com",
        "Threads": "https://www.threads.net",
        "Bluesky": "https://bsky.app",
        "SoundCloud": "https://soundcloud.com",
        "Bandcamp": "https://bandcamp.com",
        "网易云音乐": "https://music.163.com",
        "QQ音乐": "https://y.qq.com",
        "Spotify": "https://open.spotify.com",
        "Apple Music": "https://music.apple.com",
        "Deezer": "https://www.deezer.com",
        "Tidal": "https://tidal.com",
        "酷狗音乐": "https://www.kugou.com",
        "酷我音乐": "https://www.kuwo.cn",
        "喜马拉雅": "https://www.ximalaya.com",
        "荔枝FM": "https://www.lizhi.fm",
        "BBC": "https://www.bbc.com",
        "CNN": "https://www.cnn.com",
        "TED": "https://www.ted.com",
        "Coursera": "https://www.coursera.org",
        "Udemy": "https://www.udemy.com",
        "Khan Academy": "https://www.khanacademy.org",
        "Arte": "https://www.arte.tv",
        "France TV": "https://www.france.tv",
        "Imgur": "https://imgur.com",
        "Flickr": "https://www.flickr.com",
        "Archive.org": "https://archive.org",
        "豆瓣": "https://www.douban.com",
        "知乎": "https://www.zhihu.com",
        "今日头条": "https://www.toutiao.com",
        "Giphy": "https://giphy.com",
        "9GAG": "https://9gag.com",
    }

    with ui.dialog() as supported_sites_dialog, ui.card().classes("w-[560px]"):
        ui.label("支持的网站").classes("text-h6 mb-4")
        with ui.scroll_area().classes("h-80"):
            for category, sites in popular_sites.items():
                ui.label(category).classes("text-subtitle1 font-bold mt-2")
                with ui.row().classes("gap-1 flex-wrap"):
                    for site in sites:
                        url = _POPULAR_URLS.get(site, "")
                        if url:
                            ui.chip(
                                site,
                                icon="open_in_new",
                                color="primary" if category == "视频平台" else "grey-5",
                                on_click=lambda e=None, u=url: ui.navigate.to(u, new_tab=True),
                            )
                        else:
                            ui.chip(site, color="primary" if category == "视频平台" else "grey-5")
        with ui.row().classes("items-center mt-2 gap-2"):
            ui.label("以上为常用网站，yt-dlp 实际支持更多站点").classes("text-caption text-grey")
            ui.button(
                "查看全部",
                on_click=lambda: (supported_sites_dialog.close(), all_sites_dialog.open()),
            ).props("flat dense size=sm color=primary")
        with ui.row().classes("w-full justify-between items-center mt-2"):
            ver_label = ui.label("yt-dlp 加载中...").classes("text-caption text-grey")

            async def _load_version() -> None:
                ver = await asyncio.get_event_loop().run_in_executor(None, get_ytdlp_version)
                ver_label.text = f"yt-dlp {ver}"

            ui.timer(0.1, _load_version, once=True)

            async def _update_ytdlp() -> None:
                update_btn.disable()
                update_btn.text = "更新中..."
                ok, msg, changed = await asyncio.get_event_loop().run_in_executor(
                    None, update_ytdlp
                )
                if ok:
                    ver_label.text = f"yt-dlp {get_ytdlp_version()}"
                    if changed:
                        ui.notify(
                            f"更新成功: {msg}\n请重启应用使新版生效",
                            type="positive",
                            multi_line=True,
                        )
                    else:
                        ui.notify(msg, type="info")
                else:
                    ui.notify(f"更新失败: {msg}", type="negative")
                update_btn.text = "更新 yt-dlp"
                update_btn.enable()

            update_btn = ui.button(
                "更新 yt-dlp", on_click=_update_ytdlp, icon="system_update"
            ).props("flat size=sm color=primary")
        with ui.row().classes("w-full justify-end"):
            ui.button("关闭", on_click=supported_sites_dialog.close).props("flat")

    # 全部站点弹窗（从 yt-dlp 动态获取）
    sites_container: dict = {"container": None}

    async def _load_sites() -> None:
        all_extractors = await asyncio.get_event_loop().run_in_executor(None, get_supported_sites)
        sites_container["container"].clear()
        with sites_container["container"]:
            ui.label(f"共 {len(all_extractors)} 个提取器").classes("text-caption text-grey mb-2")
            with ui.column().classes("gap-0 w-full"):
                for name, url in all_extractors:
                    with ui.row().classes("items-center gap-2 w-full py-0.5"):
                        ui.label(f"• {name}").classes("w-44 text-body2 shrink-0")
                        if url:
                            ui.link(url, url, new_tab=True).classes(
                                "text-primary text-body2 truncate"
                            )
                        else:
                            ui.label("-").classes("text-grey text-body2")

    with ui.dialog() as all_sites_dialog, ui.card().classes("w-[720px]"):
        ui.label("全部支持的站点（来自 yt-dlp）").classes("text-h6 mb-2")
        with ui.scroll_area().classes("h-[480px]"):
            with ui.column().classes("w-full"):
                with ui.row().classes("items-center gap-2"):
                    ui.spinner(size="sm")
                    ui.label("加载中...").classes("text-grey")
                sites_container["container"] = ui.column().classes("gap-0 w-full")
                all_sites_dialog.on("open", lambda: ui.timer(0.1, _load_sites, once=True))
        with ui.row().classes("w-full justify-end mt-2"):
            ui.button("关闭", on_click=all_sites_dialog.close).props("flat")

    # URL 输入区域
    with ui.card().classes("w-full max-w-4xl mx-auto mt-8 p-6"):
        ui.label("输入视频链接").classes("text-h6 mb-2")

        # 单个 URL 输入
        url_input = ui.input("粘贴视频链接").props("outlined clearable").classes("w-full mb-4")

        # 批量 URL 输入
        batch_toggle = ui.switch("批量下载模式")
        batch_input = ui.textarea("每行一个链接").props("outlined").classes("w-full hidden")

        def toggle_batch() -> None:
            if batch_toggle.value:
                batch_input.classes(remove="hidden")
            else:
                batch_input.classes("hidden")

        batch_toggle.on("update:model-value", toggle_batch)

        with ui.row().classes("w-full justify-end gap-2"):
            analyze_btn = ui.button("分析", on_click=lambda: analyze()).props("color=primary")

    # 视频信息卡片
    info_card = ui.card().classes("w-full max-w-4xl mx-auto mt-4 p-6 hidden")

    # 格式选择表格
    format_card = ui.card().classes("w-full max-w-4xl mx-auto mt-4 p-6 hidden")

    # 存储分析结果
    analysis_result: dict = {"info": None, "urls": []}

    async def download_note(on_done: Callable | None = None) -> None:
        """下载抖音图文（幻灯片）的所有图片"""
        urls = analysis_result["urls"]
        if not urls:
            ui.notify("请先分析链接", type="warning")
            return

        info = analysis_result.get("info") or {}
        from pages.history import _download_progress

        for url in urls:
            cookie = get_cookie_for_url(url)
            dl_id = create_download_record(
                url=url,
                title=info.get("title", "Unknown"),
                thumbnail=info.get("thumbnail", ""),
                format_id="images",
            )

            def _make_callback(did: int):
                def cb(percent: float, speed: str, eta: str) -> None:
                    _download_progress[did] = {
                        "percent": percent,
                        "speed": speed,
                        "eta": eta,
                    }

                return cb

            await download_queue.enqueue(
                url=url,
                format_id="images",
                cookie_file=cookie,
                progress_callback=_make_callback(dl_id),
                download_id=dl_id,
                task_type="douyin_note",
            )

        if on_done:
            on_done()

        count = len(urls)
        ui.notify(
            f"已添加 {count} 个图文下载任务，请前往下载历史页面查看进度",
            type="positive",
            multi_line=True,
        )

    async def analyze() -> None:
        """分析视频链接"""
        analyze_btn.disable()
        # 清理上一轮的卡片
        for card in (info_card, format_card):
            card.classes("hidden")
            card.clear()
        try:
            urls = []
            if batch_toggle.value and batch_input.value:
                urls = [u.strip() for u in batch_input.value.split("\n") if u.strip()]
            elif url_input.value:
                urls = [url_input.value.strip()]

            if not urls:
                ui.notify("请输入链接", type="warning")
                return

            analysis_result["urls"] = urls

            info_card.classes(remove="hidden")
            info_card.clear()

            with info_card:
                ui.spinner(size="lg")
                ui.label("正在分析...").classes("ml-4")

            cookie = get_cookie_for_url(urls[0])

            # 检测抖音图文（幻灯片）链接
            if is_douyin_note_url(urls[0]):
                note_info = await extract_note_images(urls[0], cookie)
                analysis_result["info"] = note_info
                analysis_result["is_note"] = True

                info_card.clear()
                with info_card:
                    with ui.row().classes("w-full gap-4"):
                        if note_info.get("thumbnail"):
                            ui.image(note_info["thumbnail"]).classes("w-48 rounded")
                        with ui.column().classes("flex-1"):
                            ui.label(note_info["title"]).classes("text-h6")
                            media_parts = []
                            if note_info.get("image_count"):
                                media_parts.append(f"{note_info['image_count']} 张图片")
                            if note_info.get("video_count"):
                                media_parts.append(f"{note_info['video_count']} 个视频")
                            ui.label(f"共 {' + '.join(media_parts)}").classes(
                                "text-body1 text-grey"
                            )
                            if len(urls) > 1:
                                ui.label(f"批量模式：共 {len(urls)} 个链接").classes("text-caption")

                # 图片预览 & 下载按钮
                format_card.classes(remove="hidden")
                format_card.clear()
                with format_card:
                    ui.label("图片预览").classes("text-h6 mb-2")
                    with ui.row().classes("w-full gap-2 flex-wrap"):
                        for img_url in note_info["image_urls"]:
                            img = ui.image(img_url).classes(
                                "w-32 h-32 object-cover rounded cursor-pointer"
                            )

                            def _open_image(u=img_url):
                                ui.navigate.to(u, new_tab=True)

                            img.on("click", _open_image)

                    with ui.row().classes("w-full justify-end mt-4"):
                        dl_btn_ref: dict = {"btn": None}

                        async def do_note_download() -> None:
                            urls_to_check = analysis_result["urls"]
                            duplicate_titles: list[str] = []
                            for u in urls_to_check:
                                existing = find_existing_download(u)
                                if existing:
                                    duplicate_titles.append(existing.get("title") or u[:60])

                            if duplicate_titles:
                                with ui.dialog() as dialog, ui.card():
                                    ui.label("链接已存在于下载记录中").classes("text-h6")
                                    for t in duplicate_titles:
                                        ui.label(f"  · {t}").classes("text-body2")
                                    with ui.row().classes("w-full justify-end mt-4 gap-2"):
                                        ui.button(
                                            "放弃",
                                            on_click=lambda: dialog.submit("cancel"),
                                        ).props("flat")
                                        ui.button(
                                            "覆盖",
                                            on_click=lambda: dialog.submit("overwrite"),
                                        ).props("color=negative")
                                choice = await dialog
                                if choice != "overwrite":
                                    dl_btn_ref["btn"].enable()
                                    return
                                for u in urls_to_check:
                                    existing = find_existing_download(u)
                                    if existing:
                                        delete_download_record(existing["id"])

                            dl_btn_ref["btn"].disable()
                            await download_note(on_done=lambda: dl_btn_ref["btn"].enable())

                        dl_btn_ref["btn"] = ui.button(
                            f"下载全部图片 ({note_info['image_count']} 张)",
                            on_click=do_note_download,
                        ).props("color=positive push")

                return

            # 常规视频分析
            info = await extract_info(urls[0], cookie)
            analysis_result["info"] = info
            analysis_result["is_note"] = False

            info_card.clear()
            with info_card:
                with ui.row().classes("w-full gap-4"):
                    if info.get("thumbnail"):
                        ui.image(info["thumbnail"]).classes("w-48 rounded")
                    with ui.column().classes("flex-1"):
                        ui.label(info["title"]).classes("text-h6")
                        if info.get("duration"):
                            duration = int(info["duration"])
                            ui.label(f"时长: {duration // 60}:{duration % 60:02d}")
                        if len(urls) > 1:
                            ui.label(f"批量模式：共 {len(urls)} 个链接").classes("text-caption")

            # 显示格式选择
            format_card.classes(remove="hidden")
            format_card.clear()

            has_ffmpeg = check_ffmpeg()
            formats = info["formats"]

            # 推荐格式生成
            def get_suggested() -> list[dict]:
                video_only = [f for f in formats if f["vcodec"] != "none" and f["acodec"] == "none"]
                audio_only = [f for f in formats if f["vcodec"] == "none" and f["acodec"] != "none"]
                combined = [f for f in formats if f["vcodec"] != "none" and f["acodec"] != "none"]

                def _height(f):
                    r = f.get("resolution", "")
                    if r and "x" in r:
                        try:
                            return int(r.split("x")[-1])
                        except ValueError:
                            return 0
                    return 0

                suggested = []
                if has_ffmpeg and video_only:
                    video_only.sort(key=lambda f: (_height(f), f.get("filesize", 0)), reverse=True)
                    best_audio = (
                        max(audio_only, key=lambda f: f.get("filesize", 0)) if audio_only else None
                    )
                    seen = set()
                    for v in video_only:
                        h = _height(v)
                        if h in seen or h <= 0:
                            continue
                        seen.add(h)
                        if best_audio:
                            suggested.append(
                                {
                                    "label": f"{h}p",
                                    "format_id": f"{v['format_id']}+{best_audio['format_id']}",
                                    "ext": "mp4",
                                    "filesize": v.get("filesize", 0)
                                    + best_audio.get("filesize", 0),
                                    "vcodec": v["vcodec"],
                                    "acodec": best_audio["acodec"],
                                }
                            )
                    if audio_only:
                        best_a = max(audio_only, key=lambda f: f.get("filesize", 0))
                        suggested.append(
                            {
                                "label": "仅音频",
                                "format_id": best_a["format_id"],
                                "ext": best_a["ext"],
                                "filesize": best_a.get("filesize", 0),
                                "vcodec": "none",
                                "acodec": best_a["acodec"],
                            }
                        )
                else:
                    combined.sort(key=lambda f: (_height(f), f.get("filesize", 0)), reverse=True)
                    seen = set()
                    for f in combined:
                        h = _height(f)
                        if h in seen:
                            continue
                        seen.add(h)
                        suggested.append(
                            {
                                "label": f"{h}p" if h > 0 else f["resolution"],
                                "format_id": f["format_id"],
                                "ext": f["ext"],
                                "filesize": f.get("filesize", 0),
                                "vcodec": f["vcodec"],
                                "acodec": f["acodec"],
                            }
                        )
                    for a in audio_only:
                        suggested.append(
                            {
                                "label": "仅音频",
                                "format_id": a["format_id"],
                                "ext": a["ext"],
                                "filesize": a.get("filesize", 0),
                                "vcodec": "none",
                                "acodec": a["acodec"],
                            }
                        )
                return suggested

            suggested = get_suggested()

            with format_card:
                ui.label("选择下载格式").classes("text-h6 mb-2")
                if not has_ffmpeg:
                    ui.label("⚠️ 未检测到 ffmpeg，格式合并功能不可用").classes("text-orange mb-2")

                selected_formats: list[dict] = []

                # ---- 推荐格式卡片 ----
                cards_container = ui.row().classes("w-full gap-3 flex-wrap")

                def render_cards() -> None:
                    cards_container.clear()
                    selected_formats.clear()
                    with cards_container:
                        for fmt in suggested:
                            card = ui.card().classes(
                                "w-52 p-3 cursor-pointer transition-all "
                                "hover:ring-2 hover:ring-primary"
                            )
                            with card:
                                ui.label(fmt["label"]).classes("text-h6")
                                codec = []
                                if fmt["vcodec"] != "none":
                                    codec.append(fmt["vcodec"].split(".")[0])
                                if fmt["acodec"] != "none":
                                    codec.append(fmt["acodec"].split(".")[0])
                                ui.label(f"{' + '.join(codec)} · {fmt['ext']}").classes(
                                    "text-caption text-grey"
                                )
                                if fmt["filesize"] > 0:
                                    ui.label(f"{fmt['filesize']} MB").classes("text-body2")

                            def _select(e=None, f=fmt, c=card, cont=cards_container):
                                for child in cont:
                                    child.classes(remove="ring-2 ring-primary bg-blue-50")
                                c.classes("ring-2 ring-primary bg-blue-50")
                                selected_formats.clear()
                                selected_formats.append(f)

                            card.on("click", _select)

                render_cards()

                # ---- 折叠 / 展开 ----
                toggle_label = ui.label("更多视频格式 ▼").classes(
                    "text-primary cursor-pointer mt-2 text-body2"
                )

                table_ref: dict = {"table": None}

                def toggle_table() -> None:
                    if table_ref["table"] is None:
                        # 展开表格
                        cards_container.clear()
                        selected_formats.clear()
                        toggle_label.text = "收起 ▲"

                        columns = [
                            {"name": "format_id", "label": "格式ID", "field": "format_id"},
                            {"name": "resolution", "label": "分辨率", "field": "resolution"},
                            {"name": "ext", "label": "格式", "field": "ext"},
                            {"name": "filesize", "label": "大小(MB)", "field": "filesize"},
                            {"name": "vcodec", "label": "视频编码", "field": "vcodec"},
                            {"name": "acodec", "label": "音频编码", "field": "acodec"},
                        ]
                        table_ref["table"] = ui.table(
                            columns=columns,
                            rows=formats,
                            row_key="format_id",
                            selection="single" if not has_ffmpeg else "multiple",
                            pagination=10,
                        ).classes("w-full")
                        if has_ffmpeg:
                            ui.label("提示：选择一个视频+一个音频可自动合并").classes(
                                "text-caption text-grey"
                            )
                    else:
                        # 收起表格
                        table_ref["table"].delete()
                        table_ref["table"] = None
                        toggle_label.text = "更多视频格式 ▼"
                        render_cards()

                toggle_label.on("click", toggle_table)

                # ---- 下载选项 ----
                all_langs = list(
                    dict.fromkeys(
                        (info.get("subtitle_langs") or []) + (info.get("auto_subtitle_langs") or [])
                    )
                )
                default_langs = [lang for lang in all_langs if lang.startswith(("zh", "en"))]

                with ui.row().classes("w-full items-center mt-4 gap-4"):
                    thumb_cb = ui.checkbox("下载封面", value=True)
                    sub_cb = ui.checkbox(
                        "下载字幕",
                        value=bool(all_langs),
                    )
                    sub_select = (
                        ui.select(
                            all_langs,
                            multiple=True,
                            label="选择字幕语言",
                            value=default_langs,
                        )
                        .props("dense outlined use-chips")
                        .classes("w-64")
                    )
                    if not all_langs:
                        sub_cb.disable()
                        sub_cb.set_value(False)
                        sub_select.disable()
                    else:
                        # 联动：勾选字幕时启用选择器
                        sub_select.set_visibility(sub_cb.value)

                        def _toggle_sub_select() -> None:
                            sub_select.set_visibility(sub_cb.value)

                        sub_cb.on("update:model-value", _toggle_sub_select)

                # ---- 下载按钮 ----
                with ui.row().classes("w-full justify-end mt-2"):
                    video_dl_btn_ref: dict = {"btn": None}

                    async def do_download() -> None:
                        if table_ref["table"] is not None:
                            sel = table_ref["table"].selected
                        else:
                            sel = list(selected_formats)

                        # 检测历史记录中是否存在重复
                        urls_to_check = analysis_result["urls"]
                        duplicate_titles: list[str] = []
                        for u in urls_to_check:
                            existing = find_existing_download(u)
                            if existing:
                                duplicate_titles.append(existing.get("title") or u[:60])

                        if duplicate_titles:
                            with ui.dialog() as dialog, ui.card():
                                ui.label("链接已存在于下载记录中").classes("text-h6")
                                for t in duplicate_titles:
                                    ui.label(f"  · {t}").classes("text-body2")
                                with ui.row().classes("w-full justify-end mt-4 gap-2"):
                                    ui.button(
                                        "放弃",
                                        on_click=lambda: dialog.submit("cancel"),
                                    ).props("flat")
                                    ui.button(
                                        "覆盖",
                                        on_click=lambda: dialog.submit("overwrite"),
                                    ).props("color=negative")
                            choice = await dialog
                            if choice != "overwrite":
                                video_dl_btn_ref["btn"].enable()
                                return
                            # 覆盖：删除旧记录
                            for u in urls_to_check:
                                existing = find_existing_download(u)
                                if existing:
                                    delete_download_record(existing["id"])

                        video_dl_btn_ref["btn"].disable()
                        await download(
                            sel,
                            thumb_cb.value,
                            sub_cb.value,
                            sub_select.value,
                            on_done=lambda: video_dl_btn_ref["btn"].enable(),
                        )

                    video_dl_btn_ref["btn"] = ui.button("下载选中格式", on_click=do_download).props(
                        "color=positive push"
                    )

        except Exception as e:
            import traceback

            traceback.print_exc()
            info_card.clear()
            with info_card:
                ui.label(f"分析失败: {e!s}").classes("text-negative")
        finally:
            analyze_btn.enable()

    async def download(
        selected_formats: list[dict],
        write_thumbnail: bool = True,
        write_subtitles: bool = True,
        subtitle_langs: list[str] | None = None,
        on_done: Callable | None = None,
    ) -> None:
        """下载选中的格式"""
        if not selected_formats:
            ui.notify("请选择格式", type="warning")
            return

        urls = analysis_result["urls"]
        if not urls:
            ui.notify("请先分析链接", type="warning")
            return

        # 构建 format_id
        if len(selected_formats) > 1:
            format_id = "+".join([f["format_id"] for f in selected_formats])
        else:
            format_id = selected_formats[0]["format_id"]

        # 开始下载（同源串行、不同源并行）
        info = analysis_result.get("info") or {}
        from pages.history import _download_progress

        for url in urls:
            cookie = get_cookie_for_url(url)
            dl_id = create_download_record(
                url=url,
                title=info.get("title", "Unknown"),
                thumbnail=info.get("thumbnail", ""),
                format_id=format_id,
            )

            def _make_callback(did: int):
                def cb(percent: float, speed: str, eta: str) -> None:
                    _download_progress[did] = {
                        "percent": percent,
                        "speed": speed,
                        "eta": eta,
                    }

                return cb

            await download_queue.enqueue(
                url=url,
                format_id=format_id,
                cookie_file=cookie,
                write_thumbnail=write_thumbnail,
                write_subtitles=write_subtitles,
                subtitle_langs=subtitle_langs,
                progress_callback=_make_callback(dl_id),
                download_id=dl_id,
            )

        if on_done:
            on_done()

        count = len(urls)
        ui.notify(
            f"已添加 {count} 个下载任务，请前往下载历史页面查看进度",
            type="positive",
            multi_line=True,
        )
