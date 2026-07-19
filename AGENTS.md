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
- **Dialog 不要用** `dialog.on_submit` — 用 `ui.dialog()` + `ui.card()` context manager。
- **不要直接调用** `start_download` / `download_note_images` — 全部通过 `download_queue.enqueue()`。
- 重试时传 `progress_callback`，保证历史页面进度更新。
- `file_path` 对 Douyin notes 是**目录**（不是文件），`/downloads-file/{id}/{filename}` 内部按 `is_dir()` 分支处理。
- 安全：不记录密钥，Cookie 文件 gitignored，配置用环境变量，验证所有用户输入。
- Page 模块导出 `render()` 函数供路由使用。
- Cookie 目录通过 `get_cookie_dir()` 获取（`cookie_manager.py`），由 `VIDZAP_COOKIE_DIR` 环境变量控制，默认 `cookies/`。
- DB 中 `cookie_file` 存相对路径 `{domain}.txt`，读取时通过 `_resolve_cookie_path()` 拼装绝对路径（`cookie_manager.py`）。
- `save_cookie()` 返回 `False` 表示内容无效（无法解析为 Netscape 或原始 Cookie 格式），保存失败。
- `_CancelledError` 统一在 `browser_extractor.py` 定义，`douyin_note.py` 从 `browser_extractor` 导入。
- `PlaywrightNoteExtractor.extract()` 自动从 `cookie_file` 解析 Netscape Cookie 并通过 `context.add_cookies()` 注入。
- `download_note_images()` 中 `httpx.AsyncClient` 自动从 `cookie_file` 提取 name=value 对作为默认 Cookie 发送。

## 数据流

```
URL 输入 → extract_info() → 格式选择 → download_queue.enqueue() → _worker()
  ├─ "video" → start_download() → _download_sync() [5级降级重试] (yt-dlp cookiefile)
  └─ "douyin_note" → download_note_images() → NoteExtractor.extract() (Playwright, 注入 cookie)
                                             → httpx 下载 (注入 cookie)
```
