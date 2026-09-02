import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from core.db import get_connection

logger = logging.getLogger(__name__)

_COOKIE_DIR_ENV = "VIDZAP_COOKIE_DIR"


def get_cookie_dir() -> Path:
    override = os.environ.get(_COOKIE_DIR_ENV)
    if override:
        return Path(override)
    data_dir = os.environ.get("NICEVID_DATA_DIR", "data")
    return Path(data_dir) / "cookies"


def normalize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if ":" in domain:
        domain = domain.rsplit(":", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain


def extract_domain_from_input(text: str) -> str:
    text = text.strip()
    if "://" in text or text.startswith("www."):
        parsed = urlparse(text)
        raw = parsed.netloc or parsed.path
    else:
        raw = text
    return normalize_domain(raw)


def is_valid_domain(domain: str) -> bool:
    if not domain or len(domain) > 253:
        return False
    parts = domain.split(".")
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
    get_cookie_dir().mkdir(exist_ok=True)


def _resolve_cookie_path(cookie_file: str) -> str:
    p = Path(cookie_file)
    if not p.is_absolute():
        p = get_cookie_dir() / cookie_file
    return str(p)


def get_cookie_for_url(url: str) -> str | None:
    raw_domain = urlparse(url).netloc
    domain = normalize_domain(raw_domain)

    with get_connection() as conn:
        row = conn.execute(
            "SELECT cookie_file FROM cookies WHERE domain = ?",
            (domain,),
        ).fetchone()
        if row:
            return _resolve_cookie_path(str(row["cookie_file"]))

        rows = conn.execute("SELECT domain, cookie_file FROM cookies").fetchall()
        best_sub: tuple[str, str] | None = None
        best_rev: tuple[str, str] | None = None

        for row in rows:
            cd = row["domain"]
            if domain.endswith("." + cd):
                if best_sub is None or len(cd) > len(best_sub[0]):
                    best_sub = (cd, str(row["cookie_file"]))
            elif cd.endswith("." + domain):
                if best_rev is None or len(cd) < len(best_rev[0]):
                    best_rev = (cd, str(row["cookie_file"]))

        if best_sub:
            return _resolve_cookie_path(best_sub[1])
        if best_rev:
            return _resolve_cookie_path(best_rev[1])
    return None


def _is_netscape_format(content: str) -> bool:
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            return True
    return False


def _raw_to_netscape(content: str, domain: str) -> str:
    lines = ["# Netscape HTTP Cookie File"]
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
        value = value.strip().strip('"').strip("'")
        expiry = str(int(time.time()) + 365 * 86400)
        # __Secure- / __Host- 前缀的 cookie 必须设置 secure=TRUE 才能通过 HTTPS 发送
        is_secure = name.startswith("__Secure-") or name.startswith("__Host-")
        parts = [
            f".{domain}",
            "TRUE",
            "/",
            "TRUE" if is_secure else "FALSE",
            expiry,
            name,
            value,
        ]
        lines.append("\t".join(parts))
    return "\n".join(lines) + "\n"


def _normalize_cookie_content(content: str, domain: str) -> str | None:
    if _is_netscape_format(content):
        return content
    converted = _raw_to_netscape(content, domain)
    if _is_netscape_format(converted):
        return converted
    logger.warning("无法解析 Cookie 内容 (domain=%s): 前100字符=%r", domain, content[:100])
    return None


def save_cookie(domain: str, cookie_content: str) -> bool:
    init_cookie_dir()
    domain = normalize_domain(domain)
    normalized = _normalize_cookie_content(cookie_content, domain)
    if normalized is None:
        return False

    cookie_dir = get_cookie_dir()
    relative_path = f"{domain}.txt"
    cookie_file = cookie_dir / relative_path
    cookie_file.write_text(normalized)
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cookies (domain, cookie_file, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (domain, relative_path),
            )
    except Exception:
        cookie_file.unlink(missing_ok=True)
        raise
    return True


def get_cookie(domain: str) -> dict | None:
    domain = normalize_domain(domain)
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM cookies WHERE domain = ?", (domain,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        cookie_file = _resolve_cookie_path(str(result["cookie_file"]))
        try:
            with open(cookie_file) as f:
                result["content"] = f.read()
        except FileNotFoundError:
            result["content"] = ""
            logger.warning("Cookie 文件不存在: %s", cookie_file)
        return result


def list_cookies() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM cookies ORDER BY domain").fetchall()
        return [dict(row) for row in rows]


def parse_cookie_expiry(content: str) -> dict[str, int | None]:
    """解析 Netscape cookie 文件中每个 cookie 的过期时间。

    Returns:
        {cookie_name: epoch 秒或 None}；None 表示会话级 cookie
        （expires 为 0 或非法值，浏览器关闭即失效）。
    """
    result: dict[str, int | None] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name = parts[5]
        if not name:
            continue
        try:
            expiry = int(parts[4])
        except ValueError:
            expiry = 0
        # Netscape 格式中 expires=0 表示会话级 cookie（浏览器关闭即失效）
        result[name] = expiry if expiry > 0 else None
    return result


def _expiry_summary(content: str) -> dict:
    """汇总 cookie 文件的过期状态（供设置页展示）。

    Returns:
        {
            "count": int,           # 可解析 cookie 数
            "expired": int,         # 已过期 cookie 数
            "session": int,         # 会话级 cookie 数
            "valid_until": int | None,  # 全部有效时最早过期时间
            "status": str,          # "valid" / "expired" / "session" / "empty"
        }
    """
    parsed = parse_cookie_expiry(content)
    if not parsed:
        return {
            "count": 0,
            "expired": 0,
            "session": 0,
            "valid_until": None,
            "status": "empty",
        }
    now = int(time.time())
    expires: list[int] = []
    expired = 0
    session = 0
    for exp in parsed.values():
        if exp is None:
            session += 1
        elif exp < now:
            expired += 1
        else:
            expires.append(exp)
    if expired:
        status = "expired"
    elif session:
        status = "session"
    else:
        status = "valid"
    return {
        "count": len(parsed),
        "expired": expired,
        "session": session,
        "valid_until": min(expires) if expires else None,
        "status": status,
    }


def list_cookies_with_expiry() -> list[dict]:
    """列出所有 cookie 并附带过期状态总结（设置页表格用）。"""
    rows = list_cookies()
    for row in rows:
        path = _resolve_cookie_path(str(row["cookie_file"]))
        try:
            content = Path(path).read_text()
        except OSError:
            content = ""
        row["expiry"] = _expiry_summary(content)
    return rows


def delete_cookie(domain: str) -> bool:
    cookie_file = get_cookie_dir() / f"{domain}.txt"
    if cookie_file.exists():
        cookie_file.unlink()
    with get_connection() as conn:
        conn.execute("DELETE FROM cookies WHERE domain = ?", (domain,))
    return True
