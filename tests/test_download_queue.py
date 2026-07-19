"""Tests for core.download_queue."""
import asyncio

import pytest

from core.download_queue import DownloadQueue, DownloadTask


class TestGetOrigin:
    def test_youtube_url(self):
        q = DownloadQueue()
        assert q._get_origin("https://www.youtube.com/watch?v=abc") == "https://www.youtube.com"

    def test_douyin_url(self):
        q = DownloadQueue()
        assert q._get_origin("https://www.douyin.com/note/123") == "https://www.douyin.com"

    def test_http_url(self):
        q = DownloadQueue()
        assert q._get_origin("http://example.com/video") == "http://example.com"

    def test_trailing_path_ignored(self):
        q = DownloadQueue()
        assert q._get_origin("https://x.com/a/b/c") == "https://x.com"


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_returns_false(self):
        q = DownloadQueue()
        result = await q.cancel(999)
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_active_returns_true(self):
        q = DownloadQueue()
        q._cancel_events[42] = asyncio.Event()
        result = await q.cancel(42)
        assert result is True
        assert q._cancel_events[42].is_set()

    @pytest.mark.asyncio
    async def test_cancel_keeps_event(self):
        q = DownloadQueue()
        q._cancel_events[42] = asyncio.Event()
        await q.cancel(42)
        assert 42 in q._cancel_events


class TestIsCancelled:
    def test_returns_false_for_unknown(self):
        q = DownloadQueue()
        assert q.is_cancelled(999) is False

    def test_returns_true_when_cancelled(self):
        q = DownloadQueue()
        ev = asyncio.Event()
        ev.set()
        q._cancel_events[42] = ev
        assert q.is_cancelled(42) is True

    def test_returns_false_when_not_set(self):
        q = DownloadQueue()
        q._cancel_events[42] = asyncio.Event()
        assert q.is_cancelled(42) is False


class TestEnqueueAndShutdown:
    @pytest.mark.asyncio
    async def test_enqueue_creates_worker(self):
        q = DownloadQueue()
        await q.enqueue(
            url="https://example.com/video",
            format_id="best",
            cookie_file=None,
        )
        origin = "https://example.com"
        assert origin in q._queues
        assert origin in q._workers
        assert not q._queues[origin].empty()
        await q.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self):
        q = DownloadQueue()
        await q.enqueue(
            url="https://example.com/v",
            format_id="best",
            cookie_file=None,
        )
        await q.shutdown()
        assert len(q._queues) == 0
        assert len(q._workers) == 0

    @pytest.mark.asyncio
    async def test_same_origin_reuses_queue(self):
        q = DownloadQueue()
        await q.enqueue("https://x.com/a", "best", None)
        await q.enqueue("https://x.com/b", "worst", None)
        assert len(q._queues) == 1
        assert q._queues["https://x.com"].qsize() == 2
        await q.shutdown()

    @pytest.mark.asyncio
    async def test_different_origins_parallel(self):
        q = DownloadQueue()
        await q.enqueue("https://a.com/v", "best", None)
        await q.enqueue("https://b.com/v", "best", None)
        assert len(q._queues) == 2
        await q.shutdown()

    @pytest.mark.asyncio
    async def test_enqueue_with_download_id_sets_cancel_event(self):
        q = DownloadQueue()
        await q.enqueue(
            url="https://x.com/v",
            format_id="best",
            cookie_file=None,
            download_id=42,
        )
        # enqueue should not immediately set cancel_event (worker sets it)
        assert 42 not in q._cancel_events
        await q.shutdown()


class TestWorkerErrorHandling:
    @pytest.mark.asyncio
    async def test_cancelled_task_cleans_up(self, monkeypatch):
        """当 _worker 中的任务被取消时，cancel_events 应该被清理。"""
        import core.ytdlp_handler
        from core.ytdlp_handler import DownloadCancelledError

        async def mock_start(*args, **kwargs):
            raise DownloadCancelledError("cancelled")

        monkeypatch.setattr(
            core.ytdlp_handler, "start_download", mock_start
        )

        q = DownloadQueue()
        await q.enqueue(
            url="https://x.com/v",
            format_id="best",
            cookie_file=None,
            download_id=1,
        )
        await asyncio.sleep(0.05)
        assert 1 not in q._cancel_events
        assert 1 not in q._active_tasks
        await q.shutdown()

    @pytest.mark.asyncio
    async def test_generic_error_handling(self, monkeypatch):
        """_worker 应该捕获通用异常不崩溃。"""
        import core.ytdlp_handler

        async def mock_start(*args, **kwargs):
            raise RuntimeError("network error")

        monkeypatch.setattr(
            core.ytdlp_handler, "start_download", mock_start
        )

        q = DownloadQueue()
        await q.enqueue(
            url="https://x.com/v",
            format_id="best",
            cookie_file=None,
            download_id=2,
        )
        await asyncio.sleep(0.05)
        assert 2 not in q._cancel_events
        await q.shutdown()


class TestDownloadTaskDataclass:
    def test_default_task_type(self):
        task = DownloadTask(
            url="https://x.com/v",
            format_id="best",
            cookie_file=None,
            write_thumbnail=False,
            write_subtitles=False,
            subtitle_langs=None,
            progress_callback=None,
            download_id=None,
        )
        assert task.task_type == "video"

    def test_douyin_task_type(self):
        task = DownloadTask(
            url="https://douyin.com/note/123",
            format_id="",
            cookie_file=None,
            write_thumbnail=False,
            write_subtitles=False,
            subtitle_langs=None,
            progress_callback=None,
            download_id=1,
            task_type="douyin_note",
        )
        assert task.task_type == "douyin_note"
        assert task.download_id == 1

    def test_note_info_default_none(self):
        task = DownloadTask(
            url="https://x.com/v",
            format_id="best",
            cookie_file=None,
            write_thumbnail=False,
            write_subtitles=False,
            subtitle_langs=None,
            progress_callback=None,
            download_id=None,
        )
        assert task.note_info is None

    def test_note_info_field(self):
        info = {
            "id": "123",
            "title": "Test Note",
            "thumbnail": "https://example.com/thumb.jpg",
            "image_urls": ["https://example.com/img1.jpg"],
            "image_count": 1,
            "video_urls": [],
            "video_count": 0,
        }
        task = DownloadTask(
            url="https://douyin.com/note/123",
            format_id="images",
            cookie_file=None,
            write_thumbnail=False,
            write_subtitles=False,
            subtitle_langs=None,
            progress_callback=None,
            download_id=1,
            task_type="douyin_note",
            note_info=info,
        )
        assert task.note_info == info
        assert task.note_info["title"] == "Test Note"
