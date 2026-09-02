"""Tests for core.db."""

from core.db import _db_path, get_connection, init_db


def get_download_count() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM downloads").fetchone()
        return int(row["c"])


class TestInitDb:
    def test_creates_downloads_table(self):
        init_db()
        with get_connection() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            names = [r["name"] for r in tables]
            assert "downloads" in names
            assert "cookies" in names

    def test_creates_cookies_table(self):
        init_db()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='cookies'"
            ).fetchone()
            sql = row["sql"].lower()
            assert "domain" in sql
            assert "cookie_file" in sql
            assert "unique" in sql

    def test_is_idempotent(self):
        """init_db 可以安全多次调用。"""
        init_db()
        init_db()
        with get_connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as c FROM sqlite_master WHERE type='table'"
            ).fetchone()["c"]
            assert count >= 2


class TestGetConnection:
    def test_yields_open_connection(self):
        with get_connection() as conn:
            cur = conn.execute("SELECT 1 AS val")
            row = cur.fetchone()
            assert row["val"] == 1

    def test_commits_on_exit(self):
        init_db()
        before = get_download_count()
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO downloads (url, format_id, status) VALUES (?, ?, ?)",
                ("http://example.com", "best", "completed"),
            )
        after = get_download_count()
        assert after == before + 1

    def test_db_path_is_sqlite_file(self):
        assert str(_db_path()).endswith("database.sqlite")
