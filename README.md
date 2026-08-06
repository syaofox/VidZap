# VidZap

视频下载工具（Web 界面 + Android 分享），基于 [NiceGUI](https://nicegui.io) + [yt-dlp](https://github.com/yt-dlp/yt-dlp)。

## 功能

- **视频分析**：输入链接自动识别视频信息（标题、封面、时长、格式列表）
- **智能格式推荐**：按分辨率自动分组，推荐最佳音视频合并格式
- **抖音图文笔记下载**：[Playwright](https://playwright.dev) 提取，支持 Cookie 注入
- **批量下载**：多个链接同时分析并逐一下载
- **Cookie 管理**：按域名管理 Cookie，支持登录态下载
- **封面/字幕**：一键勾选下载视频封面和字幕
- **下载历史**：列表/卡片双视图，支持预览播放、取回本地、重试失败任务
- **在线更新 yt-dlp**：一键更新到最新版
- **Android 分享**：系统分享链接 → 画质选择 → 远程入队下载

## 技术栈

| 组件 | 说明 |
|------|------|
| Python 3.13 + NiceGUI 3.9 | 后端 + Web UI |
| yt-dlp | 视频提取与下载 |
| Playwright | 抖音图文笔记提取（Chromium） |
| FastAPI | HTTP 路由（NiceGUI 内置） |
| SQLite | 本地数据存储 |
| Kotlin + Gradle | Android 原生 App |

## 快速开始

```bash
git clone <repo-url> && cd vidzap
uv sync                    # 安装 Python 依赖
uv run python src/main.py  # 启动（http://localhost:8080）
```

需要 [ffmpeg](https://ffmpeg.org/) 用于格式合并，未安装时仅支持单格式下载。

## 开发命令

```bash
uv run python src/main.py   # 启动应用
uv run pytest tests/ -v     # 运行测试
make lint                   # 代码检查 (ruff)
make format                 # 代码格式化 (ruff)
make type-check             # 类型检查 (mypy)
make android-build          # 编译 Android APK
make android-install        # 编译 + adb 安装
```

## Docker

```bash
docker compose build
docker compose up -d
```

## 项目结构

```
src/
  main.py              # 入口：应用初始化、HTTP 路由、Android 分享 API
  core/
    db.py              # SQLite 数据库（downloads、cookies 表）
    ytdlp_handler.py   # yt-dlp 封装：信息提取、下载、降级重试、格式处理
    cookie_manager.py  # Cookie 文件与数据库管理
    douyin_note.py     # 抖音图文笔记下载（httpx + Playwright 回退）
    browser_extractor.py # Playwright 浏览器提取引擎
    download_queue.py  # 异步下载队列（同源串行、跨源并行）
    version.py         # 从 pyproject.toml 读取版本号
  pages/
    home.py            # 首页：URL 输入、分析、格式选择、下载
    history.py         # 下载历史：列表/卡片视图、重试、预览、清理
    settings.py        # Cookie 管理页面
android/               # Android 原生 App（Kotlin + Gradle）
  app/src/main/java/com/vidzap/share/
    MainActivity.kt       # 启动入口，立即跳转设置页
    ShareHandlerActivity.kt # 接收系统分享（ACTION_SEND），两阶段 API 调用 + 画质选择弹窗
    SettingsActivity.kt   # 服务器地址配置
```

运行时生成的文件（`data/database.sqlite`、`downloads/`、`data/cookies/`）已加入 `.gitignore`。

## Android 编译

需要 Android SDK，路径配置在 `android/local.properties`：

```properties
sdk.dir=/home/user/Android/Sdk
```

```bash
make android-build     # 输出 android/app/build/outputs/apk/debug/app-debug.apk
make android-install   # 编译后通过 adb 安装到已连接的设备
```

也可用 Android Studio 打开 `android/` 目录，同步后直接构建。

## 下载路径

视频保存在 `downloads/<extractor>/<视频标题>/` 目录下；抖音图文在 `downloads/douyin/note_<id>_<标题>/`，知乎图片在 `downloads/zhihu/<kind>_<id>_<标题>/`。

## License

MIT
