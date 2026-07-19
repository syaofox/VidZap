## 任务目标
[需求]

## 项目背景
- 语言/框架：Python 3.13, NiceGUI 3.9 (FastAPI-based), SQLite, yt-dlp, httpx, pydantic, Playwright + playwright-stealth
- 包管理：uv
- 质量工具：pytest (pytest-asyncio), mypy (warn_return_any), ruff (E/F/I/N/W/UP, line-length=100)
- 测试：`uv run pytest tests/`（使用 SQLite 文件数据库，无需额外服务）
- 项目路径映射：`src/` 为源码根目录，`tests/` 为测试目录

## 执行约束
1. **沟通语言**：中文。
2. **前置评估**（回答以下几个问题后再提交方案）：
   - 项目原本的功能是什么？修改后影响范围？有无副作用？会不会影响性能？会不会导致相关功能受损？有没有过度想象？
   - 最优解？替代方案利弊？
   - 测试计划？（新增/修改哪些 case？）
3. **确认拦截**：方案需详细解释并经我确认，方可实施编码。
4. **代码质量**：
   - Python：发现过时或者错误的注释，应该给予修正。新增/修改函数必须有对应 pytest 用例，且通过 `ruff check src/` + `MYPYPATH=src mypy src/`。最后全量通过 `pytest tests/ -v`，防止回归。
   - NiceGUI：遵循声明式 UI 模式（`with ui.card():`），线程卸载使用 `run_in_executor`，对话框禁止使用 `dialog.on_submit`。
   - 下载控制：所有下载必须通过 `download_queue.enqueue()`，禁止直接调用 `start_download`。
5. **后置处理**：
   - 修改完成后，复查所有修改，确保无错漏。
   - 确认无误后，运行 `codebase-memory-mcp_index_repository` 更新知识图。
   - 检查 `AGENTS.md`，补充 AI 无法从源码和知识图推断的新硬约束。
6. **硬约束查询**：涉及数据库、异步 I/O、下载队列、Cookie 管理、Douyin 笔记提取等改动时，务必先阅读 `AGENTS.md` 中的 `Key patterns` 章节，遵循既有约定（如重试链逻辑、域名规范化、文件服务路由、Xvfb 环境等）。
7. **不确定时**：务必追问，禁止猜测。
