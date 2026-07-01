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


def _is_netscape_format(content: str) -> bool:
    """检查内容是否为 Netscape 格式的 Cookie 文件。

    Netscape 格式每行包含 tab 分隔的 7 个字段：
    domain, flag, path, secure, expiry, name, value
    """
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            return True
    return False


def _raw_to_netscape(content: str, domain: str) -> str:
    """将原始 HTTP Cookie 字符串转为 Netscape 格式。

    输入:  "name1=val1; name2=val2"
    输出:  Netscape 格式的多行文本
    """
    import time

    lines = ["# Netscape HTTP Cookie File"]
    # 去掉常见前缀
    text = content.strip()
    if text.startswith("Cookie:"):
        text = text[7:].strip()
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]

    for pair in text.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        # 去掉引号
        value = value.strip().strip('"').strip("'")
        expiry = str(int(time.time()) + 365 * 86400)
        parts = [
            f".{domain}",
            "TRUE",
            "/",
            "FALSE",
            expiry,
            name,
            value,
        ]
        lines.append("\t".join(parts))
    return "\n".join(lines) + "\n"


def _normalize_cookie_content(content: str, domain: str) -> str:
    """将 Cookie 内容转为 yt-dlp 兼容的 Netscape 格式。

    支持输入格式：
    1. Netscape 格式（原样返回）
    2. 原始 HTTP Cookie 字符串（name=value; name=value — 自动转换）
    """
    if _is_netscape_format(content):
        return content
    # 尝试作为原始 HTTP Cookie 字符串解析
    converted = _raw_to_netscape(content, domain)
    if _is_netscape_format(converted):
        return converted
    return content


def save_cookie(domain: str, cookie_content: str) -> bool:
    """保存 Cookie 到文件和数据库。

    domain 会被自动规范化（去掉 www.、端口，转小写）。
    非 Netscape 格式的 Cookie 内容会自动转换。
    """
    init_cookie_dir()
    domain = normalize_domain(domain)
    cookie_content = _normalize_cookie_content(cookie_content, domain)
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
