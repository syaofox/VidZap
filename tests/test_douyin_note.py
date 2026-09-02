"""Tests for core.douyin_note."""

from unittest.mock import patch

import pytest

from core.douyin_note import download_note_images


class TestDownloadNoteImagesWithNoteInfo:
    """download_note_images() 传入 note_info 时应跳过 extract_note_images。"""

    @pytest.mark.asyncio
    async def test_uses_provided_note_info_skips_extraction(self, monkeypatch, tmp_path):
        """验证 note_info 传入后不调用 extract_note_images，直接使用已有数据。"""
        extract_called = False

        async def mock_extract(*args, **kwargs):
            nonlocal extract_called
            extract_called = True
            return {}

        monkeypatch.setattr("core.douyin_note.extract_note_images", mock_extract)

        note_info = {
            "id": "123",
            "title": "Test Douyin Note",
            "thumbnail": "https://example.com/thumb.jpg",
            "image_urls": [],
            "video_urls": [],
            "image_count": 0,
            "video_count": 0,
        }

        url = "https://www.douyin.com/note/123"

        with (
            patch("core.douyin_note.DOWNLOADS_DIR", tmp_path),
            patch("core.douyin_note.update_download_status"),
        ):
            with pytest.raises(ValueError, match="未找到可下载的媒体文件"):
                await download_note_images(
                    url=url,
                    cookie_file=None,
                    note_info=note_info,
                )

        assert not extract_called, "note_info 已提供时不应调用 extract_note_images"

    @pytest.mark.asyncio
    async def test_no_note_info_calls_extraction(self, monkeypatch, tmp_path):
        """验证 note_info 未传入时正常调用 extract_note_images。"""
        extract_called = False

        async def mock_extract(*args, **kwargs):
            nonlocal extract_called
            extract_called = True
            return {
                "id": "123",
                "title": "Test",
                "thumbnail": "",
                "image_urls": [],
                "video_urls": [],
                "image_count": 0,
                "video_count": 0,
            }

        monkeypatch.setattr("core.douyin_note.extract_note_images", mock_extract)

        url = "https://www.douyin.com/note/123"

        with (
            patch("core.douyin_note.DOWNLOADS_DIR", tmp_path),
            patch("core.douyin_note.update_download_status"),
        ):
            with pytest.raises(ValueError, match="未找到可下载的媒体文件"):
                await download_note_images(
                    url=url,
                    cookie_file=None,
                )

        assert extract_called, "note_info 未提供时应调用 extract_note_images"
