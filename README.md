# NiceVid

纯 Python 视频下载工具，基于 [NiceGUI](https://nicegui.io) + [yt-dlp](https://github.com/yt-dlp/yt-dlp)，提供 Web 界面操作。

## 功能

- **视频分析**：输入链接自动识别视频信息（标题、封面、时长、格式列表）
- **智能格式推荐**：按分辨率自动分组，推荐最佳音视频合并格式
- **批量下载**：支持批量粘贴多个链接同时下载
- **Cookie 管理**：按域名管理 Cookie，支持登录态下载
- **封面/字幕**：一键勾选下载视频封面和字幕
- **下载历史**：列表/卡片双视图，支持预览播放、取回本地、重试失败任务
- **支持网站浏览**：查看 yt-dlp 支持的全部站点，含官网链接
- **在线更新 yt-dlp**：一键更新 yt-dlp 到最新版

## 技术栈

| 组件 | 说明 |
|------|------|
| Python 3.13 | 运行环境 |
| NiceGUI 3.9 | Web UI 框架（Vue + Quasar） |
| yt-dlp | 视频提取与下载 |
| FastAPI | HTTP 路由（NiceGUI 内置） |
| SQLite | 本地数据存储 |

## 快速开始

```bash
# 克隆项目
git clone <repo-url> && cd nicevid

# 安装依赖
uv sync

# 启动
uv run python src/main.py
```

访问 http://localhost:8080

> 需要 [ffmpeg](https://ffmpeg.org/) 用于格式合并，未安装时仅支持单格式下载。

## 开发命令

```bash
uv run python src/main.py   # 启动应用
make lint                    # 代码检查 (ruff)
make format                  # 代码格式化 (ruff)
make type-check              # 类型检查 (mypy)
```

## 项目结构

```
src/
  main.py              # 入口：应用初始化、路由
  core/
    db.py              # SQLite 数据库（downloads、cookies 表）
    ytdlp_handler.py   # yt-dlp 封装：信息提取、下载、格式处理
    cookie_manager.py  # Cookie 文件与数据库管理
  pages/
    home.py            # 首页：URL 输入、分析、格式选择、下载
    history.py         # 下载历史：列表/卡片视图、重试、预览、清理
    settings.py        # Cookie 管理页面
```

运行时生成的文件（`database.sqlite`、`downloads/`、`cookies/`、`.nicegui/`）已加入 `.gitignore`。

## 下载路径

文件保存在 `downloads/<网站>/<视频标题>/<视频标题>.<扩展名>`，封面和字幕保存在同一目录下。

## License

MIT
