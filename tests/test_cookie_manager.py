"""Tests for core.cookie_manager."""

import pytest

from core.cookie_manager import (
    _is_netscape_format,
    _normalize_cookie_content,
    _raw_to_netscape,
    delete_cookie,
    extract_domain_from_input,
    get_cookie,
    get_cookie_dir,
    get_cookie_for_url,
    init_cookie_dir,
    is_valid_domain,
    list_cookies,
    normalize_domain,
    save_cookie,
)
from core.db import init_db

# =============================================================================
# normalize_domain
# =============================================================================


class TestNormalizeDomain:
    @pytest.mark.parametrize(
        ("input_domain", "expected"),
        [
            ("www.youtube.com", "youtube.com"),
            ("YouTube.com:443", "youtube.com"),
            ("m.youtube.com", "m.youtube.com"),
            ("  YOUTUBE.COM  ", "youtube.com"),
            ("douyin.com", "douyin.com"),
            ("bilibili.com:8080", "bilibili.com"),
        ],
    )
    def test_normalization(self, input_domain, expected):
        assert normalize_domain(input_domain) == expected


# =============================================================================
# extract_domain_from_input
# =============================================================================


class TestExtractDomainFromInput:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("https://www.youtube.com/watch?v=abc", "youtube.com"),
            ("youtube.com", "youtube.com"),
            ("www.douyin.com", "douyin.com"),
            ("  https://M.TWITCH.TV/clip  ", "m.twitch.tv"),
            ("http://bilibili.com/video/BV123", "bilibili.com"),
        ],
    )
    def test_extraction(self, text, expected):
        assert extract_domain_from_input(text) == expected


# =============================================================================
# is_valid_domain
# =============================================================================


class TestIsValidDomain:
    @pytest.mark.parametrize(
        ("domain", "expected"),
        [
            ("youtube.com", True),
            ("a.b", True),
            ("m.youtube.com", True),
            ("", False),
            ("a" * 300, False),
            ("a" * 254 + ".com", False),
            ("invalid", False),
            ("-bad.com", False),
            ("bad-.com", False),
            ("has space.com", False),
            ("a..b.com", False),
            ("a." + "x" * 64 + ".com", False),
        ],
    )
    def test_validity(self, domain, expected):
        assert is_valid_domain(domain) == expected


# =============================================================================
# get_cookie_dir & init_cookie_dir
# =============================================================================


class TestCookieDir:
    def test_default_dir(self, _temp_db_dir):
        assert get_cookie_dir() == _temp_db_dir / "cookies"

    def test_env_var_override(self, monkeypatch, tmp_path):
        target = tmp_path / "mycookies"
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(target))
        assert str(get_cookie_dir()) == str(target)

    def test_creates_directory(self, monkeypatch, tmp_path):
        target = tmp_path / "cookies"
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(target))
        init_cookie_dir()
        assert target.is_dir()


# =============================================================================
# save_cookie / list_cookies / delete_cookie
# =============================================================================


class TestCookieRoundtrip:
    def setup_method(self):
        init_db()
        init_cookie_dir()

    def test_save_and_list(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        saved = save_cookie("youtube.com", "content=abc")
        assert saved is True

        cookies = list_cookies()
        domains = [c["domain"] for c in cookies]
        assert "youtube.com" in domains

    def test_save_duplicate_updates(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("youtube.com", "content=1")
        save_cookie("youtube.com", "content=2")
        cookies = list_cookies()

        youtube_entries = [c for c in cookies if c["domain"] == "youtube.com"]
        assert len(youtube_entries) == 1

    def test_delete_cookie(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("test.example.com", "content=abc")
        assert any(c["domain"] == "test.example.com" for c in list_cookies())

        deleted = delete_cookie("test.example.com")
        assert deleted is True
        assert not any(c["domain"] == "test.example.com" for c in list_cookies())

    def test_delete_nonexistent(self):
        result = delete_cookie("nonexistent.domain.com")
        assert result is True

    def test_stores_relative_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("example.com", "x=1")
        cookies = list_cookies()
        row = next(c for c in cookies if c["domain"] == "example.com")
        assert row["cookie_file"] == "example.com.txt"

    def test_save_invalid_content_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        result = save_cookie("example.com", "garbage that cannot parse")
        assert result is False
        # DB should not have a record
        assert not any(c["domain"] == "example.com" for c in list_cookies())
        # File should not exist on disk
        assert not (tmp_path / "cookies" / "example.com.txt").exists()


# =============================================================================
# get_cookie_for_url
# =============================================================================


class TestGetCookieForUrl:
    def setup_method(self):
        init_db()

    def test_exact_match(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("youtube.com", "data=abc")
        result = get_cookie_for_url("https://www.youtube.com/watch?v=abc")
        assert result is not None
        assert result.endswith("youtube.com.txt")

    def test_subdomain_match(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("youtube.com", "data=abc")
        result = get_cookie_for_url("https://m.youtube.com/shorts/abc")
        assert result is not None

    def test_no_match_returns_none(self):
        result = get_cookie_for_url("https://unknown-site.example.com/video")
        assert result is None

    def test_multiple_cookies_returns_correct(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("bilibili.com", "data=bili")
        save_cookie("youtube.com", "data=yt")
        result = get_cookie_for_url("https://www.youtube.com/watch?v=abc")
        assert result is not None
        assert "youtube" in result

    def test_reverse_subdomain_match(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("m.youtube.com", "data=mobile")
        result = get_cookie_for_url("https://youtube.com/watch?v=abc")
        assert result is not None
        assert "m.youtube.com" in result

    def test_longest_subdomain_match_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("youtube.com", "root=root")
        save_cookie("m.youtube.com", "mobile=mobile")
        # Accessing m.youtube.com should return the more specific cookie
        result = get_cookie_for_url("https://m.youtube.com/watch?v=abc")
        assert result is not None
        assert "m.youtube.com" in result

    def test_normalize_handles_userinfo(self):
        result = get_cookie_for_url("https://user:pass@youtube.com/watch?v=abc")
        assert result is None

    def test_resolves_relative_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()

        save_cookie("example.com", "x=1")
        result = get_cookie_for_url("https://www.example.com/video")
        assert result is not None
        assert str(tmp_path / "cookies" / "example.com.txt") == result


# =============================================================================
# Cookie 格式转换测试
# =============================================================================


class TestIsNetscapeFormat:
    def test_recognizes_netscape_format(self):
        content = (
            "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t1712345678\tname\tvalue\n"
        )
        assert _is_netscape_format(content) is True

    def test_rejects_raw_http_cookie(self):
        content = "name1=val1; name2=val2"
        assert _is_netscape_format(content) is False

    def test_rejects_empty_string(self):
        assert _is_netscape_format("") is False

    def test_rejects_single_value(self):
        content = "name=value"
        assert _is_netscape_format(content) is False


class TestRawToNetscape:
    def test_converts_basic_cookie(self):
        result = _raw_to_netscape("name1=val1; name2=val2", "example.com")
        assert ".example.com" in result
        assert "name1" in result
        assert "name2" in result
        assert result.startswith("# Netscape HTTP Cookie File\n")

    def test_strips_cookie_prefix(self):
        result = _raw_to_netscape("Cookie: session=abc123", "example.com")
        assert "session" in result
        assert "abc123" in result

    def test_removes_quotes_from_values(self):
        result = _raw_to_netscape('session="abc123"', "example.com")
        assert "abc123" in result
        assert '"abc123"' not in result

    def test_handles_empty_pairs(self):
        result = _raw_to_netscape("a=1;;b=2;", "example.com")
        assert "a" in result
        assert "b" in result

    def test_uses_std_expiry(self):
        import time

        result = _raw_to_netscape("a=1", "example.com")
        now = int(time.time())
        lines = result.strip().splitlines()
        for line in lines:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            expiry = int(parts[4])
            expected_min = now + 364 * 86400
            expected_max = now + 366 * 86400
            assert expected_min <= expiry <= expected_max

    def test_secure_prefix_sets_secure_true(self):
        result = _raw_to_netscape("__Secure-3PSID=sid123", "youtube.com")
        lines = result.strip().splitlines()
        cookie_line = next(line for line in lines if not line.startswith("#") and line.strip())
        parts = cookie_line.split("\t")
        assert parts[3] == "TRUE", f"__Secure- cookie should have secure=TRUE, got {parts[3]}"

    def test_host_prefix_sets_secure_true(self):
        result = _raw_to_netscape("__Host-name=hostval", "youtube.com")
        lines = result.strip().splitlines()
        cookie_line = next(line for line in lines if not line.startswith("#") and line.strip())
        parts = cookie_line.split("\t")
        assert parts[3] == "TRUE", f"__Host- cookie should have secure=TRUE, got {parts[3]}"

    def test_regular_cookie_stays_secure_false(self):
        result = _raw_to_netscape("name1=val1; name2=val2", "example.com")
        lines = result.strip().splitlines()
        for line in lines:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            assert parts[3] == "FALSE", (
                f"regular cookie '{parts[5]}' should have secure=FALSE, got {parts[3]}"
            )

    def test_mixed_cookies_secure_flag(self):
        result = _raw_to_netscape("__Secure-3PSID=sid123; name1=val1", "youtube.com")
        lines = result.strip().splitlines()
        cookies = [line for line in lines if not line.startswith("#") and line.strip()]
        assert len(cookies) == 2
        secure_part = [c.split("\t") for c in cookies if c.split("\t")[5] == "__Secure-3PSID"][0]
        normal_part = [c.split("\t") for c in cookies if c.split("\t")[5] == "name1"][0]
        assert secure_part[3] == "TRUE"
        assert normal_part[3] == "FALSE"


class TestNormalizeCookieContent:
    def test_passes_through_netscape_format(self):
        content = "# Netscape HTTP Cookie File\n.domain\tTRUE\t/\tFALSE\t0\tn\tv\n"
        result = _normalize_cookie_content(content, "example.com")
        assert result == content

    def test_converts_raw_cookie(self):
        result = _normalize_cookie_content("x=1; y=2", "example.com")
        assert _is_netscape_format(result) is True
        assert "x" in result
        assert "y" in result

    def test_preserves_netscape_from_cookie_extractor(self):
        netscape = (
            "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tFALSE\t1712345678\tTEST\tvalue\n"
        )
        result = _normalize_cookie_content(netscape, "youtube.com")
        assert result == netscape

    def test_real_douyin_cookie_string(self):
        raw = "__ac_nonce=06a44f84f0; __ac_signature=_02B4Z6wo00f01xUfwg; ttwid=1%7Cabc123"
        result = _normalize_cookie_content(raw, "douyin.com")
        assert _is_netscape_format(result) is True
        assert "__ac_nonce" in result
        assert "__ac_signature" in result
        assert "ttwid" in result

    def test_returns_none_for_invalid_content(self):
        result = _normalize_cookie_content("completely invalid content", "x.com")
        assert result is None

    def test_returns_none_for_empty_string(self):
        result = _normalize_cookie_content("", "x.com")
        assert result is None


class TestSaveCookieNormalization:
    def test_save_converts_raw_cookie(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()
        init_db()

        raw_cookie = "name1=val1; name2=val2"
        save_cookie("example.com", raw_cookie)

        saved_file = tmp_path / "cookies" / "example.com.txt"
        assert saved_file.exists()
        content = saved_file.read_text()
        assert _is_netscape_format(content) is True
        assert "name1" in content
        assert "name2" in content

    def test_save_rejects_invalid(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()
        init_db()

        result = save_cookie("example.com", "garbage data without equals")
        assert result is False
        assert not (tmp_path / "cookies" / "example.com.txt").exists()


# =============================================================================
# get_cookie
# =============================================================================


class TestGetCookie:
    def setup_method(self):
        init_db()
        init_cookie_dir()

    def test_returns_existing_cookie(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()
        init_db()

        save_cookie("youtube.com", "name1=val1; name2=val2")
        result = get_cookie("youtube.com")

        assert result is not None
        assert result["domain"] == "youtube.com"
        assert "name1" in result["content"]
        assert "name2" in result["content"]
        assert "cookie_file" in result
        assert "created_at" in result
        assert "updated_at" in result

    def test_returns_none_for_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()
        init_db()

        result = get_cookie("nonexistent.com")
        assert result is None

    def test_handles_missing_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()
        init_db()

        save_cookie("example.com", "x=1")
        # Manually delete the file to simulate missing file
        (tmp_path / "cookies" / "example.com.txt").unlink()

        result = get_cookie("example.com")
        assert result is not None
        assert result["domain"] == "example.com"
        assert result["content"] == ""

    def test_normalizes_domain(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VIDZAP_COOKIE_DIR", str(tmp_path / "cookies"))
        init_cookie_dir()
        init_db()

        save_cookie("youtube.com", "x=1")
        result = get_cookie("www.YouTube.com")
        assert result is not None
        assert result["domain"] == "youtube.com"
