"""Tests for the Android share API (main._do_share)."""
from unittest.mock import AsyncMock, patch

import pytest

from core.db import init_db


class TestDoShareVideo:
    def setup_method(self) -> None:
        init_db()

    @pytest.mark.asyncio
    async def test_share_video_url(self):
        """分享视频链接应创建记录并用 best 格式入队。"""
        enqueue_mock = AsyncMock()
        with (
            patch("pages.home.classify_urls", return_value="video"),
            patch("core.cookie_manager.get_cookie_for_url", return_value=None),
            patch(
                "core.ytdlp_handler.extract_info",
                new_callable=AsyncMock,
                return_value={
                    "title": "Awesome Video",
                    "thumbnail": "https://example.com/thumb.jpg",
                },
            ),
            patch("core.ytdlp_handler.create_download_record", return_value=99),
            patch("core.download_queue.download_queue.enqueue", enqueue_mock),
        ):
            from main import _do_share

            result = await _do_share("https://www.youtube.com/watch?v=abc123")

        assert result == {
            "status": "ok",
            "download_id": 99,
            "title": "Awesome Video",
            "type": "video",
        }
        enqueue_mock.assert_awaited_once()
        assert enqueue_mock.await_args.kwargs["format_id"] == "bestvideo+bestaudio/best"
        assert enqueue_mock.await_args.kwargs.get("task_type", "video") == "video"

    @pytest.mark.asyncio
    async def test_share_video_extract_info_fails(self):
        """提取标题失败时仍应入队，使用 URL 作为标题。"""
        enqueue_mock = AsyncMock()
        with (
            patch("pages.home.classify_urls", return_value="video"),
            patch("core.cookie_manager.get_cookie_for_url", return_value=None),
            patch(
                "core.ytdlp_handler.extract_info",
                new_callable=AsyncMock,
                side_effect=Exception("timeout"),
            ),
            patch("core.ytdlp_handler.create_download_record", return_value=100),
            patch("core.download_queue.download_queue.enqueue", enqueue_mock),
        ):
            from main import _do_share

            result = await _do_share("https://example.com/video")

        assert result["status"] == "ok"
        assert result["type"] == "video"
        enqueue_mock.assert_awaited_once()


class TestDoShareDouyinNote:
    def setup_method(self) -> None:
        init_db()

    @pytest.mark.asyncio
    async def test_share_douyin_note_url(self):
        """分享抖音笔记应提取 note_info 并以 douyin_note 类型入队。"""
        enqueue_mock = AsyncMock()
        note_info = {
            "title": "My Douyin Note",
            "thumbnail": "https://example.com/note.jpg",
            "image_urls": ["https://example.com/img1.jpg"],
            "image_count": 1,
        }
        with (
            patch("pages.home.classify_urls", return_value="douyin_note"),
            patch("core.cookie_manager.get_cookie_for_url", return_value="/path/cookie.txt"),
            patch(
                "core.douyin_note.extract_note_images",
                new_callable=AsyncMock,
                return_value=note_info,
            ),
            patch("core.ytdlp_handler.create_download_record", return_value=88),
            patch("core.download_queue.download_queue.enqueue", enqueue_mock),
        ):
            from main import _do_share

            result = await _do_share("https://www.douyin.com/note/12345")

        assert result == {
            "status": "ok",
            "download_id": 88,
            "title": "My Douyin Note",
            "type": "douyin_note",
        }
        enqueue_mock.assert_awaited_once()
        kwargs = enqueue_mock.await_args.kwargs
        assert kwargs["task_type"] == "douyin_note"
        assert kwargs["format_id"] == "images"
        assert kwargs["note_info"] == note_info
        assert kwargs["cookie_file"] == "/path/cookie.txt"


class TestDoShareErrors:
    @pytest.mark.asyncio
    async def test_share_mixed_urls_raises(self):
        """单个 URL 不应出现 mixed，但防御性保留此检查。"""
        with patch("pages.home.classify_urls", return_value="mixed"):
            from main import _do_share

            with pytest.raises(ValueError, match="不支持的链接类型"):
                await _do_share("https://example.com/anything")
