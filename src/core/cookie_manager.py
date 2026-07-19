import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from core.db import get_connection

logger = logging.getLogger(__name__)

_COOKIE_DIR_ENV = "VIDZAP_COOKIE_DIR"


def get_cookie_dir() -> Path:
    override = os.environ.get(_COOKIE_DIR_ENV)
    return Path(override) if override else Path("cookies")


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
    import time

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


def _normalize_cookie_content(content: str, domain: str) -> str | None:
    if _is_netscape_format(content):
        return content
    converted = _raw_to_netscape(content, domain)
    if _is_netscape_format(converted):
        return converted
    logger.warning(
        "无法解析 Cookie 内容 (domain=%s): 前100字符=%r", domain, content[:100]
    )
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


def list_cookies() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM cookies ORDER BY domain").fetchall()
        return [dict(row) for row in rows]


def delete_cookie(domain: str) -> bool:
    cookie_file = get_cookie_dir() / f"{domain}.txt"
    if cookie_file.exists():
        cookie_file.unlink()
    with get_connection() as conn:
        conn.execute("DELETE FROM cookies WHERE domain = ?", (domain,))
    return True
