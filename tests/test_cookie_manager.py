"""Tests for core.cookie_manager."""
import pytest

from core.cookie_manager import (
    _is_netscape_format,
    _normalize_cookie_content,
    _raw_to_netscape,
    delete_cookie,
    extract_domain_from_input,
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
            ("a..b.com", False),  # empty label
            ("a." + "x" * 64 + ".com", False),  # label > 63 chars
        ],
    )
    def test_validity(self, domain, expected):
        assert is_valid_domain(domain) == expected


# =============================================================================
# init_cookie_dir
# =============================================================================


class TestInitCookieDir:
    def test_creates_directory(self, tmp_path):
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        target = tmp_path / "cookies"
        cm.COOKIES_DIR = target
        init_cookie_dir()
        assert target.is_dir()
        cm.COOKIES_DIR = orig


# =============================================================================
# save_cookie / list_cookies / delete_cookie
# =============================================================================


class TestCookieRoundtrip:
    def setup_method(self):
        init_db()
        init_cookie_dir()

    def test_save_and_list(self, tmp_path):
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"

        saved = save_cookie("youtube.com", "NETSCAPE cookie content")
        assert saved is True

        cookies = list_cookies()
        assert len(cookies) >= 1
        domains = [c["domain"] for c in cookies]
        assert "youtube.com" in domains

        cm.COOKIES_DIR = orig

    def test_save_duplicate_updates(self, tmp_path):
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"

        save_cookie("youtube.com", "content1")
        save_cookie("youtube.com", "content2")
        cookies = list_cookies()

        youtube_entries = [c for c in cookies if c["domain"] == "youtube.com"]
        assert len(youtube_entries) == 1

        cm.COOKIES_DIR = orig

    def test_delete_cookie(self, tmp_path):
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"

        save_cookie("test.example.com", "content")
        assert any(c["domain"] == "test.example.com" for c in list_cookies())

        deleted = delete_cookie("test.example.com")
        assert deleted is True
        assert not any(c["domain"] == "test.example.com" for c in list_cookies())

        cm.COOKIES_DIR = orig

    def test_delete_nonexistent(self):
        result = delete_cookie("nonexistent.domain.com")
        assert result is True


# =============================================================================
# get_cookie_for_url
# =============================================================================


class TestGetCookieForUrl:
    def setup_method(self):
        init_db()

    def test_exact_match(self, tmp_path):
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"

        save_cookie("youtube.com", "cookie_data")
        result = get_cookie_for_url("https://www.youtube.com/watch?v=abc")
        assert result is not None
        assert "youtube.com.txt" in result

        cm.COOKIES_DIR = orig

    def test_subdomain_match(self, tmp_path):
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"

        save_cookie("youtube.com", "cookie_data")
        result = get_cookie_for_url("https://m.youtube.com/shorts/abc")
        assert result is not None

        cm.COOKIES_DIR = orig

    def test_no_match_returns_none(self):
        result = get_cookie_for_url("https://unknown-site.example.com/video")
        assert result is None

    def test_multiple_cookies_returns_correct(self, tmp_path):
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"

        save_cookie("bilibili.com", "bilibili_data")
        save_cookie("youtube.com", "youtube_data")
        result = get_cookie_for_url("https://www.youtube.com/watch?v=abc")
        assert result is not None
        assert "youtube" in result

        cm.COOKIES_DIR = orig

    def test_reverse_subdomain_match(self, tmp_path):
        """反向匹配: youtube.com 匹配保存的 m.youtube.com cookie。"""
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"

        save_cookie("m.youtube.com", "m_youtube_data")
        result = get_cookie_for_url("https://youtube.com/watch?v=abc")
        assert result is not None
        assert "m.youtube.com" in result

        cm.COOKIES_DIR = orig

    def test_normalize_handles_userinfo(self, tmp_path):
        """带 userinfo 的 URL 也能正确提取域名。"""
        result = get_cookie_for_url("https://user:pass@youtube.com/watch?v=abc")
        assert result is None  # 没有保存 cookie, 返回 None


# =============================================================================
# Cookie 格式转换测试
# =============================================================================


class TestIsNetscapeFormat:
    def test_recognizes_netscape_format(self):
        content = (
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tFALSE\t1712345678\tname\tvalue\n"
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
        """原始 Cookie 中的引号应被去除。"""
        result = _raw_to_netscape('session="abc123"', "example.com")
        assert "abc123" in result
        assert '"abc123"' not in result

    def test_handles_empty_pairs(self):
        """空的分号段应被忽略。"""
        result = _raw_to_netscape("a=1;;b=2;", "example.com")
        assert "a" in result
        assert "b" in result

    def test_uses_std_expiry(self):
        """生成的 Cookie 应有合理过期时间（当前时间 + 1年）。"""
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
        """从浏览器导出的 Netscape 格式应原样保留。"""
        netscape = (
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tFALSE\t1712345678\tTEST\tvalue\n"
        )
        result = _normalize_cookie_content(netscape, "youtube.com")
        assert result == netscape

    def test_real_douyin_cookie_string(self):
        """模拟用户从浏览器复制粘贴的抖音 Cookie 字符串。"""
        raw = "__ac_nonce=06a44f84f0; __ac_signature=_02B4Z6wo00f01xUfwg; ttwid=1%7Cabc123"
        result = _normalize_cookie_content(raw, "douyin.com")
        assert _is_netscape_format(result) is True
        assert "__ac_nonce" in result
        assert "__ac_signature" in result
        assert "ttwid" in result


class TestSaveCookieNormalization:
    def test_save_converts_raw_cookie(self, tmp_path):
        """save_cookie 应自动转换原始 Cookie 字符串。"""
        import core.cookie_manager as cm

        orig = cm.COOKIES_DIR
        cm.COOKIES_DIR = tmp_path / "cookies"
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

        cm.COOKIES_DIR = orig
