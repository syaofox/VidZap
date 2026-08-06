## 任务目标
[需求]

## 项目背景
本仓库为 VidZap（Python 3.13 + NiceGUI 3.9 视频下载工具，含 Android 分享 API 与原生 App）。
技术栈、环境变量、硬约束（异步并发、下载队列、Cookie、数据库、Docker、Android 分享 API 等）
见 [`AGENTS.md`](../AGENTS.md)；开发经验与踩坑见 [`doc/DEVELOPMENT.md`](DEVELOPMENT.md)。

## 执行约束
1. **沟通语言**：中文。
2. **前置评估**（回答以下问题后再提交方案）：
   - 项目原本的功能是什么？修改后影响范围？有无副作用？会不会影响性能？会不会导致相关功能受损？有没有过度想象？
   - 最优解？替代方案利弊？
   - 测试计划？（新增/修改哪些 case？）
3. **确认拦截**：方案需详细解释并经我确认，方可实施编码。
4. **代码质量**：
   - Python：发现过时或错误的注释应给予修正。新增/修改函数必须有对应 pytest 用例，
     通过 `make lint` + `make type-check`，最后全量 `uv run pytest tests/ -v` 防回归。
   - NiceGUI / 下载队列 / Cookie 等约定以 `AGENTS.md` 硬约束为准。
5. **后置处理**：
   - 修改完成后，复查所有修改，确保无错漏。
   - 确认无误后，运行 `codebase-memory-mcp_index_repository` 更新知识图。
   - 检查 `AGENTS.md`，修复过时错误，补充（如果有）AI 无法从源码和知识图推断的新硬约束。
6. **不确定时**：务必追问，禁止猜测。
