# AGENTS.md — VidZap

Python 3.13 视频下载工具（NiceGUI 3.9 + yt-dlp），支持多站点视频、批量下载、Cookie 管理、Douyin 笔记提取、知乎回答/想法/专栏图片下载、豆瓣人物图片下载，含 Android 分享 API（`POST /api/share`）与 Android 原生 App。

> **开发经验、踩坑记录与设计决策的详细说明见 [`doc/DEVELOPMENT.md`](doc/DEVELOPMENT.md)**。
> 本文件只保留无法从源码直接推理的硬约束与跨语言契约。

## 关键命令

```bash
make lint                   # ruff check .
make format                 # ruff format .
make type-check             # mypy .
uv run pytest tests/ -v     # 全量测试（-k test_name 单个）
docker compose build / up -d
uv lock                     # pyproject.toml 变动后必须执行，pyproject.toml 与 uv.lock 成对提交
```

## 环境变量

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `NICEVID_DATA_DIR` | `data` | SQLite + NiceGUI 存储路径 |
| `NICEVID_STORAGE_SECRET` | 硬编码默认值 | session 加密密钥 |
| `NICEVID_RELOAD` | `false` | 热重载（仅开发） |
| `NICEVID_PORT` / `NICEVID_HOST` | `8080` / `0.0.0.0` | 监听地址 |
| `VIDZAP_COOKIE_DIR` | `{NICEVID_DATA_DIR}/cookies` | Cookie 文件存储目录（默认跟随 `NICEVID_DATA_DIR`） |
| `VIDZAP_BROWSER` | `playwright` | 浏览器引擎（目前仅 playwright 已实现） |

## 硬约束

### 异步并发（NiceGUI UI 上下文）

- **禁止裸 `asyncio.create_task()` / `asyncio.ensure_future()`**，必须用 `background_tasks.create(coro())`（NiceGUI 官方禁止，GC 会取消任务且异常静默丢失）。仅独立基础设施模块（如 `DownloadQueue`）可用 `asyncio.ensure_future()`。
- **禁止 `run_in_executor`**：用 `await run.io_bound(sync_fn, ...)` / `run.cpu_bound(...)`；超时用 `await asyncio.wait_for(..., timeout=...)`（视频提取 `extract_info()` 内部 60s；分享 API 各类型超时：视频/知乎 30s、douyin_note/douban_photo 60s；home.py 的 douyin/zhihu/豆瓣提取无外层 wait_for，靠 httpx timeout=30 / Playwright 内部超时兜底）。
- **background task 内禁止访问 `ui.context.client`**（无 slot context 会抛 RuntimeError）：在 `background_tasks.create()` 之前捕获 `client = ui.context.client`，闭包内用 `getattr(client, "_deleted", False)` 检查（I/O 前后双守卫）。**`ui.notify` 内部也依赖 `context.client`，同样不可用**——需要 notify/建 UI 时用 `with container_element:` 显式进入目标 slot（实例：`settings.py:_run_cookie_test`）。
- Timer 回调内先查 `ui.context.client._deleted` 再操作 UI。
- Dialog 用 await 模式（`with ui.dialog() as dialog, ui.card():` + `result = await dialog` + `dialog.submit(value)`），**禁止 `dialog.on_submit`**。
- 模块级变量在所有用户/标签页间共享（单进程），需要隔离用 `app.storage.user` 或 `@ui.page` 内局部变量（`history._download_progress` 是故意的全局进度缓存）。

### 下载队列

- **所有下载必须经 `download_queue.enqueue()`**，禁止直接调用 `start_download` / `download_note_images` / `download_zhihu_images` / `download_douban_images`。
- 重试（`history._retry_download`）必须传 `progress_callback` 保证历史页进度更新；重试不传 `note_info`（各下载函数自动回退提取）。
- **`note_info` 预提取链路**：analyze 结果存 `analysis_result["urls_info"]`（per-URL dict）→ `download_note()` / `download_zhihu()` / `download_douban()` 取出 → `enqueue(note_info=...)` → `_worker()` 透传给 `download_note_images()` / `download_zhihu_images()` / `download_douban_images()`，跳过二次提取。新增下载入口无预提取数据时传 `note_info=None` 走自动回退。
- `file_path` 对 douyin_note / zhihu_image / douban_photo 是**目录**（不是文件）；`/downloads-file/{id}/{filename}` 内部按 `is_dir()` 分支解析目录内文件。
- 取消：`DownloadQueue.cancel(download_id)` 置位 `asyncio.Event`，下载函数检查后抛 `DownloadCancelledError` 并清理部分文件。

### 错误处理

- 取消类异常必须透传：`except DownloadCancelledError: raise`；`_CancelledError` 在下载函数内转 `DownloadCancelledError`。
- `_CancelledError` 统一在 `core/browser_extractor.py` 定义，供 `douyin_note.py` / `zhihu_answer.py` / `douban_photo.py` 导入。
- `_download_sync`（ytdlp_handler.py）为**条件降级链**（非逐级无条件）：原始 →（字幕错误）去字幕 →（格式错误）去格式 → 去 cookie → 去 cookie+格式 → 最后手段（去 format+字幕）。`_extract_sync` 提取失败时自动去 cookie 重试。
- `logger.exception()` 记录非预期异常完整 traceback。

### Cookie 管理（cookie_manager.py）

- 目录经 `get_cookie_dir()` 获取（`VIDZAP_COOKIE_DIR` 优先，否则 `{NICEVID_DATA_DIR}/cookies`）。
- DB 中 `cookie_file` 存相对路径 `{domain}.txt`，读取经 `_resolve_cookie_path()` 拼绝对路径。
- 域名规范化：`normalize_domain()`（小写、去端口/www）；用户输入经 `extract_domain_from_input()` + `is_valid_domain()` 校验。
- **`save_cookie()` 返回 `False` 表示内容无法解析为 Netscape/原始格式，调用方必须检查并提示用户**。通用原则：返回 `bool` 的自定义函数调用方不得忽略返回值。
- `get_cookie(domain)` 返回单条记录 + `content`（文件内容，用于修改对话框预填；文件缺失时为空串）。
- **`get_cookie_for_url(url)` 带子域后缀匹配**（精确 domain → 最长子域 → 反向父域），下载/分析流程一律用它取 cookie 文件。
- **过期状态**：`list_cookies_with_expiry()` / `parse_cookie_expiry()` 供设置页展示；Netscape expires=0 或非法值视为会话级。
- **zhihu.com 403 必须抛 `ZhihuAccessError`**（zhihu_answer.py，区分"未配置/已失效"消息）；`verify_cookie(cookie_file)` 请求首页验证（200=有效），设置页保存 zhihu Cookie 后自动验证 + `/settings?test=DOMAIN` 手动验证。
- **zhihu 请求成功后必须 Set-Cookie 回写**：`_fetch_answer_page()` / `verify_cookie()` 2xx 后调用 `_persist_cookie_updates()`（httpx jar → Netscape 写回，`_cookies_to_netscape()`），403 不回写。
- 修改/删除入口：`/settings?edit=DOMAIN`、`/settings?delete=DOMAIN` URL 导航，`settings.render(edit_domain=..., delete_domain=...)` 页面加载后自动弹窗。

### 数据库

- 访问模式 `with get_connection() as conn:`（返回 `sqlite3.Row`）。
- 表：`cookies`（domain UNIQUE）、`downloads`。
- 迁移必须幂等：`init_db()` 内 `ALTER TABLE ... ADD COLUMN` 包 try/except。
- 测试隔离：`conftest.py` 的 `_temp_db_dir`（autouse）猴子补丁 `NICEVID_DATA_DIR`；cookie 相关测试 `setup_method()` 需 `init_db()` + `init_cookie_dir()`。

### URL 分类与站点要点

- `home.py:classify_urls()` 返回 `video` / `douyin_note` / `zhihu_answer` / `douban_photo` / `mixed`；analyze 阶段遇混合类型直接报错，不部分处理。`split_existing_urls()` 逐 URL 重复检测，下载弹窗提供"跳过/覆盖/取消"。
- Zhihu：回答（`question/{qid}/answer/{aid}`）、想法（`pin/{id}`）、专栏（`zhuanlan.zhihu.com/p/{id}`）三种 URL 走图片下载（`is_zhihu_image_url()`），httpx 直连无 Playwright。**zhihu.com 页面受 WAF 保护（zh-zse-ck），无有效 Cookie 时 403**，必须先配置 zhihu.com Cookie（`d_c0` 关键）。视频支持已放弃。下载目录 `downloads/zhihu/{kind}_{id}_{title}/`（kind: answer/pin/article）。
- Douyin 笔记需 Playwright（Xvfb 按需启动 + stealth + cookie 注入），httpx 下载时从 cookie_file 提取 name=value 注入。
- 豆瓣人物图片：主页 `douban.com/personage/{pid}/` 或照片列表页 `douban.com/personage/{pid}/photos/`（`is_douban_photo_url()` 两者都识别，主页内部转列表页提取），httpx 直连无 Playwright，task_type=`douban_photo`，下载目录 `downloads/douban/personage_{pid}_{title}/`。**列表页/单页需 douban.com Cookie**（无 Cookie 302 跳转 `sec.douban.com` 安全校验，`DoubanAccessError` 区分未配置/已失效；`verify_cookie()` 以 /mine/ 200 为准，风控页不算有效）。**反爬风控**：高频连续请求会 302 到 `/misc/sorry` 机器人检测页（腾讯云验证码，HTTP 200），`_fetch_page` 必须检测该 URL/HTML 并抛 `DoubanAccessError`（提示稍后再试/降低频率），禁止静默返回空；下载循环与分页间有 `_REQUEST_DELAY=0.3s` 节流。**原图 xl URL 由缩略图路径变换**：`/view/photo/photo/` → `/view/photo/xl/`（等价单页"查看大图"链接，无需签名）；xl 下载 4xx 时兜底抓单页解析 `photo-zoom` href。**图片 CDN（img*.doubanio.com）只接受 douban.com / doubanio.com 来源的 Referer，其他来源（含无 Referer）一律 418**：httpx 下载必须带 `Referer: https://www.douban.com/`；**浏览器预览无法伪造豆瓣 Referer（页面 URL 必被拒），必须走应用内代理 `/douban-image?url=...`**（main.py，服务端带豆瓣 Referer 代抓；SSRF 防护：仅允许 doubanio.com 域名；home.py 分析页缩略图/预览网格与 history.py 缩略图均走代理）。预览点击打开豆瓣单页而非直开 xl（直开 xl 同样 418）。**sec.douban.com 校验页 URL 被粘贴时**：home.py / share API 直接拦截提示，不走 yt-dlp。
- 页面含 Bilibili 图片时必须加 `<meta name="referrer" content="no-referrer">`（`home.py:render()` 已有，CDN 会拦截非 Bilibili Referer）。

### Docker

- 用户 `nicevid`（uid=1000）经 `gosu` 降权；`uv` 固定在 `/usr/local/bin/uv`（`update_ytdlp()` subprocess 依赖）。
- Xvfb 不由 entrypoint 启动，由 `browser_extractor._ensure_xvfb()` 按需启动。
- `PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright`；持久化卷：`downloads/`、`data/`。
- `docker-compose.yml` 固定 `mem_limit: 2g` + `cpus: "4"`（Chromium 峰值 800MB + ffmpeg 易超 1G），勿移除或大幅调高。
- 修改 `Dockerfile` / `.dockerignore` 后执行 `docker compose build --no-cache` 全量验证。

### Android 分享 API（`POST /api/share`，main.py）

- **阶段一** `{"url"}` → `{"status":"analyzed", "type":"video", "title", "thumbnail", "duration", "formats":[{label, format_id, ext, filesize, vcodec, acodec}]}`（`get_suggested_formats()` 按 ffmpeg 可用性精简推荐）。
- **阶段二** `{"url", "format_id"}` → `{"status":"ok", "download_id", "title", "type":"video"}`。
- douyin_note / zhihu_answer / douban_photo：单阶段自动下载，type 分别为 `douyin_note` / `zhihu_image` / `douban_photo`。
- 核心 `_do_share(url, format_id=None)`；无内置认证（LAN 部署），敏感环境用反向代理保护。
- Android App（`android/`，包名 `com.vidzap.share`）：`MainActivity`（启动即跳转设置页）、`ShareHandlerActivity`（`ACTION_SEND`，dialog 主题浮窗，`noHistory` + `excludeFromRecents`）。

## 数据流

```
URL 输入 → classify_urls()（混合类型报错）→ split_existing_urls()（跳过/覆盖/取消）
  ├─ "video" → extract_info()（60s 超时，失败去 cookie 重试）→ 格式选择
  │             → download_queue.enqueue() → _worker() → start_download()
  │             → _download_sync() [条件降级链]（yt-dlp cookiefile）
  ├─ "douyin_note" → extract_note_images()（Playwright+Xvfb，cookie 注入）
  │             → download_queue.enqueue(task_type="douyin_note", note_info=...)
  │             → _worker() → download_note_images()
  │               ├─ note_info 存在 → 跳过 Playwright，直接 httpx 下载（注入 cookie）
  │               └─ note_info 缺失 → extract_note_images() → httpx 下载
  ├─ "zhihu_answer" → extract_zhihu_answer()（httpx，需 zhihu Cookie，回答/想法/专栏）
  │            → download_queue.enqueue(task_type="zhihu_image", note_info=...)
  │            → _worker() → download_zhihu_images()
  │              ├─ note_info 存在 → 直接 httpx 下载
  │              └─ note_info 缺失 → extract_zhihu_answer() → httpx 下载
  └─ "douban_photo" → extract_douban_photos()（httpx，需 douban Cookie，列表页+分页）
             → download_queue.enqueue(task_type="douban_photo", note_info=...)
             → _worker() → download_douban_images()
               ├─ note_info 存在 → 直接 httpx 下载（xl 变换 URL）
               └─ note_info 缺失 → extract_douban_photos() → httpx 下载

Android 分享 API：
  POST /api/share {"url"} → _do_share(url) → classify → extract_info → get_suggested_formats
    → {status:"analyzed", formats:[...]}（App 内 AlertDialog 选择画质）
  POST /api/share {"url","format_id"} → _do_share(url, format_id)
    → create_download_record → download_queue.enqueue()
  douyin_note / zhihu_answer / douban_photo：单阶段自动下载
```

## 详见 doc/DEVELOPMENT.md

- NiceGUI API 经验：原地更新 vs 重建、`response_timeout`、`app.timer` vs `ui.timer`、`ui.query`、`@ui.refreshable`、`app.add_media_files`、`run_method` 等
- 站点踩坑：Bilibili 412 bug（yt-dlp 版本下限原因）、`FFmpegThumbnailsConvertor` when 参数、YouTube 字幕 429、Douyin Xvfb/stealth/批量串行防限流、Zhihu WAF Cookie 细节、Bilibili CDN Referer
- Docker 构建优化（层序 + cache mount）与 2G 资源限制决策
