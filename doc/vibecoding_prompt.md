## 任务目标
[需求]

## 项目背景
- 语言/框架：Python 3.13, NiceGUI 3.9 (FastAPI-based), SQLite, yt-dlp, httpx, pydantic, Playwright + playwright-stealth, python-multipart
- Android 分享 API：`POST /api/share` 两阶段流程（分析 → 用户选画质 → 入队下载），见 `src/main.py`
- Android 原生 App：`android/` 目录，Kotlin + Gradle，`ACTION_SEND` intent filter 接收系统分享
- 包管理：uv（`pyproject.toml` 变动后必须 `uv lock` 同步 `uv.lock`）
- 质量工具：pytest (pytest-asyncio), mypy (warn_return_any), ruff (E/F/I/N/W/UP, line-length=100)
- 命令：`make lint` / `make format` / `make type-check` / `uv run pytest tests/`
- 测试：使用 SQLite 文件数据库，无需额外服务
- Docker：两阶段构建 + cache mount，`docker compose build` / `docker compose up -d`

## 执行约束
1. **沟通语言**：中文。
2. **前置评估**（回答以下几个问题后再提交方案）：
   - 项目原本的功能是什么？修改后影响范围？有无副作用？会不会影响性能？会不会导致相关功能受损？有没有过度想象？
   - 最优解？替代方案利弊？
   - 测试计划？（新增/修改哪些 case？）
3. **确认拦截**：方案需详细解释并经我确认，方可实施编码。
4. **代码质量**：
   - Python：发现过时或者错误的注释，应该给予修正。新增/修改函数必须有对应 pytest 用例，且通过 `make lint` + `make type-check`。最后全量通过 `uv run pytest tests/ -v`，防止回归。
   - NiceGUI：遵循声明式 UI 模式（`with ui.card():`），I/O 卸载使用 `run.io_bound()` / `run.cpu_bound()`（禁止 `run_in_executor`），UI 异步用 `background_tasks.create(coro())`（禁止 `asyncio.create_task`），对话框用 await 模式（禁止 `dialog.on_submit`）。
   - 下载控制：所有下载必须通过 `download_queue.enqueue()`，禁止直接调用 `start_download` / `download_note_images`。
   - Cookie：`save_cookie()` 返回值必须检查（False 表示内容无效，需提示用户）。
5. **后置处理**：
   - 修改完成后，复查所有修改，确保无错漏。
   - 确认无误后，运行 `codebase-memory-mcp_index_repository` 更新知识图。
   - 检查 `AGENTS.md`，修复过时的错误，同时补充（如果有） AI 无法从源码和知识图推断的新硬约束。
6. **硬约束查询**：涉及以下任一改动时，务必通读 `AGENTS.md` 全文，遵循既有约定：
   - 数据库（SQLite Schema、迁移模式）
   - 异步 I/O（`background_tasks.create` vs `asyncio.ensure_future` 适用范围）
   - 下载队列（重试链逻辑、`note_info` 预提取透传、`progress_callback`）
   - Cookie 管理（`save_cookie` 返回值、`get_cookie(domain)` 签名、域名规范化）
   - Douyin 笔记提取（Playwright Xvfb、httpx cookie 注入、`_CancelledError` 统一导入）
   - Android 分享 API（两阶段流程、推荐格式 `get_suggested_formats`）
   - Docker 构建（层序、cache mount、`uv lock` 同步约束）
7. **不确定时**：务必追问，禁止猜测。

