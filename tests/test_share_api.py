"""Tests for the Android share API (main._do_share) and image proxy."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.db import init_db


class TestDoShareVideoAnalyze:
    """视频分享阶段一：分析 URL，返回可用格式（无 format_id）。"""

    def setup_method(self) -> None:
        init_db()

    @pytest.mark.asyncio
    async def test_share_video_analyze_returns_formats(self):
        """分析阶段应返回格式列表，不入队。"""
        with (
            patch("pages.home.classify_urls", return_value="video"),
            patch("core.cookie_manager.get_cookie_for_url", return_value=None),
            patch(
                "core.ytdlp_handler.extract_info",
                new_callable=AsyncMock,
                return_value={
                    "title": "Awesome Video",
                    "thumbnail": "https://example.com/thumb.jpg",
                    "duration": 300,
                    "formats": [
                        {"format_id": "137", "vcodec": "avc1", "acodec": "none",
                         "resolution": "1920x1080", "ext": "mp4", "filesize": 500},
                        {"format_id": "140", "vcodec": "none", "acodec": "mp4a",
                         "resolution": "audio only", "ext": "m4a", "filesize": 50},
                    ],
                },
            ),
            patch("main.get_suggested_formats") as mock_suggested,
        ):
            mock_suggested.return_value = [
                {"label": "1080p", "format_id": "137+140", "ext": "mp4",
                 "filesize": 550, "vcodec": "avc1", "acodec": "mp4a"},
            ]
            from main import _do_share

            result = await _do_share("https://www.youtube.com/watch?v=abc123")

        assert result["status"] == "analyzed"
        assert result["type"] == "video"
        assert result["title"] == "Awesome Video"
        assert result["thumbnail"] == "https://example.com/thumb.jpg"
        assert result["duration"] == 300
        assert len(result["formats"]) == 1
        assert result["formats"][0]["label"] == "1080p"
        assert result["formats"][0]["format_id"] == "137+140"


class TestDoShareVideoDownload:
    """视频分享阶段二：用指定格式入队下载（有 format_id）。"""

    def setup_method(self) -> None:
        init_db()

    @pytest.mark.asyncio
    async def test_share_video_with_format_id(self):
        """带 format_id 时应创建记录并用指定格式入队。"""
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

            result = await _do_share(
                "https://www.youtube.com/watch?v=abc123",
                format_id="137+140",
            )

        assert result == {
            "status": "ok",
            "download_id": 99,
            "title": "Awesome Video",
            "type": "video",
        }
        enqueue_mock.assert_awaited_once()
        assert enqueue_mock.await_args.kwargs["format_id"] == "137+140"
        assert enqueue_mock.await_args.kwargs.get("task_type", "video") == "video"

    @pytest.mark.asyncio
    async def test_share_video_download_extract_info_fails(self):
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

            result = await _do_share(
                "https://example.com/video",
                format_id="bestvideo+bestaudio/best",
            )

        assert result["status"] == "ok"
        assert result["type"] == "video"
        enqueue_mock.assert_awaited_once()
        assert enqueue_mock.await_args.kwargs["format_id"] == "bestvideo+bestaudio/best"


class TestDoShareDouyinNote:
    def setup_method(self) -> None:
        init_db()

    @pytest.mark.asyncio
    async def test_share_douyin_note_url(self):
        """分享抖音笔记应提取 note_info 并以 douyin_note 类型入队（保持原行为）。"""
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


class TestDoShareZhihu:
    def setup_method(self) -> None:
        init_db()

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.zhihu.com/question/1/answer/2",
            "https://zhuanlan.zhihu.com/p/2068622649132054152?share_code=M0kiYWKW280p",
        ],
    )
    @pytest.mark.asyncio
    async def test_share_zhihu_auto_download(self, url):
        """知乎回答/专栏分享应单阶段提取图片并以 zhihu_image 类型入队。"""
        enqueue_mock = AsyncMock()
        zhihu_info = {
            "title": "知乎内容",
            "thumbnail": "https://picx.zhimg.com/v2-abc.jpg",
            "image_urls": ["https://picx.zhimg.com/v2-abc.jpg"],
            "image_count": 1,
        }
        with (
            patch("pages.home.classify_urls", return_value="zhihu_answer"),
            patch("core.cookie_manager.get_cookie_for_url", return_value="/path/cookie.txt"),
            patch(
                "core.zhihu_answer.extract_zhihu_answer",
                new_callable=AsyncMock,
                return_value=zhihu_info,
            ),
            patch("core.ytdlp_handler.create_download_record", return_value=77),
            patch("core.download_queue.download_queue.enqueue", enqueue_mock),
        ):
            from main import _do_share

            result = await _do_share(url)

        assert result == {
            "status": "ok",
            "download_id": 77,
            "title": "知乎内容",
            "type": "zhihu_image",
        }
        enqueue_mock.assert_awaited_once()
        kwargs = enqueue_mock.await_args.kwargs
        assert kwargs["task_type"] == "zhihu_image"
        assert kwargs["format_id"] == "images"
        assert kwargs["note_info"] == zhihu_info


class TestDoShareDouban:
    def setup_method(self) -> None:
        init_db()

    @pytest.mark.asyncio
    async def test_share_douban_photo_auto_download(self):
        """豆瓣人物分享应单阶段提取图片并以 douban_photo 类型入队。"""
        enqueue_mock = AsyncMock()
        douban_info = {
            "title": "王怡仁的图片",
            "thumbnail": "https://img1.doubanio.com/view/photo/photo/public/p1.jpg",
            "image_urls": [
                "https://img1.doubanio.com/view/photo/xl/public/p1.jpg"
            ],
            "detail_urls": [
                "https://www.douban.com/personage/27499516/photo/1"
            ],
            "thumb_urls": [
                "https://img1.doubanio.com/view/photo/photo/public/p1.jpg"
            ],
            "image_count": 1,
        }
        with (
            patch("pages.home.classify_urls", return_value="douban_photo"),
            patch("core.cookie_manager.get_cookie_for_url", return_value="/path/cookie.txt"),
            patch(
                "core.douban_photo.extract_douban_photos",
                new_callable=AsyncMock,
                return_value=douban_info,
            ),
            patch("core.ytdlp_handler.create_download_record", return_value=66),
            patch("core.download_queue.download_queue.enqueue", enqueue_mock),
        ):
            from main import _do_share

            result = await _do_share(
                "https://www.douban.com/personage/27499516/photos/"
            )

        assert result == {
            "status": "ok",
            "download_id": 66,
            "title": "王怡仁的图片",
            "type": "douban_photo",
        }
        enqueue_mock.assert_awaited_once()
        kwargs = enqueue_mock.await_args.kwargs
        assert kwargs["task_type"] == "douban_photo"
        assert kwargs["format_id"] == "images"
        assert kwargs["note_info"] == douban_info

    @pytest.mark.asyncio
    async def test_share_douban_no_images_raises(self):
        """豆瓣人物页无可下载图片时应报错。"""
        with (
            patch("pages.home.classify_urls", return_value="douban_photo"),
            patch("core.cookie_manager.get_cookie_for_url", return_value=None),
            patch(
                "core.douban_photo.extract_douban_photos",
                new_callable=AsyncMock,
                return_value={
                    "title": "空相册",
                    "thumbnail": "",
                    "image_urls": [],
                    "detail_urls": [],
                    "thumb_urls": [],
                    "image_count": 0,
                },
            ),
        ):
            from main import _do_share

            with pytest.raises(ValueError, match="未能从豆瓣人物页面提取到可下载内容"):
                await _do_share(
                    "https://www.douban.com/personage/27499516/photos/"
                )


class TestDoubanImageProxy:
    """main.douban_image 豆瓣图片预览代理端点。"""

    def setup_method(self) -> None:
        init_db()

    @pytest.mark.asyncio
    async def test_proxy_success(self):
        """正常代理：服务端带 Referer 抓取豆瓣图片并返回。"""
        with patch("main.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"fakeimage"
            mock_resp.headers = {"content-type": "image/jpeg"}
            mock_client_instance.get.return_value = mock_resp

            from main import douban_image

            resp = await douban_image(
                "https://img9.doubanio.com/view/photo/photo/public/p1.jpg"
            )

        assert resp.status_code == 200
        assert resp.body == b"fakeimage"
        assert resp.media_type == "image/jpeg"
        # 服务端必须带豆瓣 Referer（AsyncClient 构造时传入）
        client_kwargs = mock_client.call_args.kwargs
        assert client_kwargs["headers"]["Referer"] == "https://www.douban.com/"

    @pytest.mark.asyncio
    async def test_proxy_rejects_non_doubanio(self):
        """非 doubanio.com 域名应拒绝（防 SSRF）。"""
        from main import douban_image

        resp = await douban_image("https://example.com/evil.jpg")
        assert resp[0]["error"] == "仅允许 doubanio.com 图片 URL"
        assert resp[1] == 400

    @pytest.mark.asyncio
    async def test_proxy_upstream_non_200(self):
        """上游非 200 时透传状态码。"""
        with patch("main.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.content = b""
            mock_resp.headers = {}
            mock_client_instance.get.return_value = mock_resp

            from main import douban_image

            resp = await douban_image(
                "https://img9.doubanio.com/view/photo/photo/public/p1.jpg"
            )

        assert resp[1] == 404

    @pytest.mark.asyncio
    async def test_proxy_network_error_502(self):
        """网络异常返回 502。"""
        with patch("main.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.side_effect = httpx.ConnectError("boom")

            from main import douban_image

            resp = await douban_image(
                "https://img9.doubanio.com/view/photo/photo/public/p1.jpg"
            )

        assert resp[1] == 502


class TestDoShareErrors:
    @pytest.mark.asyncio
    async def test_share_mixed_urls_raises(self):
        """单个 URL 不应出现 mixed，但防御性保留此检查。"""
        with patch("pages.home.classify_urls", return_value="mixed"):
            from main import _do_share

            with pytest.raises(ValueError, match="不支持的链接类型"):
                await _do_share("https://example.com/anything")

    @pytest.mark.asyncio
    async def test_share_sec_douban_url_raises(self):
        """豆瓣安全校验页链接应给出友好提示而非走 yt-dlp。"""
        from main import _do_share

        with pytest.raises(ValueError, match="sec.douban.com"):
            await _do_share(
                "https://sec.douban.com/c?r=https%3A%2F%2Fwww.douban.com%2F"
            )


class TestEnvBool:
    """main._env_bool 布尔环境变量解析。"""

    @pytest.mark.parametrize("val", ["true", "TRUE", "True", "1", "yes", "on", " ON "])
    def test_truthy_variants(self, monkeypatch, val):
        from main import _env_bool

        monkeypatch.setenv("VIDZAP_TEST_BOOL", val)
        assert _env_bool("VIDZAP_TEST_BOOL") is True

    @pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "off", ""])
    def test_falsy_variants(self, monkeypatch, val):
        from main import _env_bool

        monkeypatch.setenv("VIDZAP_TEST_BOOL", val)
        assert _env_bool("VIDZAP_TEST_BOOL") is False

    def test_default_when_unset(self, monkeypatch):
        from main import _env_bool

        monkeypatch.delenv("VIDZAP_TEST_BOOL", raising=False)
        assert _env_bool("VIDZAP_TEST_BOOL") is False
        assert _env_bool("VIDZAP_TEST_BOOL", default=True) is True

