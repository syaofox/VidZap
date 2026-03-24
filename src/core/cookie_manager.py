from pathlib import Path
from urllib.parse import urlparse

from core.db import get_connection

COOKIES_DIR = Path("cookies")


def init_cookie_dir() -> None:
    """初始化 Cookie 目录"""
    COOKIES_DIR.mkdir(exist_ok=True)


def get_cookie_for_url(url: str) -> str | None:
    """根据 URL 自动匹配 Cookie 文件"""
    domain = urlparse(url).netloc
    with get_connection() as conn:
        # 精确匹配
        row = conn.execute(
            "SELECT cookie_file FROM cookies WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row:
            return str(row["cookie_file"])

        # 模糊匹配（如 .youtube.com 匹配 youtube.com）
        rows = conn.execute("SELECT domain, cookie_file FROM cookies").fetchall()
        for row in rows:
            if domain.endswith(row["domain"]) or row["domain"].endswith(domain):
                return str(row["cookie_file"])
    return None


def save_cookie(domain: str, cookie_content: str) -> bool:
    """保存 Cookie 到文件和数据库"""
    init_cookie_dir()
    cookie_file = COOKIES_DIR / f"{domain}.txt"
    cookie_file.write_text(cookie_content)

    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO cookies (domain, cookie_file, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            """,
            (domain, str(cookie_file)),
        )
    return True


def list_cookies() -> list[dict]:
    """列出所有 Cookie"""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM cookies ORDER BY domain").fetchall()
        return [dict(row) for row in rows]


def delete_cookie(domain: str) -> bool:
    """删除 Cookie"""
    with get_connection() as conn:
        conn.execute("DELETE FROM cookies WHERE domain = ?", (domain,))
    cookie_file = COOKIES_DIR / f"{domain}.txt"
    if cookie_file.exists():
        cookie_file.unlink()
    return True
