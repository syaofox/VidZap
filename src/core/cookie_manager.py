from pathlib import Path
from urllib.parse import urlparse

from core.db import get_connection

COOKIES_DIR = Path("cookies")


def normalize_domain(domain: str) -> str:
    """规范化域名：去掉 www. 前缀、端口号，转小写。

    Examples:
        www.youtube.com -> youtube.com
        YouTube.com:443 -> youtube.com
        m.youtube.com -> m.youtube.com
    """
    domain = domain.strip().lower()
    # 去掉端口号
    if ":" in domain:
        domain = domain.rsplit(":", 1)[0]
    # 去掉 www. 前缀
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def extract_domain_from_input(text: str) -> str:
    """从用户输入中提取域名。支持直接输入域名或完整 URL。

    Examples:
        https://www.youtube.com/watch?v=xxx -> youtube.com
        youtube.com -> youtube.com
    """
    text = text.strip()
    # 如果看起来像 URL，用 urlparse 提取
    if "://" in text or text.startswith("www."):
        parsed = urlparse(text)
        raw = parsed.netloc or parsed.path
    else:
        raw = text
    return normalize_domain(raw)


def is_valid_domain(domain: str) -> bool:
    """校验域名格式是否合法。"""
    if not domain or len(domain) > 253:
        return False
    parts = domain.split(".")
    # 至少两段（如 youtube.com）
    if len(parts) < 2:
        return False
    for part in parts:
        if not part or len(part) > 63:
            return False
        if not all(c.isalnum() or c == "-" for c in part):
            return False
        if part.startswith("-") or part.endswith("-"):
            return False
    return True


def init_cookie_dir() -> None:
    """初始化 Cookie 目录"""
    COOKIES_DIR.mkdir(exist_ok=True)


def get_cookie_for_url(url: str) -> str | None:
    """根据 URL 自动匹配 Cookie 文件。

    匹配策略：
    1. 规范化 URL 域名（去掉 www.、端口，转小写）
    2. 精确匹配：规范化后的域名完全相等
    3. 后缀匹配：URL 域名以 .{cookie_domain} 结尾（支持子域名）
       例如：youtube.com cookie 匹配 www.youtube.com、m.youtube.com
    """
    raw_domain = urlparse(url).netloc
    domain = normalize_domain(raw_domain)

    with get_connection() as conn:
        # 精确匹配（规范化后）
        row = conn.execute(
            "SELECT cookie_file FROM cookies WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row:
            return str(row["cookie_file"])

        # 后缀匹配：URL 域名以 .{cookie_domain} 结尾
        rows = conn.execute("SELECT domain, cookie_file FROM cookies").fetchall()
        for row in rows:
            cookie_domain = row["domain"]
            # 完全相等（冗余保险）
            if domain == cookie_domain:
                return str(row["cookie_file"])
            # 子域名匹配：www.youtube.com / m.youtube.com 匹配 youtube.com
            if domain.endswith("." + cookie_domain):
                return str(row["cookie_file"])
            # 反向：youtube.com 匹配用户保存的 m.youtube.com（少见但合理）
            if cookie_domain.endswith("." + domain):
                return str(row["cookie_file"])
    return None


def save_cookie(domain: str, cookie_content: str) -> bool:
    """保存 Cookie 到文件和数据库。

    domain 会被自动规范化（去掉 www.、端口，转小写）。
    """
    init_cookie_dir()
    domain = normalize_domain(domain)
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
