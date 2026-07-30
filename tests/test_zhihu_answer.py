"""Tests for core.zhihu_answer module."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.browser_extractor import _CancelledError as _BE_CancelledError
from core.ytdlp_handler import DownloadCancelledError
from core.zhihu_answer import (
    _extract_images_from_html,
    _extract_title,
    _guess_extension,
    _normalize_image_url,
    _parse_cookies,
    download_zhihu_images,
    extract_zhihu_answer,
    is_zhihu_answer_url,
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
            "invalid line without tabs\n"
            ".zhihu.com\tTRUE\t/\tFALSE\t1767225600\t\t\n"
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
        html = '''
        <img data-actual="https://picx.zhimg.com/v2-abc.jpg">
        <img data-actual="https://pic1.zhimg.com/v2-abc.webp">
        <img data-actual="https://pic4.zhimg.com/v2-abc.png">
        '''
        result = _extract_images_from_html(html)
        assert len(result) == 1

    def test_different_hashes_kept(self):
        """不同 hash 的图分别保留。"""
        html = '''
        <img data-actual="https://picx.zhimg.com/v2-abc.jpg">
        <img data-actual="https://pic1.zhimg.com/v2-def.png">
        '''
        result = _extract_images_from_html(html)
        assert len(result) == 2

    def test_prefers_data_actual(self):
        """data-actual 含有原图地址，应优先于 src 的缩略图。"""
        html = '''
        <img data-actual="https://picx.zhimg.com/v2-abc.jpg" src="https://picx.zhimg.com/80/v2-abc.jpg">
        '''
        result = _extract_images_from_html(html)
        assert result == ["https://picx.zhimg.com/v2-abc.jpg"]

    def test_normalizes_thumbnail_from_src(self):
        """仅 src 含缩略图时，归一化为原图地址。"""
        html = '<img src="https://picx.zhimg.com/50/v2-def.png">'
        result = _extract_images_from_html(html)
        assert result == ["https://picx.zhimg.com/v2-def.png"]

    def test_from_initial_state_json(self):
        html = '''
        <script id="js-initialData" type="text/json">
        {"content": "<img src=\\"https://picx.zhimg.com/v2-abc.jpg\\">"}
        </script>
        '''
        result = _extract_images_from_html(html)
        assert "https://picx.zhimg.com/v2-abc.jpg" in result

    def test_from_next_data_json(self):
        html = '''
        <script id="__NEXT_DATA_INIT__" type="application/json">
        {"props": {"images": ["https://picx.zhimg.com/v2-def.png"]}}
        </script>
        '''
        result = _extract_images_from_html(html)
        assert "https://picx.zhimg.com/v2-def.png" in result

    def test_empty_html(self):
        assert _extract_images_from_html("<html></html>") == []


class TestExtractZhihuAnswer:
    @pytest.mark.asyncio
    async def test_extract_returns_image_urls(self):
        html = '''
        <html>
        <head><title>测试回答 - 知乎</title></head>
        <body>
        <script id="js-initialData" type="text/json">
        {"content": "<img src=\\"https://picx.zhimg.com/v2-abc.jpg\\"><img src=\\"https://pic4.zhimg.com/v2-def.png\\">"}
        </script>
        </body>
        </html>
        '''
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
        html = '''
        <html>
        <head><title>Test</title></head>
        <body>
        <img src="https://picx.zhimg.com/80/v2-abc.jpg">
        <img data-actual="https://picx.zhimg.com/v2-def.png">
        </body>
        </html>
        '''
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
        cookie_file.write_text(
            ".zhihu.com\tTRUE\t/\tFALSE\t1767225600\tz_c0\tabc123\n"
        )
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
