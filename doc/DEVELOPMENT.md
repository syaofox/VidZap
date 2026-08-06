# VidZap 开发经验与踩坑记录

本文件记录 AGENTS.md 中未展开的**经验与踩坑**：为什么这样做、遇到过什么问题、如何规避。
涉及代码行为的硬约束见 [AGENTS.md](../AGENTS.md)。

## NiceGUI 经验

### 为什么 UI 上下文禁止裸 `asyncio.create_task()` / `ensure_future()`
NiceGUI 官方明确禁止在页面构建、事件处理器、timer 回调中使用裸任务创建：
GC 可能取消任务（任务引用不保留时），且异常会静默丢失。UI 代码一律用
`background_tasks.create(coro())`，任务被 NiceGUI 内部跟踪。

独立基础设施模块（`DownloadQueue` 等，不依赖 NiceGUI event loop）不受此限，
`download_queue.py` 的 `enqueue()` 即用 `asyncio.ensure_future()`（源码注释说明了原因）。

### background task 中访问 `ui.context.client` 会抛 RuntimeError
背景协程没有 slot context。必须在 `background_tasks.create()` **之前**捕获引用：
`client = ui.context.client`，闭包内用 `getattr(client, "_deleted", False)` 判断页面是否销毁。
惯例是 I/O 前后各查一次（双守卫），实例见 `home.py` 的 `_load_version`、
`settings.py` 的 `_load_cookies`、`history.py` 的 `_load_and_rebuild`。

### Timer 与已销毁页面
`ui.timer` 绑定到页面，页面销毁后不会自动停。回调里操作 UI 前必须检查
`ui.context.client._deleted`，否则已销毁页面会崩溃（`history.py` 的 `refresh_active` 先检查，
且无活动任务时主动 `deactivate()`）。

### 原地更新优于重建
NiceGUI 没有 virtual DOM diffing：重建元素子树会丢失焦点、滚动位置和动画状态。
优先 `.set_text()` / `.set_value()` / 直接属性赋值（`label.text = ...`）原地更新。

### 模块级变量在所有用户间共享
NiceGUI 是单进程，模块级 `list`/`dict` 会被所有用户和标签页共享。
需要隔离时用 `app.storage.user`（按用户持久化，如 `history.py` 的布局模式）或
`@ui.page` 内的局部变量（如 `home.py` 的 `analysis_result`）。
注意 `history._download_progress` 是**故意的**全局进度缓存——所有页面看到同一份进度。

### `@ui.page(response_timeout=N)`
默认页面构建超时 3 秒。重数据页面（DB 查询、API 调用）应先交付骨架屏，
数据在 `background_tasks.create()` 中异步加载，避免页面构建超时。

### `app.timer` vs `ui.timer`
`app.timer` 是全局非 UI 定时器，不绑定页面，适用于后端周期性任务；
`ui.timer` 是 UI 元素，绑定当前页面，页面销毁后停止。

### 全局样式用 `ui.query()`
Python 优先：`ui.query('.nicegui-content').style('max-width: 1200px; ...')`（本项目在
`main.py` 三个页面路由中调用）。注意 `ui.query()` 必须在 `@ui.page` 函数内调用，
不能在模块顶层作用域执行。

### `ui.on_exception(handler)`
HTML 已发送给浏览器之后发生的异常（按钮点击、timer 回调）会经过此处理器。
本项目每个页面函数开头都注册：
`ui.on_exception(lambda e: ui.notify(f"页面错误: {e}", type="negative"))`。

### 其他 API 备忘（本项目暂未使用）
- `await element.run_method(...)` / `await element.get_computed_prop(...)`：与前端交互取返回值。
- `@ui.refreshable`：局部 UI 刷新，支持参数与 awaitable refresh（历史页当前用手动 rebuild）。
- `app.add_media_files()`：流式服务大文件，支持 Range 请求（本项目用 FastAPI `FileResponse`
  路由 `/downloads-file/{id}/{filename}`，Starlette 同样支持 Range；注意记录 `file_path` 为
  目录时按 `is_dir()` 分支解析目录内文件）。

## 站点踩坑

### yt-dlp
- **Bilibili HTTP 412**：2026.03.17 的 Bilibili extractor 存在 `HTTP 412 Precondition Failed`
  bug，无法提取视频信息和封面。因此 `pyproject.toml` 锁定 `yt-dlp>=2026.7.0`，升级时勿降低。
- **`FFmpegThumbnailsConvertor` 勿设 `when: "before_dl"`**：封面转换时机用默认 `after_dl`，
  否则可能导致转码时序问题。
- **YouTube 字幕 429 限流**：字幕下载易触发限流，`_download_sync` 对字幕类错误自动去字幕重试；
  UI 侧自动生成字幕默认不勾选（`home.py`），手动字幕默认只勾 zh/en。
- **YouTube cookie 反爬**：带 cookie 提取可能失败，`_extract_sync` 失败时自动移除 cookie 重试。
- **批量提取串行化**：批量分析用 `asyncio.Semaphore(1)` 串行执行，避免触发 YouTube 反爬 /
  Douyin 限流。
- **视频信息超时**：提取统一 `asyncio.wait_for(..., timeout=60)`（分享 API 阶段分析 30s）。

### Bilibili CDN 封面 Referer 拦截
`i1.hdslb.com` CDN 检查 `Referer` 头，非 Bilibili 域名（如 `localhost:8080`）返回 403。
`home.py:render()` 已加 `<meta name="referrer" content="no-referrer">` 阻止浏览器发送 Referer。
**新增含 Bilibili 图片的页面必须加同款 meta。**

### Douyin 笔记（browser_extractor.py）
- **必须 Xvfb**：Douyin 有 bot 检测，headless 容易被识别。`_ensure_xvfb()` 按需启动
  （Docker 的 entrypoint 不启动 Xvfb）。
- **伪装**：`playwright-stealth` + 固定 UA / 1280x720 viewport / zh-CN locale / Asia/Shanghai
  timezone / 禁用 AutomationControlled 特性。
- **提取策略**：优先拦截 `aweme` API 的 JSON 响应（`page.on("response")`），失败回退 DOM 提取
  （`img[src*="tplv-dy-aweme-images"]` + background-image + data-src + srcset）。
- **httpx 下载注入 cookie**：从 cookie_file 解析 Netscape 格式提取 name=value 对作为默认 Cookie，
  并带 `Referer: https://www.douyin.com/`。

### Zhihu（zhihu_answer.py）
- **WAF 保护（zh-zse-ck）**：zhihu.com 与 zhuanlan.zhihu.com 页面无有效 Cookie 时 httpx 返回 403。
  必须先配置 zhihu.com 的 Cookie：`d_c0` 为关键，`_xsrf` / `z_c0` / `__zse_ck` 一并带上最稳。
- **专栏 SSR 无标题**：zhuanlan 页面 SSR 无 `<title>` / og:title，标题回退"知乎专栏 {id}"；
  `_extract_title` 支持 `/answer/`、`/pin/`、`/p/` 三种回退。
- **提取优先级**：1) `data-actual` / `data-original` 属性（原图地址） 2)
  `js-initialData` / `__NEXT_DATA_INIT__` JSON（含 `window.__INITIAL_STATE__` 经典格式）
  3) `<img src>` 兜底。
- **原图归一化与去重**：`_normalize_image_url()` 去除 `/80/v2-xxx.jpg` 缩略图尺寸前缀；
  同一张图跨 CDN 子域名 / 扩展名出现时，用 `v2-{hash}` 去重。
- **视频已放弃**：yt-dlp 不支持 answer URL 格式（只支持 zvideo/{id}），静态 HTML 无法可靠提取视频。

## Docker 构建经验

- **两阶段构建**：builder 装依赖 → final 复制 `.venv` + 源码 + Playwright。依赖不变时，
  代码改动只 invalidate 源码层，不触发依赖重装。
- **2 个 cache mount**：`/root/.cache/uv`（uv 包下载）、`/var/cache/apt` + `/var/lib/apt/lists`
  （apt 包）。修改依赖不必从零下载。
- **层序按变更频率**：用户创建 → `pyproject.toml`/`entrypoint.sh` → `playwright install` →
  `COPY src/`（最后）。
- 修改 `Dockerfile` 或 `.dockerignore` 后执行 `docker compose build --no-cache` 全量验证一次。
- **资源限制决策**：`mem_limit: 2g` + `cpus: "4"`。Playwright/Chromium 内存峰值可达 800MB，
  加上 ffmpeg 转码很容易超过 1G，2G 保证功能可用不频繁 OOM；NAS 共享环境防单容器拖垮整机。
  **不要移除或大幅调高此限制。**

## 其他

- **`pyproject.toml` 与 `uv.lock` 必须成对提交**：`uv sync --frozen`（Docker builder 与 CI）依赖
  两者一致，改 pyproject.toml（含版本号）后必须 `uv lock`。
- **更新 yt-dlp**：`update_ytdlp()` 通过 subprocess 调 `uv pip install --upgrade yt-dlp`，
  Docker 内 `uv` 固定在 `/usr/local/bin/uv`（勿移动）。
