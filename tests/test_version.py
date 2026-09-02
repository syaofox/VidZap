"""Tests for core.version."""

from pathlib import Path

from core.version import get_app_version


class TestGetAppVersion:
    def test_returns_string(self):
        version = get_app_version()
        assert isinstance(version, str)

    def test_is_not_empty(self):
        assert len(get_app_version()) > 0

    def test_is_semver_like(self):
        version = get_app_version()
        parts = version.split(".")
        assert len(parts) >= 2
        for part in parts:
            assert part.isdigit() or "-" in part

    def test_missing_file_falls_back(self, monkeypatch):
        """pyproject.toml 缺失时应回退 "unknown" 而不是抛异常。"""
        from core import version as version_mod

        version_mod.get_app_version.cache_clear()
        try:
            monkeypatch.setattr(version_mod, "_PROJECT_ROOT", Path("/nonexistent/vidzap"))
            assert version_mod.get_app_version() == "unknown"
        finally:
            version_mod.get_app_version.cache_clear()
