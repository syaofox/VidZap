import tomllib
from functools import lru_cache
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """从 pyproject.toml 读取版本号（结果缓存；文件缺失或解析失败时回退 "unknown"）。"""
    try:
        with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
            data: dict = tomllib.load(f)
            version: str = data["project"]["version"]
            return version
    except (OSError, KeyError, ValueError):
        return "unknown"
