# AGENTS.md — VidZap

Python 3.13 视频下载工具（NiceGUI + yt-dlp），支持多站点、格式选择、批量下载、Cookie管理、Douyin笔记提取。

## 关键命令

```bash
./start.sh                  # 启动
make lint                   # ruff check .
make format                 # ruff format .
make type-check             # mypy .
uv run pytest tests/        # 测试
make playwright-setup       # 安装 Playwright Chromium（devcontainer）
```

## 非源码可见约束

- **Timer 内必须检查** `ui.context.client._deleted` 再操作 UI；否则已销毁页面崩溃。
- **避免 `ui.timer(0.1, fn, once=True)` 做页面初始化** — 使用 `asyncio.ensure_future(fn())` 替代，消除 Timer 与父 slot 的生命周期绑定，防止 `_get_context` 因 slot 删除抛出 `RuntimeError`。
- **Dialog 不要用** `dialog.on_submit` — 用 `ui.dialog()` + `ui.card()` context manager。
- **不要直接调用** `start_download` / `download_note_images` — 全部通过 `download_queue.enqueue()`。
- 重试时传 `progress_callback`，保证历史页面进度更新。
- `file_path` 对 Douyin notes 是**目录**（不是文件），`/downloads-file/{id}/{filename}` 内部按 `is_dir()` 分支处理。
- 安全：不记录密钥，Cookie 文件 gitignored，配置用环境变量，验证所有用户输入。
- Page 模块导出 `render()` 函数供路由使用。
- Cookie 目录通过 `get_cookie_dir()` 获取（`cookie_manager.py`），由 `VIDZAP_COOKIE_DIR` 环境变量控制，默认 `cookies/`。
- DB 中 `cookie_file` 存相对路径 `{domain}.txt`，读取时通过 `_resolve_cookie_path()` 拼装绝对路径（`cookie_manager.py`）。
- `save_cookie()` 返回 `False` 表示内容无效（无法解析为 Netscape 或原始 Cookie 格式），保存失败。UI 调用处必须检查返回值，返回 `False` 时提示用户而不是显示"保存成功"。
- `_CancelledError` 统一在 `browser_extractor.py` 定义，`douyin_note.py` 从 `browser_extractor` 导入。
- `PlaywrightNoteExtractor.extract()` 自动从 `cookie_file` 解析 Netscape Cookie 并通过 `context.add_cookies()` 注入。
- `download_note_images()` 中 `httpx.AsyncClient` 自动从 `cookie_file` 提取 name=value 对作为默认 Cookie 发送。

## Docker 约束

- Docker 以 `nicevid` (uid=1000) 用户运行，通过 `gosu` 降权。
- `uv` 安装在 `/usr/local/bin/uv`（`update_ytdlp()` 依赖）。
- HEALTHCHECK：`python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/', timeout=5)"`，间隔 30s。
- Xvfb 不由 `entrypoint.sh` 启动，而是由 `browser_extractor._ensure_xvfb()` 在 Douyin 笔记提取时按需启动。
- Playwright Chromium 安装路径：`/app/.cache/ms-playwright`（`PLAYWRIGHT_BROWSERS_PATH`）。
- 数据持久化卷：`downloads/`（下载文件）、`cookies/`（Cookie 文件）、`data/`（SQLite DB + NiceGUI storage）。
- Docker 构建分两阶段：builder 安装依赖 → final 复制 `.venv` + 源码 + Playwright。

## 数据流

```
URL 输入 → extract_info() → 格式选择 → download_queue.enqueue() → _worker()
  ├─ "video" → start_download() → _download_sync() [5级降级重试] (yt-dlp cookiefile)
  └─ "douyin_note" → download_note_images() → NoteExtractor.extract() (Playwright, 注入 cookie)
                                             → httpx 下载 (注入 cookie)
```
