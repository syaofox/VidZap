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
背景协程没有 slot context（`Slot.stacks` 按 asyncio task 隔离，background task 的 stack 为空）。
必须在 `background_tasks.create()` **之前**捕获引用：
`client = ui.context.client`，闭包内用 `getattr(client, "_deleted", False)` 判断页面是否销毁。
惯例是 I/O 前后各查一次（双守卫），实例见 `home.py` 的 `_load_version`、
`settings.py` 的 `_load_cookies`、`history.py` 的 `_load_and_rebuild`。

**`ui.notify` 在 background task 内同样不可用**：它内部访问 `context.client`（notify.py），
slot stack 为空时抛同样的 RuntimeError。需要 notify / 创建 UI 时必须用
`with container_element:` 显式进入目标 slot 再调用（`Slot.__enter__` 会把自己的 slot 压入
当前 task 的 stack）。实例：`settings.py` 的 `_run_cookie_test`（background_tasks.create 时
传入预先捕获的 client，notify 前 `with cookie_table_container:`）。

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
- **视频信息超时**：`ytdlp_handler.extract_info()` 内部统一 `asyncio.wait_for(..., timeout=60)`（分享 API 阶段分析 30s，main.py 外层 wait_for）。注意：**home.py 的 douyin/zhihu 提取没有外层 wait_for 兜底**，靠 httpx timeout=30 / Playwright page.goto 30s 内部超时，卡死时只能由用户重新点击。

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
- **Cookie 失效快的原因（踩坑结论）**：① 知乎风控检测"异地 IP + UA 指纹不符 + 无 JS 行为"后快速吊销
  `z_c0`/`d_c0`（几小时～几天）；② `__zse_ck` 由 zse-96 JS 算法动态计算并与 `d_c0` 绑定，静态导出无法续签；
  ③ 浏览器导出的会话级 cookie（Netscape expires=0）导出后很快失效。**运维提示：失效快主要是知乎侧机制，
  只能靠重新导出缓解；项目侧已做检测、提示与 Set-Cookie 回写（见下）。**
- **Set-Cookie 自动回写**：`_fetch_answer_page()` / `verify_cookie()` 请求成功（2xx）后调用
  `_persist_cookie_updates()`，把 httpx cookie jar（含服务端刷新）序列化回写 Netscape cookie 文件
  （`_cookies_to_netscape()`：跳过过期/空值，同名并存时优先保留服务端下发条目）。403 不回写（防误删）。
  可延长静态 cookie 有效期，但 `__zse_ck` 等需 JS 生成的签名仍会过期。
- **403 必须抛 `ZhihuAccessError`**：`_fetch_answer_page` 遇 403 抛 `ZhihuAccessError`（消息区分"未配置"与
  "已失效/自动刷新后仍失效"三种场景），上层（home.py / main.py share API）捕获后透传提示，禁止吞成笼统的 HTTPStatusError。
- **`verify_cookie(cookie_file)` 验证入口**：请求知乎首页，200 即有效（**不自动刷新**，需设置页手动刷新）。设置页保存 zhihu.com Cookie 后自动
  验证，并支持 `/settings?test=DOMAIN` 链接手动验证。
- **知乎 Cookie 自动刷新（延长有效期）**：`zhihu_answer.refresh_zhihu_cookie(cookie_file)` 用 Playwright
  注入旧 cookie（`browser_extractor._parse_netscape_cookies`）→ 访问 `/hot` 触发 zse-96 JS 生成 `__zse_ck` →
  轮询 `context.cookies()` 至出现 `__zse_ck`（最长 20s，参考 zhihu-fisher / RSSHub）→
  `_playwright_cookies_to_netscape()` 回写；`_fetch_answer_page()` 403 时自动刷新重试 **一次**（仍 403 抛
  “自动刷新后仍无效”），`verify_cookie` 不自动刷新需手动触发；设置页 `/settings?refresh=DOMAIN`
 （仅知乎域显示）对应 `settings._run_cookie_refresh`（`background_tasks.create` + `with container:` 显式 slot，
  与 `_run_cookie_test` 同范式）；刷新仅在 `d_c0` 存在时执行（缺 `d_c0` 无法生成 `__zse_ck`）。
- **浏览器拟真请求头**：知乎 `httpx` 请求统一带 `_ZHIHU_HEADERS`（含 `Accept` / `Accept-Language` /
  `Sec-Fetch-Dest/Mode/Site` / `Upgrade-Insecure-Requests`），降低 WAF 因指纹不符而快速吊销的概率。
- **过期状态展示**：`cookie_manager.parse_cookie_expiry()` / `_expiry_summary()` / `list_cookies_with_expiry()`
  解析 Netscape 格式 expires 列（0 或非法值视为会话级），设置页表格按行展示"已过期 / 含会话级 / 有效至"。
- **专栏 SSR 无标题**：zhuanlan 页面 SSR 无 `<title>` / og:title，标题回退"知乎专栏 {id}"；
  `_extract_title` 支持 `/answer/`、`/pin/`、`/p/` 三种回退。
- **提取优先级**：1) `data-actual` / `data-original` 属性（原图地址） 2)
  `js-initialData` / `__NEXT_DATA_INIT__` JSON（含 `window.__INITIAL_STATE__` 经典格式）
  3) `<img src>` 兜底。
- **原图归一化与去重**：`_normalize_image_url()` 去除 `/80/v2-xxx.jpg` 缩略图尺寸前缀；
  同一张图跨 CDN 子域名 / 扩展名出现时，用 `v2-{hash}` 去重。
- **视频已放弃**：yt-dlp 不支持 answer URL 格式（只支持 zvideo/{id}），静态 HTML 无法可靠提取视频。

### 豆瓣人物图片（douban_photo.py）
- **列表页必须带 Cookie**：`douban.com/personage/{pid}/photos/` 无 Cookie 时 302 跳转
  `sec.douban.com` 安全校验页（`follow_redirects=True` 后最终 URL 变为 sec 域名）。
  `_fetch_page` 检测 `str(resp.url)` 含 `sec.douban.com` 或直接 403/418 时抛
  `DoubanAccessError`（区分"未配置/已失效"），禁止吞成笼统 HTTPStatusError。
- **CDN 只接受豆瓣来源 Referer（踩坑结论）**：`img*.doubanio.com` 对无 Referer **或非
  douban.com/doubanio.com 来源**的请求一律 418（防盗链收紧，早期实测跨站 Referer 可过，现已拒绝）。
  httpx 下载统一带 `Referer: https://www.douban.com/`。**浏览器预览无法伪造豆瓣 Referer**
  （页面 URL 必被拒，referrerpolicy 属性也无济于事），因此新增应用内代理 `GET /douban-image?url=...`
  （main.py）：服务端带豆瓣 Referer 代抓后返回图片，SSRF 防护仅放行 doubanio.com 域名；
  home.py 分析页缩略图/预览网格与 history.py 缩略图对 doubanio 图片一律走该代理。
- **原图 = 缩略图路径变换**：列表页缩略图 `/view/photo/photo/public/p{id}.jpg` → 原图
  `/view/photo/xl/public/p{id}.jpg`，等价单页"查看大图"（photo-zoom）链接但无需签名参数、
  无需 Cookie。实测 67/67 张全部可用，避免 N+1 逐张抓单页。
- **单页兜底**：xl 下载 4xx 时抓 `.../photo/{id}` 单页解析 `photo-zoom` href（含 `&amp;`
  需 `html.unescape`）重试一次，防御未来结构变化。
- **预览与全局 no-referrer 冲突**：home.py 全局 `<meta name="referrer" content="no-referrer">`
  （Bilibili 需要）会让豆瓣缩略图 418。解决：豆瓣 img 单独加 `referrerpolicy="unsafe-url"`
  （Quasar QImg 支持该 prop，可覆盖文档级策略）；预览点击打开豆瓣**单页**而非直开 xl
  （浏览器导航同样不带 Referer，直开 xl 会 418）。
- **`verify_cookie` 用 `/mine/`**：豆瓣首页公开无需登录（200 无法区分），`/mine/`（我的豆瓣）
  无 Cookie 403、有 Cookie 200 且 302 到 `/people/{uid}/`，以此为准。
- **分页结构**：每页 30 张，分页器 `(共N张)` 总张数 + `?start={n}&sortby={sortby}`；
  提取循环按 total 截止 + 页容量 + photo_id 去重。
- **反爬风控（踩坑结论）**：连续高频请求（如 67 张 xl 批量下载 / 反复分析）会触发机器人检测，
  302 到 `https://www.douban.com/misc/sorry?original-url=...`（腾讯云验证码 TCaptcha，HTTP 200）。
  症状是提取**静默返回 0 张**（页面 200 但内容为验证码 HTML）。修复：① `_fetch_page` 检测
  URL 含 `misc/sorry` 或 HTML 含 `turing.captcha` 时抛 `DoubanAccessError`（提示稍后再试）；
  ② 下载循环与分页间加 `_REQUEST_DELAY=0.3s` 节流。风控按 IP 持续一段时间，提示用户等待。
- **主页与列表页都支持**：用户常直接粘贴人物主页 `/personage/{pid}/`（而非 /photos/），
  `is_douban_photo_url()` 两者都识别，提取统一走列表页。浏览器未登录时地址栏会变成
  `sec.douban.com/c?...` 校验 URL——home.py / share API 对该 URL 直接拦截提示。

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
