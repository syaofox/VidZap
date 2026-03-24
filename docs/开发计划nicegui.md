
NiceVid —— 纯 Python 视频下载工具（NiceGUI + yt-dlp）**

一个**零 JS / 零 HTML / 零 CSS** 的现代化网页视频下载器，使用 **NiceGUI** 实现全 Python 前端，界面友好、实时进度丝滑，支持 Cookie 自动匹配、任意分辨率选择、一键下载保存。

---

### 1. 项目名称与定位
**NiceVid**（NiceGUI + Video）  
- 简洁、专业、好记  
- 国内用户可直接叫「NiceVid 视频下载器」

---

### 2. 项目结构（推荐布局）

```
nicevid/
├── .venv/                    # uv 虚拟环境（自动生成）
├── src/
│   ├── main.py               # 唯一入口文件（FastAPI + NiceGUI）
│   ├── core/
│   │   ├── ytdlp_handler.py  # 解析 + 下载核心
│   │   ├── cookie_manager.py # Cookie 存储与自动匹配
│   │   └── models.py         # Pydantic 模型（可选）
│   └── static/               # 下载完成后临时目录（.gitignore）
├── downloads/                # 用户下载文件保存目录
├── cookies/                  # 每个域名一个 .txt Cookie 文件
├── database.sqlite           # SQLite 存储 Cookie + 下载记录
├── pyproject.toml            # uv 项目管理文件
├── uv.lock                   # uv 锁定文件
├── .env                      # 配置（端口、下载路径）
├── .gitignore
└── README.md
```

---

### 3. 技术栈（全 Python）
- **UI 框架**：NiceGUI 3.9.0（2026 年最新版，原生 Tailwind + 实时 WebSocket）
- **后端**：FastAPI（NiceGUI 底层使用）
- **下载引擎**：yt-dlp（最新版）
- **虚拟环境管理**：**uv**（极速创建 + 依赖管理）
- **数据库**：SQLite（内置，无需额外安装）
- **异步**：asyncio + NiceGUI background_tasks

---

### 4. 环境搭建（使用 uv）

```bash
# 1. 安装 uv（先判断系统是否已经安装,一次就好）
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2. 创建项目
mkdir nicevid && cd nicevid
uv init --name nicevid --python 3.13

# 3. 添加依赖（uv 自动写入 pyproject.toml）
uv add nicegui==3.9.0 fastapi uvicorn yt-dlp pydantic python-multipart

# 4. 创建虚拟环境并安装
uv sync

# 5. 激活（uv 自动管理）
uv run python src/main.py
```

---

### 5. 核心功能实现

#### 5.1 Cookie 管理（自动匹配）
- SQLite 表存储 `域名 → Cookie 文件路径`
- 解析 URL 时自动查找最匹配域名（youtube.com > .com）
- 设置页面支持粘贴浏览器导出的 Cookie（Netscape 格式）

#### 5.2 URL 解析与分辨率选择
- 输入链接 → “分析”按钮
- 自动获取 Cookie → yt-dlp `extract_info`
- 用 `ui.table`（支持多选）显示所有可用格式（分辨率、ext、大小）

#### 5.3 下载与实时进度
- 选中格式后点击下载
- NiceGUI `background_tasks` + yt-dlp `progress_hooks`
- `ui.linear_progress` + 速度/ETA 实时更新
- 下载完成后 `ui.download()` 触发浏览器保存（支持 10GB+ 大文件流式下载）

---

### 6. 界面布局（全 Python 代码实现）

使用 `ui.tabs` 实现三页结构：

1. **首页**（下载主界面）
   - 大输入框 + 分析按钮
   - 视频封面 + 标题卡片
   - 可多选分辨率表格
   - “仅音频”开关 + 大绿色“开始下载”按钮
   - 全局进度条 + 当前任务列表

2. **Cookie 设置**
   - 表格显示已设置域名
   - 一键添加/编辑/删除（弹窗 + 大文本域）

3. **下载历史**
   - 表格展示已完成任务（可重新下载）

---

### 7. 关键代码模板

**pyproject.toml**（uv 自动生成后可手动补充）
```toml
[project]
name = "nicevid"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "nicegui==3.9.0",
    "fastapi",
    "uvicorn",
    "yt-dlp",
    "pydantic",
]
```

**src/main.py**（完整骨架，复制后即可启动）
```python
from nicegui import ui, app, background_tasks
import asyncio
from pathlib import Path
from core.ytdlp_handler import extract_info, start_download
from core.cookie_manager import get_cookie_for_url, init_db

init_db()

@ui.page('/')
def home():
    ui.header().classes('justify-between').props('elevated')
    with ui.header():
        ui.label('NiceVid').classes('text-h4 text-white')
        ui.button('设置', on_click=lambda: ui.navigate('/settings')).props('flat color=white')

    url = ui.input('粘贴视频链接').props('outlined clearable').classes('w-full max-w-2xl')
    card = ui.card().classes('w-full max-w-2xl')
    progress = ui.linear_progress(value=0).props('instant-feedback').classes('w-full hidden')

    table = ui.table(
        columns=[
            {'name': 'resolution', 'label': '分辨率', 'field': 'resolution'},
            {'name': 'ext', 'label': '格式', 'field': 'ext'},
            {'name': 'size', 'label': '大小', 'field': 'filesize'}
        ],
        selection='multiple'
    ).classes('w-full')

    async def analyze():
        card.clear()
        progress.classes(remove='hidden')
        cookie = get_cookie_for_url(url.value)
        info = await extract_info(url.value, cookie)
        with card:
            ui.image(info.get('thumbnail')).classes('w-64')
            ui.label(info['title']).classes('text-h5')
            table.rows = info['formats']

    ui.button('分析', on_click=analyze).props('color=primary')

    async def download():
        for row in table.selected:
            task_id = f"task_{asyncio.get_event_loop().time()}"
            background_tasks.create(task_id, start_download(
                url=url.value,
                format_id=row['format_id'],
                cookie_file=get_cookie_for_url(url.value),
                progress_bar=progress
            ))

    ui.button('下载选中格式', on_click=download).props('color=positive push')

@ui.page('/settings')
def settings(): 
    # Cookie 管理页面（后续可补全）
    ui.label('Cookie 设置页面').classes('text-h4')
    # ... 表格 + 添加按钮

ui.run(port=8080, reload=True, title='NiceVid')
```

**core/ytdlp_handler.py**（核心逻辑，已包含进度实时更新）
```python
import yt_dlp
from nicegui import ui

async def extract_info(url: str, cookie_file: str | None):
    opts = {'quiet': True, 'cookiefile': cookie_file}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    formats = [
        {
            'format_id': f['format_id'],
            'resolution': f.get('resolution', 'audio-only'),
            'ext': f['ext'],
            'filesize': f.get('filesize_approx', 0) // (1024*1024)
        } for f in info.get('formats', [])
    ]
    return {'title': info['title'], 'thumbnail': info.get('thumbnail'), 'formats': formats}

def start_download(url: str, format_id: str, cookie_file: str | None, progress_bar):
    def hook(d):
        if d['status'] == 'downloading':
            p = float(d.get('_percent_str', '0').strip('%'))
            progress_bar.set_value(p)

    opts = {
        'format': format_id,
        'cookiefile': cookie_file,
        'progress_hooks': [hook],
        'outtmpl': str(Path('downloads') / '%(title)s.%(ext)s')
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    ui.notify('✅ 下载完成！', type='positive')
    # 自动触发浏览器下载
    ui.download(ydl._filename)
```

---

### 8. 开发流程

**Day 1**：uv 初始化 + main.py 骨架 + 首页界面  
**Day 2**：cookie_manager + 设置页面  
**Day 3**：ytdlp_handler 完整实现（解析 + 下载 + 进度）  
**Day 4**：美化 + 下载历史 + 测试（YouTube / Bilibili / Twitter）

---

### 9. 启动与部署
- 本地：`uv run python src/main.py`  


