import asyncio
import logging
import shutil
import subprocess
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

import yt_dlp

from core.db import get_connection

logger = logging.getLogger(__name__)

DOWNLOADS_DIR = Path("downloads")


def _format_speed(speed: float | None) -> str:
    """格式化下载速度为人类可读格式"""
    if speed is None:
        return "N/A"
    if speed < 1024:
        return f"{speed:.0f} B/s"
    elif speed < 1024 * 1024:
        return f"{speed / 1024:.1f} KB/s"
    else:
        return f"{speed / (1024 * 1024):.1f} MB/s"


def _format_eta(eta: float | None) -> str:
    """格式化预计剩余时间"""
    if eta is None:
        return "N/A"
    eta_int = int(eta)
    if eta_int < 60:
        return f"{eta_int}s"
    elif eta_int < 3600:
        return f"{eta_int // 60}:{eta_int % 60:02d}"
    else:
        return f"{eta_int // 3600}:{(eta_int % 3600) // 60:02d}:{eta_int % 60:02d}"


def check_ffmpeg() -> bool:
    """检查系统是否安装了 ffmpeg"""
    return shutil.which("ffmpeg") is not None


def init_downloads_dir() -> None:
    """初始化下载目录"""
    DOWNLOADS_DIR.mkdir(exist_ok=True)


async def extract_info(url: str, cookie_file: str | None = None) -> dict:
    """提取视频信息"""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if cookie_file:
        opts["cookiefile"] = cookie_file

    loop = asyncio.get_event_loop()
    info = await asyncio.wait_for(
        loop.run_in_executor(None, lambda: _extract_sync(url, opts)),
        timeout=60,
    )
    return info


def _extract_sync(url: str, opts: dict) -> dict:
    """同步提取视频信息，cookie 失败时自动降级重试"""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        # cookie 可能触发 YouTube 反爬，降级为无 cookie 重试
        if opts.get("cookiefile"):
            fallback = {k: v for k, v in opts.items() if k != "cookiefile"}
            try:
                with yt_dlp.YoutubeDL(fallback) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception:
                raise  # 无 cookie 也失败，抛出原始异常
        else:
            raise

    formats = []
    for f in info.get("formats", []):
        formats.append(
            {
                "format_id": f["format_id"],
                "resolution": f.get("resolution")
                or f"{f.get('width', '?')}x{f.get('height', '?')}",
                "ext": f.get("ext", "unknown"),
                "filesize": (f.get("filesize") or f.get("filesize_approx") or 0) // (1024 * 1024),
                "vcodec": f.get("vcodec", "none"),
                "acodec": f.get("acodec", "none"),
            }
        )

    return {
        "title": info.get("title", "Unknown"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "formats": formats,
        "has_subtitles": bool(info.get("subtitles")),
    }


async def start_download(
    url: str,
    format_id: str,
    cookie_file: str | None,
    progress_callback: Callable[[float, str, str], None] | None = None,
    merge_format: bool = True,
    write_thumbnail: bool = False,
    write_subtitles: bool = False,
    progress_state: dict | None = None,
    download_id: int | None = None,
) -> str:
    """开始下载，支持格式合并"""
    init_downloads_dir()
    has_ffmpeg = check_ffmpeg()

    def hook(d) -> None:
        status = d.get("status", "")

        if status == "downloading":
            # 使用可靠的字段计算进度
            downloaded = d.get("downloaded_bytes", 0) or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate")

            # 计算百分比
            if total and total > 0:
                percent = (downloaded / total) * 100
            else:
                percent = 0

            speed = d.get("speed")
            eta = d.get("eta")

            # 格式化显示
            speed_str = _format_speed(speed)
            eta_str = _format_eta(eta)

            logger.debug(f"Download progress: {percent:.1f}% - {speed_str} - ETA: {eta_str}")

            if progress_state is not None:
                progress_state[url] = {
                    "status": "downloading",
                    "percent": percent,
                    "speed": speed_str,
                    "eta": eta_str,
                }
            if progress_callback:
                try:
                    progress_callback(percent, speed_str, eta_str)
                except Exception:
                    pass

        elif status == "finished":
            logger.debug("Download finished")
            if progress_state is not None:
                progress_state[url] = {
                    "status": "finished",
                    "percent": 100,
                    "speed": "",
                    "eta": "",
                }
            if progress_callback:
                try:
                    progress_callback(100, "完成", "0")
                except Exception:
                    pass

        elif status == "error":
            logger.error(f"Download error: {d}")
            if progress_state is not None:
                progress_state[url] = {
                    "status": "error",
                    "percent": 0,
                    "speed": "",
                    "eta": "下载出错",
                }

    opts: dict = {
        "progress_hooks": [hook],
        "outtmpl": str(DOWNLOADS_DIR / "%(extractor)s/%(title)s/%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    if cookie_file:
        opts["cookiefile"] = cookie_file

    # 封面
    if write_thumbnail:
        opts["writethumbnail"] = True
        opts["postprocessors"] = opts.get("postprocessors", [])
        opts["postprocessors"].append(
            {
                "key": "FFmpegThumbnailsConvertor",
                "format": "jpg",
                "when": "before_dl",
            }
        )

    # 字幕
    if write_subtitles:
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["all"]

    # 格式合并：需要 ffmpeg
    if merge_format and "+" in format_id:
        if has_ffmpeg:
            opts["format"] = format_id
            opts["merge_output_format"] = "mp4"
        else:
            # 没有 ffmpeg，选择单个格式（优先视频格式）
            opts["format"] = format_id.split("+")[0]
    else:
        opts["format"] = format_id

    loop = asyncio.get_event_loop()
    try:
        file_path = await loop.run_in_executor(None, lambda: _download_sync(url, opts))
    except Exception as e:
        if download_id is not None:
            update_download_status(download_id, "failed", error_msg=str(e)[:500])
        raise

    # 更新下载记录
    if download_id is not None:
        update_download_status(download_id, "completed", file_path=file_path)
    else:
        _save_download_history(url, format_id, file_path)

    return file_path


def _download_sync(url: str, opts: dict) -> str:
    """同步下载，cookie 失败时自动降级重试"""
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
            info = ydl.extract_info(url, download=False)
            return str(ydl.prepare_filename(info))
    except Exception:
        if opts.get("cookiefile"):
            fallback = {k: v for k, v in opts.items() if k != "cookiefile"}
            with yt_dlp.YoutubeDL(fallback) as ydl:
                ydl.download([url])
                info = ydl.extract_info(url, download=False)
                return str(ydl.prepare_filename(info))
        raise


def _save_download_history(url: str, format_id: str, file_path: str) -> None:
    """保存下载历史"""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO downloads (url, format_id, file_path, status, completed_at)
            VALUES (?, ?, ?, 'completed', CURRENT_TIMESTAMP)
            """,
            (url, format_id, file_path),
        )


def find_existing_download(url: str) -> dict | None:
    """查找指定 URL 的已有下载记录，返回最近一条或 None"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM downloads WHERE url = ? ORDER BY id DESC LIMIT 1",
            (url,),
        ).fetchone()
        return dict(row) if row else None


def create_download_record(url: str, title: str, thumbnail: str, format_id: str) -> int:
    """创建下载记录，返回记录 ID"""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO downloads (url, title, thumbnail, format_id, status)
            VALUES (?, ?, ?, ?, 'downloading')
            """,
            (url, title, thumbnail, format_id),
        )
        return cur.lastrowid


def update_download_status(
    download_id: int,
    status: str,
    file_path: str | None = None,
    error_msg: str | None = None,
) -> None:
    """更新下载状态"""
    with get_connection() as conn:
        if status == "completed":
            conn.execute(
                """
                UPDATE downloads
                SET status=?, file_path=?, completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, file_path, download_id),
            )
        elif status == "failed":
            conn.execute(
                """
                UPDATE downloads
                SET status=?, error_msg=?, completed_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (status, error_msg, download_id),
            )
        else:
            conn.execute(
                "UPDATE downloads SET status=? WHERE id=?",
                (status, download_id),
            )


def get_download_by_id(download_id: int) -> dict | None:
    """按 ID 获取下载记录"""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM downloads WHERE id=?", (download_id,)).fetchone()
        return dict(row) if row else None


def get_download_history() -> list[dict]:
    """获取下载历史"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM downloads ORDER BY created_at DESC LIMIT 100",
        ).fetchall()
        return [dict(row) for row in rows]


def delete_download_record(download_id: int) -> None:
    """删除下载记录"""
    with get_connection() as conn:
        conn.execute("DELETE FROM downloads WHERE id=?", (download_id,))


def clear_completed_records() -> int:
    """清理所有已完成的下载记录，返回删除条数"""
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM downloads WHERE status='completed'")
        return cur.rowcount


async def batch_download(
    urls: list[str],
    format_id: str,
    cookie_file: str | None,
    progress_callback: Callable[[str, float, str], None],
) -> list[str]:
    """批量下载多个视频"""
    results = []
    for url in urls:
        try:

            def url_progress(percent: float, speed: str, eta: str) -> None:
                progress_callback(url, percent, speed)

            file_path = await start_download(
                url,
                format_id,
                cookie_file,
                url_progress,
            )
            results.append(file_path)
        except Exception as e:
            progress_callback(url, -1, str(e))
    return results


def get_ytdlp_version() -> str:
    """获取当前 yt-dlp 版本"""
    return yt_dlp.version.__version__


def update_ytdlp() -> tuple[bool, str, bool]:
    """通过 uv 更新 yt-dlp，返回 (是否成功, 消息, 版本是否变化)"""
    old_ver = get_ytdlp_version()
    try:
        result = subprocess.run(
            ["uv", "pip", "install", "--upgrade", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return False, result.stderr.strip()[:200] or "更新失败", False
        import importlib

        importlib.reload(yt_dlp.version)
        new_ver = get_ytdlp_version()
        if new_ver != old_ver:
            return True, f"{old_ver} → {new_ver}", True
        return True, f"已是最新 ({new_ver})", False
    except subprocess.TimeoutExpired:
        return False, "更新超时", False
    except Exception as e:
        return False, str(e)[:200], False


# 常用站点名称 -> 官网URL 映射
_SITE_URL_MAP: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "youtube:tab": "https://www.youtube.com",
    "youtube:playlist": "https://www.youtube.com",
    "youtube:search": "https://www.youtube.com",
    "youtube:watchLater": "https://www.youtube.com",
    "youtube:recommended": "https://www.youtube.com",
    "youtube:subscriptions": "https://www.youtube.com",
    "youtube:history": "https://www.youtube.com",
    "youtube:music": "https://music.youtube.com",
    "youtube:podcast": "https://www.youtube.com/podcasts",
    "bilibili": "https://www.bilibili.com",
    "bilibili:category": "https://www.bilibili.com",
    "bilibili:watchlater": "https://www.bilibili.com",
    "douyin": "https://www.douyin.com",
    "ixigua": "https://www.ixigua.com",
    "iqiyi": "https://www.iqiyi.com",
    "youku": "https://www.youku.com",
    "qq": "https://v.qq.com",
    "mgtv": "https://www.mgtv.com",
    "acfun": "https://www.acfun.cn",
    "twitch": "https://www.twitch.tv",
    "twitch:clips": "https://clips.twitch.tv",
    "vimeo": "https://vimeo.com",
    "dailymotion": "https://www.dailymotion.com",
    "nicovideo": "https://www.nicovideo.jp",
    "twitter": "https://x.com",
    "twitter:shorts": "https://x.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "tiktok": "https://www.tiktok.com",
    "reddit": "https://www.reddit.com",
    "pinterest": "https://www.pinterest.com",
    "tumblr": "https://www.tumblr.com",
    "weibo": "https://weibo.com",
    "xiaohongshu": "https://www.xiaohongshu.com",
    "snapchat": "https://www.snapchat.com",
    "vk": "https://vk.com",
    "threads": "https://www.threads.net",
    "bluesky": "https://bsky.app",
    "soundcloud": "https://soundcloud.com",
    "bandcamp": "https://bandcamp.com",
    "neteasemusic": "https://music.163.com",
    "ytmusic": "https://music.youtube.com",
    "spotify": "https://open.spotify.com",
    "apple_music": "https://music.apple.com",
    "deezer": "https://www.deezer.com",
    "tidal": "https://tidal.com",
    "kugou": "https://www.kugou.com",
    "kuwo": "https://www.kuwo.cn",
    "ximalaya": "https://www.ximalaya.com",
    "bbc": "https://www.bbc.com",
    "cnn": "https://www.cnn.com",
    "ted": "https://www.ted.com",
    "coursera": "https://www.coursera.org",
    "udemy": "https://www.udemy.com",
    "khanacademy": "https://www.khanacademy.org",
    "crunchyroll": "https://www.crunchyroll.com",
    "espn": "https://www.espn.com",
    "imgur": "https://imgur.com",
    "flickr": "https://www.flickr.com",
    "archiveorg": "https://archive.org",
    "douban": "https://www.douban.com",
    "zhihu": "https://www.zhihu.com",
    "toutiao": "https://www.toutiao.com",
    "giphy": "https://giphy.com",
    "9gag": "https://9gag.com",
    "linkedin": "https://www.linkedin.com",
    "rumble": "https://rumble.com",
    "odysee": "https://odysee.com",
    "douyu": "https://www.douyu.com",
    "huya": "https://www.huya.com",
    "kuaishou": "https://www.kuaishou.com",
    "xhs": "https://www.xiaohongshu.com",
    "miaopai": "https://www.miaopai.com",
    "haokan": "https://haokan.baidu.com",
    "imdb": "https://www.imdb.com",
    "arte": "https://www.arte.tv",
    "francetv": "https://www.france.tv",
    "niconico": "https://www.nicovideo.jp",
    "peertube": "https://joinpeertube.org",
    "bandlab": "https://www.bandlab.com",
    "lizhifm": "https://www.lizhi.fm",
    "ximalaya": "https://www.ximalaya.com",
    "ted": "https://www.ted.com",
    "twitch:stream": "https://www.twitch.tv",
}


@lru_cache(maxsize=1)
def get_supported_sites() -> list[tuple[str, str]]:
    """从 yt-dlp 获取全部支持的站点名称，返回 (name, url) 列表。

    已知站点返回官网 URL，未知站点仅返回名称。
    结果会被缓存。
    """
    # 构建小写 key 的查找表
    url_map = {k.lower(): v for k, v in _SITE_URL_MAP.items()}

    extractors = yt_dlp.list_extractors()
    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    for ie in extractors:
        raw_name = ie.IE_NAME
        base_name = raw_name.split(":")[0]
        display_name = raw_name

        if base_name.lower() in ("generic", "common"):
            continue

        url = url_map.get(raw_name.lower()) or url_map.get(base_name.lower(), "")

        key = display_name.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append((display_name, url))

    results.sort(key=lambda x: x[0].lower())
    return results
