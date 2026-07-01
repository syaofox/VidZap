"""Tests for core.version."""
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
