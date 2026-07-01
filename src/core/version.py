import tomllib
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_app_version() -> str:
    with open(_PROJECT_ROOT / "pyproject.toml", "rb") as f:
        data: dict = tomllib.load(f)
        version: str = data["project"]["version"]
        return version
