"""Tests for core.browser_extractor."""

import pytest

from core.browser_extractor import _parse_netscape_cookies, is_douyin_note_url


class TestIsDouyinNoteUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.douyin.com/note/7660844979422505545", True),
            ("https://douyin.com/note/7660844979422505545", True),
            ("http://www.douyin.com/note/7660844979422505545", True),
            ("http://douyin.com/note/7660844979422505545", True),
            ("https://www.douyin.com/video/7660844979422505545", False),
            ("https://www.douyin.com/jingxuan?modal_id=123", False),
            ("https://example.com/note/123", False),
            ("https://www.douyin.com/note/abc", False),
            ("", False),
            ("not a url", False),
        ],
    )
    def test_matches_note_urls(self, url, expected):
        assert is_douyin_note_url(url) == expected


class TestParseNetscapeCookies:
    def test_parses_valid_cookie_file(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            ".douyin.com\tTRUE\t/\tTRUE\t0\tcookie_name\tcookie_value\n"
            ".douyin.com\tTRUE\t/\tFALSE\t0\tname2\tvalue2\n"
        )
        result = _parse_netscape_cookies(str(cookie_file))
        assert len(result) == 2
        assert result[0] == {
            "name": "cookie_name",
            "value": "cookie_value",
            "domain": ".douyin.com",
            "path": "/",
            "secure": True,
        }
        assert result[1] == {
            "name": "name2",
            "value": "value2",
            "domain": ".douyin.com",
            "path": "/",
            "secure": False,
        }

    def test_skips_comments_and_empty_lines(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            "# This is a comment\n"
            "\n"
            ".douyin.com\tTRUE\t/\tTRUE\t0\tvalid_name\tvalid_value\n"
        )
        result = _parse_netscape_cookies(str(cookie_file))
        assert len(result) == 1
        assert result[0]["name"] == "valid_name"

    def test_skips_malformed_lines(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            ".douyin.com\tTRUE\t/\tTRUE\t0\tonly_name\n.douyin.com\tTRUE\t/\tTRUE\t0\t\t\t\t\n"
        )
        result = _parse_netscape_cookies(str(cookie_file))
        assert len(result) == 0

    def test_returns_empty_for_missing_file(self):
        result = _parse_netscape_cookies("/nonexistent/cookies.txt")
        assert result == []

    def test_returns_empty_for_empty_file(self, tmp_path):
        cookie_file = tmp_path / "empty.txt"
        cookie_file.write_text("")
        result = _parse_netscape_cookies(str(cookie_file))
        assert result == []
