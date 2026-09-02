"""Tests for core.zhihu_answer module."""

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.browser_extractor import _CancelledError as _BE_CancelledError
from core.ytdlp_handler import DownloadCancelledError
from core.zhihu_answer import (
    ZhihuAccessError,
    _cookies_to_netscape,
    _extract_images_from_html,
    _extract_title,
    _fallback_domain,
    _fetch_answer_page,
    _guess_extension,
    _normalize_image_url,
    _parse_cookies,
    _persist_cookie_updates,
    download_zhihu_images,
    extract_zhihu_answer,
    is_zhihu_answer_url,
    verify_cookie,
)

_ZHIHU_ANSWER_URL = "https://www.zhihu.com/question/123/answer/456"
_ZHIHU_QUESTION_URL = "https://www.zhihu.com/question/123"
_ZHIHU_ZHUANLAN_URL = "https://zhuanlan.zhihu.com/p/123"


class TestIsZhihuAnswerUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (_ZHIHU_ANSWER_URL, True),
            ("https://zhihu.com/question/1/answer/2", True),
            ("http://www.zhihu.com/question/12345/answer/67890", True),
            (_ZHIHU_QUESTION_URL, False),
            ("https://www.zhihu.com/question/123", False),
            ("https://zhuanlan.zhihu.com/p/123", False),
            ("https://www.zhihu.com/question/123/answer/", False),
            ("https://www.youtube.com/watch?v=abc", False),
            ("https://www.douyin.com/note/123", False),
            ("", False),
        ],
    )
    def test_match(self, url, expected):
        assert is_zhihu_answer_url(url) == expected


_ZHIHU_PIN_URL = "https://www.zhihu.com/pin/12345"


class TestIsZhihuPinUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (_ZHIHU_PIN_URL, True),
            ("https://www.zhihu.com/pin/2065929550513672210", True),
            ("https://zhihu.com/pin/1", True),
            ("http://www.zhihu.com/pin/999", True),
            ("https://www.zhihu.com/question/123/answer/456", False),
            ("https://zhuanlan.zhihu.com/p/123", False),
            ("https://www.youtube.com/watch?v=abc", False),
            ("", False),
        ],
    )
    def test_match(self, url, expected):
        from core.zhihu_answer import is_zhihu_pin_url

        assert is_zhihu_pin_url(url) == expected


class TestIsZhihuArticleUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            (_ZHIHU_ZHUANLAN_URL, True),
            ("https://zhuanlan.zhihu.com/p/2068622649132054152", True),
            ("http://zhuanlan.zhihu.com/p/1", True),
            (
                "https://zhuanlan.zhihu.com/p/2068622649132054152?"
                "share_code=M0kiYWKW280p&utm_psn=2068851398465396812",
                True,
            ),
            ("https://www.zhihu.com/question/123/answer/456", False),
            ("https://www.zhihu.com/pin/12345", False),
            ("https://www.zhihu.com/p/123", False),
            ("https://www.youtube.com/watch?v=abc", False),
            ("https://example.com/zhuanlan/zhihu/p/123", False),
            ("", False),
        ],
    )
    def test_match(self, url, expected):
        from core.zhihu_answer import is_zhihu_article_url

        assert is_zhihu_article_url(url) == expected


class TestIsZhihuImageUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.zhihu.com/question/1/answer/2", True),
            ("https://www.zhihu.com/pin/12345", True),
            ("https://zhuanlan.zhihu.com/p/123", True),
            ("https://www.youtube.com/watch?v=abc", False),
            ("https://www.douyin.com/note/123", False),
        ],
    )
    def test_match(self, url, expected):
        from core.zhihu_answer import is_zhihu_image_url

        assert is_zhihu_image_url(url) == expected


class TestExtractZhihuId:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.zhihu.com/question/123/answer/456", "456"),
            ("https://www.zhihu.com/pin/12345", "12345"),
            (
                "https://zhuanlan.zhihu.com/p/2068622649132054152?share_code=M0kiYWKW280p",
                "2068622649132054152",
            ),
            ("https://www.youtube.com/watch?v=abc", "unknown"),
        ],
    )
    def test_extract(self, url, expected):
        from core.zhihu_answer import _extract_zhihu_id

        assert _extract_zhihu_id(url) == expected

    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.zhihu.com/question/123/answer/456", "answer"),
            ("https://www.zhihu.com/pin/12345", "pin"),
            ("https://zhuanlan.zhihu.com/p/123", "article"),
        ],
    )
    def test_kind(self, url, expected):
        from core.zhihu_answer import _zhihu_kind

        assert _zhihu_kind(url) == expected


class TestParseCookies:
    def test_no_cookie_file(self):
        assert _parse_cookies(None) == {}

    def test_file_not_found(self):
        assert _parse_cookies("/nonexistent/cookie.txt") == {}

    def test_parses_netscape_cookies(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n"
            ".zhihu.com\tTRUE\t/\tTRUE\t1767225600\td_c0\tdef456\n"
        )
        result = _parse_cookies(str(cookie_file))
        assert result == {"z_c0": "abc123", "d_c0": "def456"}

    def test_ignores_invalid_lines(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            "invalid line without tabs\n.zhihu.com\tTRUE\t/\tFALSE\t1767225600\t\t\n"
        )
        result = _parse_cookies(str(cookie_file))
        assert result == {}


class TestExtractTitle:
    def test_og_title(self):
        html = '<meta property="og:title" content="这是一个好问题？">'
        assert _extract_title(html, _ZHIHU_ANSWER_URL) == "这是一个好问题？"

    def test_html_title(self):
        html = "<title>如何评价？ - 知乎</title>"
        assert _extract_title(html, _ZHIHU_ANSWER_URL) == "如何评价？"

    def test_fallback_to_answer_id(self):
        html = "<html></html>"
        assert _extract_title(html, _ZHIHU_ANSWER_URL) == "知乎回答 456"

    def test_fallback_to_pin_id(self):
        html = "<html></html>"
        assert _extract_title(html, "https://www.zhihu.com/pin/12345") == "知乎想法 12345"

    def test_fallback_to_article_id(self):
        html = "<html></html>"
        assert _extract_title(html, _ZHIHU_ZHUANLAN_URL) == "知乎专栏 123"

    def test_fallback_unknown(self):
        html = "<html></html>"
        assert _extract_title(html, "https://example.com") == "知乎内容"


class TestGuessExtension:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://picx.zhimg.com/v2-xxx.jpg", ".jpg"),
            ("https://picx.zhimg.com/v2-xxx.jpeg", ".jpg"),
            ("https://picx.zhimg.com/v2-xxx.png", ".png"),
            ("https://picx.zhimg.com/v2-xxx.webp", ".webp"),
            ("https://picx.zhimg.com/v2-xxx.gif", ".gif"),
            ("https://picx.zhimg.com/v2-xxx.heic", ".heic"),
            ("https://picx.zhimg.com/v2-xxx", ".jpg"),
            ("https://example.com/image?format=jpg", ".jpg"),
        ],
    )
    def test_extension_guessing(self, url, expected):
        assert _guess_extension(url) == expected


class TestNormalizeImageUrl:
    def test_thumbnail_to_original(self):
        url = "https://picx.zhimg.com/80/v2-abc.jpg"
        assert _normalize_image_url(url) == "https://picx.zhimg.com/v2-abc.jpg"

    def test_small_thumbnail_to_original(self):
        url = "https://picx.zhimg.com/50/v2-def.png"
        assert _normalize_image_url(url) == "https://picx.zhimg.com/v2-def.png"

    def test_original_preserved(self):
        url = "https://picx.zhimg.com/v2-ghi.webp"
        assert _normalize_image_url(url) == url

    def test_strips_query_params(self):
        url = "https://picx.zhimg.com/v2-abc.jpg?source=xyz"
        assert _normalize_image_url(url) == "https://picx.zhimg.com/v2-abc.jpg"

    def test_no_known_extension_returns_none(self):
        assert _normalize_image_url("https://example.com/image") is None

    def test_unknown_size_prefix(self):
        url = "https://picx.zhimg.com/200/v2-abc.jpg"
        assert _normalize_image_url(url) == "https://picx.zhimg.com/v2-abc.jpg"


class TestImageHash:
    def test_v2_hash_from_normal_url(self):
        from core.zhihu_answer import _image_hash

        assert _image_hash("https://picx.zhimg.com/v2-abc.jpg") == "v2-abc"

    def test_v2_hash_from_thumbnail(self):
        from core.zhihu_answer import _image_hash

        assert _image_hash("https://pic1.zhimg.com/80/v2-def.webp") == "v2-def"

    def test_v2_hash_from_png(self):
        from core.zhihu_answer import _image_hash

        assert _image_hash("https://pic4.zhimg.com/v2-xyz.png") == "v2-xyz"

    def test_non_zhimg_returns_none(self):
        from core.zhihu_answer import _image_hash

        assert _image_hash("https://example.com/image.jpg") is None


class TestExtractImagesFromHtml:
    def test_dedup_by_hash(self):
        """同一张图跨 CDN / 跨扩展名只保留一条。"""
        html = """
        <img data-actual="https://picx.zhimg.com/v2-abc.jpg">
        <img data-actual="https://pic1.zhimg.com/v2-abc.webp">
        <img data-actual="https://pic4.zhimg.com/v2-abc.png">
        """
        result = _extract_images_from_html(html)
        assert len(result) == 1

    def test_different_hashes_kept(self):
        """不同 hash 的图分别保留。"""
        html = """
        <img data-actual="https://picx.zhimg.com/v2-abc.jpg">
        <img data-actual="https://pic1.zhimg.com/v2-def.png">
        """
        result = _extract_images_from_html(html)
        assert len(result) == 2

    def test_prefers_data_actual(self):
        """data-actual 含有原图地址，应优先于 src 的缩略图。"""
        html = """
        <img data-actual="https://picx.zhimg.com/v2-abc.jpg" src="https://picx.zhimg.com/80/v2-abc.jpg">
        """
        result = _extract_images_from_html(html)
        assert result == ["https://picx.zhimg.com/v2-abc.jpg"]

    def test_normalizes_thumbnail_from_src(self):
        """仅 src 含缩略图时，归一化为原图地址。"""
        html = '<img src="https://picx.zhimg.com/50/v2-def.png">'
        result = _extract_images_from_html(html)
        assert result == ["https://picx.zhimg.com/v2-def.png"]

    def test_from_initial_state_json(self):
        html = """
        <script id="js-initialData" type="text/json">
        {"content": "<img src=\\"https://picx.zhimg.com/v2-abc.jpg\\">"}
        </script>
        """
        result = _extract_images_from_html(html)
        assert "https://picx.zhimg.com/v2-abc.jpg" in result

    def test_from_next_data_json(self):
        html = """
        <script id="__NEXT_DATA_INIT__" type="application/json">
        {"props": {"images": ["https://picx.zhimg.com/v2-def.png"]}}
        </script>
        """
        result = _extract_images_from_html(html)
        assert "https://picx.zhimg.com/v2-def.png" in result

    def test_empty_html(self):
        assert _extract_images_from_html("<html></html>") == []


class TestExtractZhihuAnswer:
    @pytest.mark.asyncio
    async def test_extract_returns_image_urls(self):
        html = """
        <html>
        <head><title>测试回答 - 知乎</title></head>
        <body>
        <script id="js-initialData" type="text/json">
        {"content": "<img src=\\"https://picx.zhimg.com/v2-abc.jpg\\"><img src=\\"https://pic4.zhimg.com/v2-def.png\\">"}
        </script>
        </body>
        </html>
        """
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            result = await extract_zhihu_answer(_ZHIHU_ANSWER_URL)

        assert result["title"] == "测试回答"
        assert len(result["image_urls"]) == 2
        assert "https://picx.zhimg.com/v2-abc.jpg" in result["image_urls"]
        assert "https://pic4.zhimg.com/v2-def.png" in result["image_urls"]
        assert result["image_count"] == 2
        assert result["thumbnail"] in result["image_urls"]

    @pytest.mark.asyncio
    async def test_extract_no_images(self):
        html = "<html><head><title>无图回答</title></head><body></body></html>"
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            result = await extract_zhihu_answer(_ZHIHU_ANSWER_URL)

        assert result["image_count"] == 0
        assert result["image_urls"] == []
        assert result["thumbnail"] == ""

    @pytest.mark.asyncio
    async def test_extract_normalizes_thumbnail_url(self):
        """缩略图 URL 应被归一化为原图地址。"""
        html = """
        <html>
        <head><title>Test</title></head>
        <body>
        <img src="https://picx.zhimg.com/80/v2-abc.jpg">
        <img data-actual="https://picx.zhimg.com/v2-def.png">
        </body>
        </html>
        """
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            result = await extract_zhihu_answer(_ZHIHU_ANSWER_URL)

        assert "https://picx.zhimg.com/80/v2-abc.jpg" not in result["image_urls"]
        assert "https://picx.zhimg.com/v2-abc.jpg" in result["image_urls"]
        assert "https://picx.zhimg.com/v2-def.png" in result["image_urls"]

    @pytest.mark.asyncio
    async def test_extract_uses_cookies(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n")
        html = "<html><head><title>Test</title></head><body></body></html>"

        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            await extract_zhihu_answer(_ZHIHU_ANSWER_URL, str(cookie_file))

            _, kwargs = mock_client_instance.get.call_args
            assert mock_client_instance.get.called


class TestZhihuAccessError:
    @pytest.mark.asyncio
    async def test_403_without_cookie_raises_missing_message(self):
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            with pytest.raises(ZhihuAccessError, match="未配置"):
                await extract_zhihu_answer(_ZHIHU_ANSWER_URL)

    @pytest.mark.asyncio
    async def test_403_with_cookie_raises_expired_message(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n")
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            with pytest.raises(ZhihuAccessError, match="失效"):
                await extract_zhihu_answer(_ZHIHU_ANSWER_URL, str(cookie_file))

    @pytest.mark.asyncio
    async def test_other_status_still_raises_http_error(self):
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500", request=MagicMock(), response=mock_resp
            )
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            with pytest.raises(httpx.HTTPStatusError):
                await extract_zhihu_answer(_ZHIHU_ANSWER_URL)


class TestVerifyCookie:
    @pytest.mark.asyncio
    async def test_verify_success(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n")
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            assert await verify_cookie(str(cookie_file)) is True

    @pytest.mark.asyncio
    async def test_verify_403_fails(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n")
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 403
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            assert await verify_cookie(str(cookie_file)) is False

    @pytest.mark.asyncio
    async def test_verify_no_cookie_file(self):
        assert await verify_cookie(None) is False
        assert await verify_cookie("") is False

    @pytest.mark.asyncio
    async def test_verify_empty_cookie_file(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text("# only comment\n")
        assert await verify_cookie(str(cookie_file)) is False

    @pytest.mark.asyncio
    async def test_verify_network_error(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n")
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.side_effect = httpx.ConnectError("boom")

            assert await verify_cookie(str(cookie_file)) is False


class TestCookiePersistence:
    def _make_client_with_cookies(self, initial: dict[str, str]) -> httpx.AsyncClient:
        cookies = httpx.Cookies()
        for name, value in initial.items():
            cookies.set(name, value, domain="zhihu.com", path="/")
        client = httpx.AsyncClient(cookies=cookies)
        return client

    def _apply_set_cookie(self, client: httpx.AsyncClient, set_cookie: str) -> None:
        resp = httpx.Response(
            200,
            headers={"set-cookie": set_cookie},
            request=httpx.Request("GET", "https://www.zhihu.com/"),
        )
        client.cookies.extract_cookies(resp)

    def test_fallback_domain(self):
        assert _fallback_domain("https://www.zhihu.com/question/1/answer/2") == ".zhihu.com"
        assert _fallback_domain("https://zhuanlan.zhihu.com/p/1") == ".zhuanlan.zhihu.com"
        assert _fallback_domain("not-a-url") == ".zhihu.com"

    @pytest.mark.asyncio
    async def test_cookies_to_netscape_basic(self):
        client = self._make_client_with_cookies({"z_c0": "abc"})
        result = _cookies_to_netscape(client.cookies, ".zhihu.com")
        await client.aclose()
        assert result.startswith("# Netscape HTTP Cookie File")
        assert "z_c0" in result
        assert "abc" in result

    @pytest.mark.asyncio
    async def test_cookies_to_netscape_roundtrip(self, tmp_path):
        client = self._make_client_with_cookies({"z_c0": "old"})
        self._apply_set_cookie(client, "z_c0=new; Path=/; Domain=.zhihu.com")
        cookie_file = tmp_path / "zhihu.txt"
        _persist_cookie_updates(
            client, str(cookie_file), _fallback_domain("https://www.zhihu.com/")
        )
        await client.aclose()

        content = cookie_file.read_text()
        assert content.startswith("# Netscape HTTP Cookie File")
        assert "z_c0" in content
        assert "new" in content
        assert "old" not in content
        assert _parse_cookies(str(cookie_file))["z_c0"] == "new"

    @pytest.mark.asyncio
    async def test_cookies_to_netscape_skips_expired(self, tmp_path):
        from http.cookiejar import Cookie as JarCookie

        client = self._make_client_with_cookies({"good": "1"})
        expired = JarCookie(
            version=0,
            name="expired",
            value="x",
            port=None,
            port_specified=False,
            domain="zhihu.com",
            domain_specified=True,
            domain_initial_dot=False,
            path="/",
            path_specified=True,
            secure=False,
            expires=time.time() - 10,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
        client.cookies.jar.set_cookie(expired)
        cookie_file = tmp_path / "zhihu.txt"
        _persist_cookie_updates(client, str(cookie_file), ".zhihu.com")
        await client.aclose()
        content = cookie_file.read_text()
        assert "expired" not in content
        assert "good" in content

    @pytest.mark.asyncio
    async def test_persist_no_cookie_file_noop(self):
        client = httpx.AsyncClient()
        _persist_cookie_updates(client, None, ".zhihu.com")
        _persist_cookie_updates(client, "", ".zhihu.com")
        await client.aclose()

    @pytest.mark.asyncio
    async def test_fetch_persists_set_cookie(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\told\n")
        real_cookies = httpx.Cookies()
        real_cookies.set("z_c0", "old", domain="zhihu.com", path="/")
        resp = httpx.Response(
            200,
            text="<html></html>",
            headers={"set-cookie": "z_c0=new; Path=/; Domain=.zhihu.com"},
            request=httpx.Request("GET", _ZHIHU_ANSWER_URL),
        )
        real_cookies.extract_cookies(resp)
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.cookies = real_cookies
            mock_client_instance.get.return_value = resp

            html = await _fetch_answer_page(_ZHIHU_ANSWER_URL, str(cookie_file))

        assert html == "<html></html>"
        assert _parse_cookies(str(cookie_file))["z_c0"] == "new"

    @pytest.mark.asyncio
    async def test_fetch_403_does_not_persist(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n")
        real_cookies = httpx.Cookies()
        real_cookies.set("z_c0", "abc123", domain="zhihu.com", path="/")
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.cookies = real_cookies
            resp = httpx.Response(
                403,
                headers={"set-cookie": "z_c0=; Path=/; Max-Age=0"},
                request=httpx.Request("GET", _ZHIHU_ANSWER_URL),
            )
            mock_client_instance.get.return_value = resp

            with pytest.raises(ZhihuAccessError):
                await _fetch_answer_page(_ZHIHU_ANSWER_URL, str(cookie_file))

        assert _parse_cookies(str(cookie_file))["z_c0"] == "abc123"

    @pytest.mark.asyncio
    async def test_verify_persists_set_cookie(self, tmp_path):
        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\told\n")
        real_cookies = httpx.Cookies()
        real_cookies.set("z_c0", "old", domain="zhihu.com", path="/")
        resp = httpx.Response(
            200,
            headers={"set-cookie": "z_c0=new; Path=/; Domain=.zhihu.com"},
            request=httpx.Request("GET", "https://www.zhihu.com/"),
        )
        real_cookies.extract_cookies(resp)
        with patch("core.zhihu_answer.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.cookies = real_cookies
            mock_client_instance.get.return_value = resp

            assert await verify_cookie(str(cookie_file)) is True

        assert _parse_cookies(str(cookie_file))["z_c0"] == "new"


class TestDownloadZhihuImages:
    @pytest.mark.asyncio
    async def test_download_with_pre_extracted_info(self, tmp_path, monkeypatch):
        note_info = {
            "title": "测试回答",
            "thumbnail": "https://picx.zhimg.com/v2-abc.jpg",
            "image_urls": [
                "https://picx.zhimg.com/v2-abc.jpg",
                "https://pic4.zhimg.com/v2-def.png",
            ],
            "image_count": 2,
        }

        _dl_calls: list[tuple] = []

        async def _fake_dl(media_url, filepath, media_type, client, cancel_event=None):
            _dl_calls.append((media_url, filepath, media_type))
            filepath.write_bytes(b"fake_image_data")

        monkeypatch.setattr("core.zhihu_answer._download_media", _fake_dl)
        monkeypatch.setattr("core.zhihu_answer.DOWNLOADS_DIR", tmp_path)

        with patch("core.zhihu_answer.update_download_status") as mock_update:
            result = await download_zhihu_images(
                _ZHIHU_ANSWER_URL,
                cookie_file=None,
                note_info=note_info,
                progress_callback=None,
                download_id=42,
            )

        assert len(_dl_calls) == 2, f"Expected 2 calls, got {len(_dl_calls)}"

        result_path = Path(result)
        assert result_path.is_dir()
        files = list(result_path.iterdir())
        assert len(files) == 3  # 2 images + info.txt
        assert (result_path / "img_001.jpg").exists()
        assert (result_path / "img_002.png").exists()

        mock_update.assert_called_once_with(42, "completed", file_path=result)

    @pytest.mark.asyncio
    async def test_download_zhuanlan_uses_article_dir(self, tmp_path, monkeypatch):
        """专栏文章应下载到 article_{id}_{title} 目录。"""
        note_info = {
            "title": "测试专栏",
            "thumbnail": "",
            "image_urls": ["https://picx.zhimg.com/v2-abc.jpg"],
            "image_count": 1,
        }

        async def _fake_dl(media_url, filepath, media_type, client, cancel_event=None):
            filepath.write_bytes(b"fake_image_data")

        monkeypatch.setattr("core.zhihu_answer._download_media", _fake_dl)
        monkeypatch.setattr("core.zhihu_answer.DOWNLOADS_DIR", tmp_path)

        with patch("core.zhihu_answer.update_download_status"):
            result = await download_zhihu_images(
                "https://zhuanlan.zhihu.com/p/2068622649132054152",
                cookie_file=None,
                note_info=note_info,
            )

        assert "article_2068622649132054152_测试专栏" in str(result)

    @pytest.mark.asyncio
    async def test_download_no_images_raises(self):
        note_info = {
            "title": "空回答",
            "thumbnail": "",
            "image_urls": [],
            "image_count": 0,
        }

        with pytest.raises(ValueError, match="未找到可下载的图片"):
            await download_zhihu_images(
                _ZHIHU_ANSWER_URL,
                cookie_file=None,
                note_info=note_info,
            )

    @pytest.mark.asyncio
    async def test_download_cancel_event(self, tmp_path, monkeypatch):
        note_info = {
            "title": "测试回答",
            "thumbnail": "",
            "image_urls": [
                "https://picx.zhimg.com/v2-abc.jpg",
                "https://pic4.zhimg.com/v2-def.png",
            ],
            "image_count": 2,
        }
        cancel_event = asyncio.Event()
        cancel_event.set()

        monkeypatch.setattr("core.zhihu_answer.DOWNLOADS_DIR", tmp_path)
        monkeypatch.setattr(
            "core.zhihu_answer._download_media",
            AsyncMock(side_effect=_BE_CancelledError),
        )

        with pytest.raises(DownloadCancelledError):
            await download_zhihu_images(
                _ZHIHU_ANSWER_URL,
                cookie_file=None,
                note_info=note_info,
                cancel_event=cancel_event,
            )

    @pytest.mark.asyncio
    async def test_download_triggers_progress(self, tmp_path, monkeypatch):
        note_info = {
            "title": "测试回答",
            "thumbnail": "",
            "image_urls": [
                "https://picx.zhimg.com/v2-abc.jpg",
            ],
            "image_count": 1,
        }
        progress_calls: list[tuple[float, str, str]] = []

        def progress_cb(percent: float, speed: str, eta: str) -> None:
            progress_calls.append((percent, speed, eta))

        async def _fake_dl(*args, **kwargs):
            pass

        monkeypatch.setattr("core.zhihu_answer._download_media", _fake_dl)
        monkeypatch.setattr("core.zhihu_answer.DOWNLOADS_DIR", tmp_path)

        await download_zhihu_images(
            _ZHIHU_ANSWER_URL,
            cookie_file=None,
            note_info=note_info,
            progress_callback=progress_cb,
        )

        assert len(progress_calls) >= 2  # progress updates + 100% final
        assert any(pct == 100.0 for pct, _, _ in progress_calls)


class TestPlaywrightCookiesToNetscape:
    def test_basic(self):
        from core.zhihu_answer import _playwright_cookies_to_netscape

        cookies = [
            {
                "name": "z_c0",
                "value": "abc",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": 9999999999,
                "secure": True,
            },
            {
                "name": "__zse_ck",
                "value": "xyz",
                "domain": "zhihu.com",
                "path": "/",
                "expires": -1,
                "secure": False,
            },
        ]
        result = _playwright_cookies_to_netscape(cookies, ".zhihu.com")
        assert result.startswith("# Netscape HTTP Cookie File")
        assert "z_c0" in result
        assert "__zse_ck" in result
        # secure flag
        assert ".zhihu.com\tTRUE\t/\tTRUE\t" in result  # z_c0 secure
        # session cookie expires 0
        lines = [ln for ln in result.splitlines() if "__zse_ck" in ln]
        assert lines[0].split("\t")[4] == "0"

    def test_skips_expired(self):
        from core.zhihu_answer import _playwright_cookies_to_netscape

        cookies = [
            {
                "name": "expired",
                "value": "x",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": time.time() - 100,
                "secure": False,
            },
            {
                "name": "good",
                "value": "1",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": 9999999999,
                "secure": False,
            },
        ]
        result = _playwright_cookies_to_netscape(cookies, ".zhihu.com")
        assert "expired" not in result
        assert "good" in result

    def test_fallback_domain(self):
        from core.zhihu_answer import _playwright_cookies_to_netscape

        cookies = [
            {"name": "a", "value": "1", "domain": "", "path": "/", "expires": -1, "secure": False}
        ]
        result = _playwright_cookies_to_netscape(cookies, ".zhihu.com")
        assert ".zhihu.com" in result

    def test_skips_empty_name_or_value(self):
        from core.zhihu_answer import _playwright_cookies_to_netscape

        cookies = [
            {
                "name": "",
                "value": "1",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": -1,
                "secure": False,
            },
            {
                "name": "a",
                "value": "",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": -1,
                "secure": False,
            },
        ]
        result = _playwright_cookies_to_netscape(cookies, ".zhihu.com")
        assert result.strip() == "# Netscape HTTP Cookie File"


class TestRefreshZhihuCookie:
    @pytest.mark.asyncio
    async def test_no_cookie_file(self):
        from core.zhihu_answer import refresh_zhihu_cookie

        assert await refresh_zhihu_cookie(None) is False
        assert await refresh_zhihu_cookie("") is False

    @pytest.mark.asyncio
    async def test_missing_file(self):
        from core.zhihu_answer import refresh_zhihu_cookie

        assert await refresh_zhihu_cookie("/nonexistent/cookie.txt") is False

    @pytest.mark.asyncio
    async def test_missing_d_c0(self, tmp_path):
        from core.zhihu_answer import refresh_zhihu_cookie

        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n.zhihu.com\tTRUE\t/\tFALSE\t9999999999\tz_c0\tabc123\n"
        )
        assert await refresh_zhihu_cookie(str(cookie_file)) is False

    @pytest.mark.asyncio
    async def test_success(self, tmp_path, monkeypatch):
        from core.zhihu_answer import refresh_zhihu_cookie

        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".zhihu.com\tTRUE\t/\tFALSE\t9999999999\td_c0\told_d\n"
            ".zhihu.com\tTRUE\t/\tFALSE\t9999999999\tz_c0\told_z\n"
        )
        # mock playwright
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_context.cookies.return_value = [
            {
                "name": "d_c0",
                "value": "old_d",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": 9999999999,
                "secure": False,
            },
            {
                "name": "__zse_ck",
                "value": "new_ck",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": 9999999999,
                "secure": True,
            },
            {
                "name": "z_c0",
                "value": "old_z",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": 9999999999,
                "secure": False,
            },
        ]
        mock_page.goto = AsyncMock(return_value=None)
        mock_context.add_cookies = AsyncMock(return_value=None)

        mock_async_pw = MagicMock()
        mock_async_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
        mock_async_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_stealth_cls = MagicMock()
        mock_stealth = MagicMock()
        mock_stealth.apply_stealth_async = AsyncMock(return_value=None)
        mock_stealth_cls.return_value = mock_stealth

        monkeypatch.setattr(
            "core.browser_extractor._parse_netscape_cookies",
            lambda x: [
                {
                    "name": "d_c0",
                    "value": "old_d",
                    "domain": ".zhihu.com",
                    "path": "/",
                    "secure": False,
                }
            ],
        )
        monkeypatch.setattr("core.browser_extractor._ensure_xvfb", lambda: ":99")
        # patch inside function imports
        with (
            patch("playwright.async_api.async_playwright", mock_async_pw),
            patch("playwright_stealth.Stealth", mock_stealth_cls),
        ):
            ok = await refresh_zhihu_cookie(str(cookie_file), timeout=2)

        assert ok is True
        content = cookie_file.read_text()
        assert "__zse_ck" in content
        assert "new_ck" in content

    @pytest.mark.asyncio
    async def test_timeout_no_zse_ck(self, tmp_path, monkeypatch):
        from core.zhihu_answer import refresh_zhihu_cookie

        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n.zhihu.com\tTRUE\t/\tFALSE\t9999999999\td_c0\told_d\n"
        )
        mock_pw_instance = AsyncMock()
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_context.cookies.return_value = [
            {
                "name": "d_c0",
                "value": "old_d",
                "domain": ".zhihu.com",
                "path": "/",
                "expires": 9999999999,
                "secure": False,
            },
        ]
        mock_page.goto = AsyncMock(return_value=None)
        mock_context.add_cookies = AsyncMock(return_value=None)

        mock_async_pw = MagicMock()
        mock_async_pw.return_value.__aenter__ = AsyncMock(return_value=mock_pw_instance)
        mock_async_pw.return_value.__aexit__ = AsyncMock(return_value=None)

        mock_stealth_cls = MagicMock()
        mock_stealth = MagicMock()
        mock_stealth.apply_stealth_async = AsyncMock(return_value=None)
        mock_stealth_cls.return_value = mock_stealth

        monkeypatch.setattr(
            "core.browser_extractor._parse_netscape_cookies",
            lambda x: [
                {
                    "name": "d_c0",
                    "value": "old_d",
                    "domain": ".zhihu.com",
                    "path": "/",
                    "secure": False,
                }
            ],
        )
        monkeypatch.setattr("core.browser_extractor._ensure_xvfb", lambda: ":99")

        with (
            patch("playwright.async_api.async_playwright", mock_async_pw),
            patch("playwright_stealth.Stealth", mock_stealth_cls),
        ):
            ok = await refresh_zhihu_cookie(str(cookie_file), timeout=1)

        assert ok is False
        # 文件未被改写为含 __zse_ck
        assert "__zse_ck" not in cookie_file.read_text()


class TestFetchRetryWithRefresh:
    @pytest.mark.asyncio
    async def test_retry_success_after_refresh(self, tmp_path, monkeypatch):
        """首次 403 刷新后重试成功，应返回重试后的 HTML。"""
        from core.zhihu_answer import _fetch_answer_page

        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t9999999999\td_c0\tabc123\n")

        # mock refresh returns True 且更新 cookie 文件（模拟真实刷新后文件内容仍可用）
        async def fake_refresh(path, timeout=20):
            Path(path).write_text(
                ".zhihu.com\tTRUE\t/\tFALSE\t9999999999\td_c0\tnew123\n"
                ".zhihu.com\tTRUE\t/\tFALSE\t9999999999\t__zse_ck\tnewck\n"
            )
            return True

        monkeypatch.setattr("core.zhihu_answer.refresh_zhihu_cookie", fake_refresh)

        call_count = {"n": 0}

        def _make_client(*args, **kwargs):
            # 返回一个 mock AsyncClient 实例，按调用次数返回不同响应
            mock_client = AsyncMock()
            # __aenter__ returns self
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            # cookies needed for _persist_cookie_updates (jar empty is fine)
            mock_client.cookies = httpx.Cookies()

            async def fake_get(url):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    resp = MagicMock()
                    resp.status_code = 403
                    resp.text = ""
                    resp.url = url
                    resp.raise_for_status = MagicMock()
                    return resp
                else:
                    resp = MagicMock()
                    resp.status_code = 200
                    resp.text = "<html>ok</html>"
                    resp.url = url
                    resp.raise_for_status = MagicMock()
                    return resp

            mock_client.get = fake_get
            return mock_client

        # patch AsyncClient to use our factory
        with patch("core.zhihu_answer.httpx.AsyncClient", side_effect=_make_client):
            html = await _fetch_answer_page(
                "https://www.zhihu.com/question/1/answer/2", str(cookie_file)
            )
            assert html == "<html>ok</html>"
            assert call_count["n"] == 2

    @pytest.mark.asyncio
    async def test_retry_still_403_raises_refreshed_message(self, tmp_path, monkeypatch):
        from core.zhihu_answer import ZhihuAccessError, _fetch_answer_page

        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t9999999999\td_c0\tabc123\n")

        async def fake_refresh(path, timeout=20):
            return True

        monkeypatch.setattr("core.zhihu_answer.refresh_zhihu_cookie", fake_refresh)

        def _make_client(*args, **kwargs):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.cookies = httpx.Cookies()

            async def fake_get(url):
                resp = MagicMock()
                resp.status_code = 403
                resp.url = url
                resp.raise_for_status = MagicMock()
                return resp

            mock_client.get = fake_get
            return mock_client

        with patch("core.zhihu_answer.httpx.AsyncClient", side_effect=_make_client):
            with pytest.raises(ZhihuAccessError, match="自动刷新后仍无效"):
                await _fetch_answer_page(
                    "https://www.zhihu.com/question/1/answer/2", str(cookie_file)
                )

    @pytest.mark.asyncio
    async def test_no_retry_when_refresh_fails(self, tmp_path, monkeypatch):
        from core.zhihu_answer import ZhihuAccessError, _fetch_answer_page

        cookie_file = tmp_path / "zhihu.txt"
        cookie_file.write_text(".zhihu.com\tTRUE\t/\tFALSE\t9999999999\td_c0\tabc123\n")

        async def fake_refresh(path, timeout=20):
            return False

        monkeypatch.setattr("core.zhihu_answer.refresh_zhihu_cookie", fake_refresh)

        def _make_client(*args, **kwargs):
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.cookies = httpx.Cookies()

            async def fake_get(url):
                resp = MagicMock()
                resp.status_code = 403
                resp.url = url
                resp.raise_for_status = MagicMock()
                return resp

            mock_client.get = fake_get
            return mock_client

        with patch("core.zhihu_answer.httpx.AsyncClient", side_effect=_make_client):
            with pytest.raises(ZhihuAccessError, match="已失效"):
                await _fetch_answer_page(
                    "https://www.zhihu.com/question/1/answer/2", str(cookie_file)
                )

    def test_headers_include_browser_like(self):
        from core.zhihu_answer import _ZHIHU_HEADERS

        assert "Sec-Fetch-Dest" in _ZHIHU_HEADERS
        assert "Accept-Language" in _ZHIHU_HEADERS
        assert _ZHIHU_HEADERS["Referer"] == "https://www.zhihu.com/"
