# AGENTS.md — VidZap

Python 3.13 视频下载工具（NiceGUI + yt-dlp），支持多站点、格式选择、批量下载、Cookie管理、Douyin笔记提取。

## 关键命令

```bash
make lint                   # ruff check .
make format                 # ruff format .
make type-check             # mypy .
uv run pytest tests/        # 全量测试
uv run pytest tests/ -v -k test_name  # 单个测试
docker compose build        # Docker 构建
docker compose up -d        # Docker 启动
uv lock                     # pyproject.toml 变更后同步 uv.lock
```

## 代码约定

### 命名
- 函数/变量: `snake_case`，私有加 `_` 前缀（如 `_download_sync`）
- 类: `PascalCase`（如 `DownloadCancelledError`，`PlaywrightNoteExtractor`）
- 模块级常量: `UPPER_SNAKE_CASE`
- Test 类: `TestPascalCase`，方法: `snake_case`

### 导入
- 绝对导入优先: `from core.db import get_connection`
- 循环引用用惰性导入: 在函数体内 `from core.ytdlp_handler import start_download`
- 类型注解用 `collections.abc`（`Callable`，`Iterator`），不用 `typing.*`
- Union 用 `X | None` 语法（Python 3.10+）

### 类型注解
- 所有 `async def` 和公开函数必须有返回类型注解
- mypy: `warn_return_any = true`，`disallow_untyped_defs = false`

### 错误处理
- 自定义异常: `DownloadCancelledError`（`ytdlp_handler.py`），`_CancelledError`（`browser_extractor.py`，供 `douyin_note.py` 导入）
- 取消异常必须透传: `except DownloadCancelledError: raise`
- 下载降级重试（`_download_sync`）: 原始 → 去字幕 → 去格式 → 去 cookie → 去 cookie+格式 — 逐级回退
- 日志: `logger.exception()` 记录非预期异常的完整 traceback

### 数据库（SQLite）
- 访问模式: `with get_connection() as conn:`，返回 `sqlite3.Row`
- 表: `cookies`（domain UNIQUE），`downloads`（url/title/status/file_path 等）
- 迁移: `init_db()` 用 `ALTER TABLE ... ADD COLUMN` 包 try/except，幂等
- 测试: `conftest.py` 的 `_temp_db_dir` fixture 自动隔离每个测试的数据库

### 测试约定
- 类容器: `class TestFeatureName:`，方法为测试
- 异步测试加 `@pytest.mark.asyncio`，使用 `pytest-asyncio`
- Mocking: `monkeypatch.setattr` + `unittest.mock.patch`，避免 mock 整个模块
- `@pytest.mark.parametrize` 用于数据驱动测试
- DB 隔离: `_temp_db_dir`（autouse fixture）猴子补丁 `NICEVID_DATA_DIR`
- `setup_method()` 内调用 `init_db()` + `init_cookie_dir()`

### 异步并发
- UI 上下文: 用 `background_tasks.create(coro())`，禁止用 `asyncio.create_task()`
- 基础设施模块: 可用 `asyncio.ensure_future()`（如 `DownloadQueue.enqueue()`）
- 并发控制: `asyncio.Semaphore(1)` 序列化提取防限流，`asyncio.Event` 做取消信号，`asyncio.Lock` 保护共享状态
- I/O 卸载: `await run.io_bound(sync_fn, args...)`，不用 `run_in_executor`
- 超时: `await asyncio.wait_for(run.io_bound(...), timeout=60)`

### 页面模式
- 每个页面模块导出 `def render() -> None`
- 函数开头设异常处理: `ui.on_exception(lambda e: ui.notify(f"页面错误: {e}", type="negative"))`
- 重数据加载: 先用骨架屏交付，`background_tasks.create()` 中异步加载
- 异步加载中定期检查 `getattr(_client, "_deleted", False)`（双守卫模式）

### 环境变量

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `NICEVID_DATA_DIR` | `data` | SQLite + NiceGUI 存储路径 |
| `NICEVID_STORAGE_SECRET` | 硬编码默认值 | session 加密密钥 |
| `NICEVID_RELOAD` | `false` | 热重载（仅开发） |
| `NICEVID_PORT` / `NICEVID_HOST` | `8080` / `0.0.0.0` | 监听地址 |
| `VIDZAP_COOKIE_DIR` | `{NICEVID_DATA_DIR}/cookies` | Cookie 文件存储目录（默认跟随 `NICEVID_DATA_DIR`） |
| `VIDZAP_BROWSER` | `playwright` | 浏览器引擎（也接受 `cloakbrowser`） |

## 非源码可见约束

### NiceGUI 通用规则

- **`background_tasks.create()` 替代裸 `asyncio.create_task/ensure_future`** — NiceGUI 官方明确禁止在 UI 上下文（页面构建、事件处理器、timer 回调）中使用 `asyncio.create_task()` 或 `asyncio.ensure_future()`，因为 GC 可能取消任务且异常静默丢失。UI 代码一律使用 `background_tasks.create(coro())`。独立基础设施模块（下载队列、core services 等）不需要 NiceGUI event loop，可继续使用 `asyncio.ensure_future()`。
- **background task 中不能访问 `ui.context.client`** — 背景协程没有 slot context，访问 `ui.context.client` 会抛出 `RuntimeError`。必须在 `background_tasks.create()` 之前捕获 client 引用：`client = ui.context.client`，然后在闭包内使用 `getattr(client, "_deleted", False)` 检查页面是否销毁。
- **`run.io_bound()` / `run.cpu_bound()` 替代 `run_in_executor`** — I/O 密集型卸载用 `await run.io_bound(sync_fn, args...)`，CPU 密集型用 `await run.cpu_bound(sync_fn, args...)`。它们内部使用线程/进程池，且与 NiceGUI 异常处理集成更好。
- **Timer 内必须检查** `ui.context.client._deleted` 再操作 UI；否则已销毁页面崩溃。
- **Dialog 用 await 模式替代** `dialog.on_submit` — 用 `with ui.dialog() as dialog, ui.card():` 创建，`result = await dialog` 等待结果，`dialog.submit(value)` 提交。
- **优先使用 `.set_text()` / `.set_value()` / bindings 原地更新**，而不是重建元素及其子树。重建会丢失焦点、滚动位置和动画状态（NiceGUI 没有 virtual DOM diffing）。
- **模块级变量是所有用户共享的** — NiceGUI 是单进程，模块级 `list`/`dict` 在所有用户和标签页间共享。需要隔离时使用 `app.storage.user`（按用户持久化）或 `@ui.page` 内的局部变量。
- **`@ui.page(response_timeout=N)` 控制页面构建超时** — 默认 3 秒，如果页面构建耗时较长（DB 查询、API 调用等），应在页面函数内通过 `background_tasks.create()` 异步加载，骨架 UI 立刻交付。
- **`app.timer` 用于全局非 UI 定时器** — 不绑定到任何页面上下文，适用于后端周期性任务。`ui.timer` 是 UI 元素，绑定到当前页面，页面销毁后停止。
- **`ui.query('body')` 进行全局样式** — Python 优先，不要用 `ui.add_head_html('<style>...</style>')` 做页面级样式，改用 `ui.query('body').classes('bg-grey-2')`。注意 `ui.query()` 必须在 `@ui.page` 函数内调用，不能在模块顶层作用域执行。
- **`ui.on_exception(handler)` 注册页内异常处理** — 在 HTML 已发送给浏览器后发生的异常（按钮点击、timer 回调）会经过此处理器，可用来显示错误通知或对话框。
- **`result = await element.run_method(...)` / `await element.get_computed_prop(...)`** 可与前端交互获取返回值。
- **`@ui.refreshable` 用于局部 UI 重建** — 对需要定期刷新的 UI 片段使用，支持 awaitable refresh，支持参数传递。
- **`app.add_media_files()` 流式服务下载文件** — 对于视频/音频等大文件使用 `add_media_files()` 而非 `add_static_files()`，以支持 Range 请求和流式传输。

### 项目专用规则

- **所有下载必须通过 `download_queue.enqueue()`**，禁止直接调用 `start_download` / `download_note_images`。
- 重试时传 `progress_callback`，保证历史页面进度更新。
- `file_path` 对 Douyin notes 是**目录**（不是文件），`/downloads-file/{id}/{filename}` 内部按 `is_dir()` 分支处理。
- 安全：不记录密钥，Cookie 文件 gitignored，配置用环境变量，验证所有用户输入。
- Page 模块导出 `render()` 函数供路由使用。
- Cookie 目录通过 `get_cookie_dir()` 获取（`cookie_manager.py`），由 `VIDZAP_COOKIE_DIR` 环境变量控制，未设置时从 `NICEVID_DATA_DIR` 派生默认为 `{NICEVID_DATA_DIR}/cookies`。
- DB 中 `cookie_file` 存相对路径 `{domain}.txt`，读取时通过 `_resolve_cookie_path()` 拼装绝对路径（`cookie_manager.py`）。
- `save_cookie()` 返回 `False` 表示内容无效（无法解析为 Netscape 或原始 Cookie 格式），保存失败。UI 调用处必须检查返回值，返回 `False` 时提示用户而不是显示"保存成功"。
- `get_cookie(domain)` 返回单条 Cookie 记录（含 `content` 字段，即文件内容），用于修改对话框预填。文件不存在时 `content` 为空字符串。
- Cookie 修改通过 `/settings?edit=DOMAIN` URL 导航 + 页面自动弹窗实现。`settings.render(edit_domain)` 在页面加载后自动打开修改对话框。
- `_CancelledError` 统一在 `browser_extractor.py` 定义，`douyin_note.py` 从 `browser_extractor` 导入。
- **`note_info` 预提取优化**：`download_note_images()` 接受可选参数 `note_info: dict | None`，当已分析过时跳过二次 Playwright 提取。`home.py:download_note()` 从 `analysis_result["urls_info"]`（per-URL dict）取出后经 `DownloadTask.note_info` → `_worker()` 透传。**单链接和批量模式均会传 per-URL note_info**（批量模式在 analyze 阶段已并发提取所有 URL）。新增下载入口若没有预提取数据，传 `note_info=None` 即可走自动提取回退。
- `PlaywrightNoteExtractor.extract()` 自动从 `cookie_file` 解析 Netscape Cookie 并通过 `context.add_cookies()` 注入。
- `download_note_images()` 中 `httpx.AsyncClient` 自动从 `cookie_file` 提取 name=value 对作为默认 Cookie 发送。
- **`classify_urls(urls)` 和 `split_existing_urls(urls)`** 是 `home.py` 的模块级函数，分别用于 URL 类型分类（video/douyin_note/mixed）和重复检测（返回新 URL 列表和已有记录列表）。可直接导入测试。
- **批量下载 URL 类型一致性**：analyze 阶段调用 `classify_urls()` 检查所有 URL 类型，混合类型直接报错返回，不会部分处理。
- **重复检测逐 URL**：`do_download()` / `do_note_download()` 使用 `split_existing_urls()` 区分新 URL 和已存在的 URL。弹窗提供"跳过/覆盖/取消"三个选项，不再全有全无。
- **`batch_download()` 已从 `ytdlp_handler.py` 删除**（死代码，从未被调用）。批量下载统一走 `download_queue.enqueue()` 逐 URL。
- **`download()` 和 `download_note()` 签名变更**：第一个参数改为 `urls: list[str]`，由调用方传入待下载 URL 列表（可能是过滤后的子集）。
- **`_DEFAULT_HTTP_HEADERS` 已移除**（曾被用于应对 Bilibili 412，但自定义 UA 干扰 YouTube 的格式协商和登录态检测，现已完全删除。yt-dlp 使用默认头。）
- **`pyproject.toml` 变动必须同步 `uv.lock`** — 修改 `pyproject.toml`（含版本号）后执行 `uv lock` 生成新 `uv.lock`。提交时 `pyproject.toml` 和 `uv.lock` 必须成对提交，否则 `uv sync --frozen` 会失败。

## Docker 约束

- Docker 以 `nicevid` (uid=1000) 用户运行，通过 `gosu` 降权。
- `uv` 安装在 `/usr/local/bin/uv`（`update_ytdlp()` 依赖）。
- HEALTHCHECK：`python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=5)"`，间隔 30s。
- Xvfb 不由 `entrypoint.sh` 启动，而是由 `browser_extractor._ensure_xvfb()` 在 Douyin 笔记提取时按需启动。
- Playwright Chromium 安装路径：`/app/.cache/ms-playwright`（`PLAYWRIGHT_BROWSERS_PATH`）。
- 数据持久化卷：`downloads/`（下载文件）、`data/`（SQLite DB + NiceGUI storage + Cookie 文件）。
- Docker 构建分两阶段：builder 安装依赖 → final 复制 `.venv` + 源码 + Playwright。
- **构建优化（层序 + cache mount）**：
  - Dockerfile 使用 3 个 `--mount=type=cache`：uv 包下载（`/root/.cache/uv`）、apt 包（`/var/cache/apt` + `/var/lib/apt/lists`）、Playwright Chromium 二进制（`/app/.cache/ms-playwright`）。修改依赖时不必从零下载。
  - 层序按变更频率排列：用户创建 → `pyproject.toml`/`entrypoint.sh` → playwright install → `COPY src/`（最后）。代码变更只 invalidate 源码层，不触发依赖重装。
  - 修改 `Dockerfile` 或 `.dockerignore` 后执行 `docker compose build --no-cache` 全量验证一次。
- 资源限制：`docker-compose.yml` 已配置 `mem_limit: 2g` + `cpus: "4"`。Playwright/Chromium 内存峰值可达 800MB，加上 ffmpeg 转码很容易超过 1G，2G 保证功能可用不频繁 OOM。NAS 环境不要移除或大幅调高此限制。

## 数据流

```
URL 输入 → extract_info() → 格式选择 → download_queue.enqueue() → _worker()
  ├─ "video" → start_download() → _download_sync() [5级降级重试] (yt-dlp cookiefile)
  └─ "douyin_note" → download_note_images()
                       ├─ note_info 已存在 → 跳过 Playwright，直接 httpx 下载 (注入 cookie)
                       └─ note_info 不存在 → NoteExtractor.extract() (Playwright, 注入 cookie)
                                             → httpx 下载 (注入 cookie)
```
