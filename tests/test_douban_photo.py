"""Tests for core.douban_photo module."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.browser_extractor import _CancelledError as _BE_CancelledError
from core.douban_photo import (
    DoubanAccessError,
    _build_page_url,
    _extract_title,
    _guess_extension,
    _parse_cookies,
    _parse_photo_items,
    _parse_total_count,
    _resolve_original_from_detail,
    _to_original_url,
    download_douban_images,
    extract_douban_photos,
    is_douban_photo_url,
    verify_cookie,
)
from core.ytdlp_handler import DownloadCancelledError

_LIST_URL = "https://www.douban.com/personage/27499516/photos/"
_PAGE_URL = (
    "https://www.douban.com/personage/27499516/photos/?start=0&sortby=like"
)


def _list_page_html(photo_ids: list[str], total: int | None = None) -> str:
    items = "\n".join(
        f'<li data-id="{pid}">'
        f'<a href="/personage/27499516/photo/{pid}" target="_blank">'
        f'<img src="https://img{1 + i % 9}.doubanio.com/view/photo/photo/public/'
        f'p{pid}.jpg"></a>'
        f'<div class="name"></div></li>'
        for i, pid in enumerate(photo_ids)
    )
    paginator = ""
    if total is not None:
        paginator = (
            '<div class="paginator">'
            f'<span class="thispage" data-total-page="{(total + 29) // 30}">1</span>'
            f'<span class="count">(共{total}张)</span></div>'
        )
    return (
        "<html><head><title>测试人物的图片</title></head><body>"
        f'<h1>测试人物的图片</h1><ul class="pics">{items}</ul>{paginator}'
        "</body></html>"
    )


def _make_resp(
    text: str,
    status: int = 200,
    url: str = _PAGE_URL,
):
    resp = MagicMock()
    resp.text = text
    resp.status_code = status
    resp.url = url
    resp.raise_for_status = MagicMock()
    return resp


class TestIsDoubanPhotoUrl:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://www.douban.com/personage/27499516/photos/", True),
            ("https://www.douban.com/personage/27499516/photos", True),
            (
                "https://www.douban.com/personage/27499516/photos/"
                "?start=30&sortby=like",
                True,
            ),
            (
                "https://www.douban.com/personage/27499516/photos?start=0&sortby=time",
                True,
            ),
            ("http://douban.com/personage/123/photos/", True),
            # 人物主页（用户常直接粘贴）
            ("https://www.douban.com/personage/27499516/", True),
            ("https://www.douban.com/personage/27499516", True),
            ("https://www.douban.com/personage/27499516/?from=share", True),
            ("http://douban.com/personage/123", True),
            # 单页与不支持的子路径
            ("https://www.douban.com/personage/27499516/photo/812130995", False),
            ("https://www.douban.com/personage/27499516/misc", False),
            ("https://www.douban.com/personage/", False),
            ("https://www.douban.com/people/syaofox/photos/", False),
            ("https://www.zhihu.com/question/1/answer/2", False),
            ("https://www.youtube.com/watch?v=abc", False),
            ("", False),
        ],
    )
    def test_match(self, url, expected):
        assert is_douban_photo_url(url) == expected


class TestParseCookies:
    def test_no_cookie_file(self):
        assert _parse_cookies(None) == {}

    def test_file_not_found(self):
        assert _parse_cookies("/nonexistent/cookie.txt") == {}

    def test_parses_netscape_cookies(self, tmp_path):
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n"
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\tbid\tabc123\n"
            ".douban.com\tTRUE\t/\tTRUE\t1767225600\tck\tGU4Q\n"
        )
        result = _parse_cookies(str(cookie_file))
        assert result == {"bid": "abc123", "ck": "GU4Q"}

    def test_ignores_invalid_lines(self, tmp_path):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            "invalid line without tabs\n"
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\t\t\n"
        )
        result = _parse_cookies(str(cookie_file))
        assert result == {}


class TestToOriginalUrl:
    def test_thumbnail_to_original(self):
        url = "https://img9.doubanio.com/view/photo/photo/public/p812130995.jpg"
        assert (
            _to_original_url(url)
            == "https://img9.doubanio.com/view/photo/xl/public/p812130995.jpg"
        )

    def test_different_hosts(self):
        url = "https://img1.doubanio.com/view/photo/photo/public/p1.jpg"
        assert _to_original_url(url).startswith(
            "https://img1.doubanio.com/view/photo/xl/"
        )

    def test_original_preserved(self):
        url = "https://img9.doubanio.com/view/photo/xl/public/p1.jpg"
        assert _to_original_url(url) == url


class TestParsePhotoItems:
    def test_parses_items(self):
        html = _list_page_html(["812130995", "812127378"])
        items = _parse_photo_items(html)
        assert len(items) == 2
        assert items[0]["photo_id"] == "812130995"
        assert (
            items[0]["detail_url"]
            == "https://www.douban.com/personage/27499516/photo/812130995"
        )
        assert items[0]["thumbnail"] == (
            "https://img1.doubanio.com/view/photo/photo/public/p812130995.jpg"
        )
        assert items[1]["photo_id"] == "812127378"

    def test_empty_html(self):
        assert _parse_photo_items("<html></html>") == []


class TestParseTotalCount:
    def test_total_count(self):
        assert _parse_total_count('<span class="count">(共67张)</span>') == 67

    def test_no_total(self):
        assert _parse_total_count("<html></html>") is None


class TestExtractTitle:
    def test_h1(self):
        assert _extract_title("<h1>王怡仁的图片</h1>", "27499516") == "王怡仁的图片"

    def test_title_tag(self):
        assert (
            _extract_title("<title>王怡仁的图片</title>", "27499516")
            == "王怡仁的图片"
        )

    def test_fallback(self):
        assert _extract_title("<html></html>", "27499516") == "豆瓣人物图片 27499516"


class TestBuildPageUrl:
    def test_build_page_url(self):
        assert _build_page_url(
            "https://www.douban.com/personage/1/photos/", 30, "like"
        ) == "https://www.douban.com/personage/1/photos/?start=30&sortby=like"


class TestGuessExtension:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://img9.doubanio.com/view/photo/xl/public/p1.jpg", ".jpg"),
            ("https://img9.doubanio.com/view/photo/xl/public/p1.jpeg", ".jpg"),
            ("https://img9.doubanio.com/view/photo/xl/public/p1.png", ".png"),
            ("https://img9.doubanio.com/view/photo/xl/public/p1.webp", ".webp"),
            ("https://img9.doubanio.com/view/photo/xl/public/p1.gif", ".gif"),
            ("https://img9.doubanio.com/view/photo/xl/public/p1.heic", ".heic"),
            ("https://img9.doubanio.com/view/photo/xl/public/p1", ".jpg"),
        ],
    )
    def test_extension_guessing(self, url, expected):
        assert _guess_extension(url) == expected


class TestExtractDoubanPhotos:
    @pytest.mark.asyncio
    async def test_extract_single_page(self):
        html = _list_page_html(["1", "2"], total=2)
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(html)

            result = await extract_douban_photos(_LIST_URL)

        assert result["title"] == "测试人物的图片"
        assert result["image_count"] == 2
        assert result["image_urls"][0] == (
            "https://img1.doubanio.com/view/photo/xl/public/p1.jpg"
        )
        assert result["image_urls"][1] == (
            "https://img2.doubanio.com/view/photo/xl/public/p2.jpg"
        )
        assert result["detail_urls"][0] == (
            "https://www.douban.com/personage/27499516/photo/1"
        )
        assert result["thumb_urls"] == [
            "https://img1.doubanio.com/view/photo/photo/public/p1.jpg",
            "https://img2.doubanio.com/view/photo/photo/public/p2.jpg",
        ]
        assert result["thumbnail"] == result["thumb_urls"][0]

    @pytest.mark.asyncio
    async def test_extract_follows_pagination(self):
        page1 = _make_resp(_list_page_html([str(i) for i in range(1, 31)], total=67))
        page2 = _make_resp(_list_page_html([str(i) for i in range(31, 61)], total=67))
        page3 = _make_resp(_list_page_html([str(i) for i in range(61, 68)], total=67))

        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.side_effect = [page1, page2, page3]

            result = await extract_douban_photos(_LIST_URL)

        assert result["image_count"] == 67
        assert len(result["image_urls"]) == 67
        assert len(set(result["detail_urls"])) == 67
        requested = [
            c.args[0] for c in mock_client_instance.get.call_args_list
        ]
        assert requested == [
            "https://www.douban.com/personage/27499516/photos/?start=0&sortby=like",
            "https://www.douban.com/personage/27499516/photos/?start=30&sortby=like",
            "https://www.douban.com/personage/27499516/photos/?start=60&sortby=like",
        ]

    @pytest.mark.asyncio
    async def test_extract_dedups_photo_ids(self):
        page1 = _make_resp(_list_page_html([str(i) for i in range(1, 31)], total=45))
        # 第 2 页 30 个条目，其中 15 个与第 1 页重复
        page2 = _make_resp(
            _list_page_html([str(i) for i in range(16, 46)], total=45)
        )

        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.side_effect = [page1, page2]

            result = await extract_douban_photos(_LIST_URL)

        assert result["image_count"] == 45
        assert len(set(result["detail_urls"])) == 45

    @pytest.mark.asyncio
    async def test_extract_preserves_sortby_from_input(self):
        html = _list_page_html(["1"], total=1)
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(html)

            await extract_douban_photos(
                "https://www.douban.com/personage/27499516/photos/?sortby=time"
            )

        requested = [
            c.args[0] for c in mock_client_instance.get.call_args_list
        ]
        assert requested[0] == (
            "https://www.douban.com/personage/27499516/photos/?start=0&sortby=time"
        )

    @pytest.mark.asyncio
    async def test_extract_from_personage_home_url(self):
        """人物主页 URL 应转走照片列表页提取。"""
        html = _list_page_html(["1", "2"], total=2)
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(html)

            result = await extract_douban_photos(
                "https://www.douban.com/personage/27499516/"
            )

        assert result["image_count"] == 2
        requested = [
            c.args[0] for c in mock_client_instance.get.call_args_list
        ]
        assert requested[0] == (
            "https://www.douban.com/personage/27499516/photos/?start=0&sortby=like"
        )

    @pytest.mark.asyncio
    async def test_extract_no_items(self):
        html = "<html><head><title>空相册</title></head><body></body></html>"
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(html)

            result = await extract_douban_photos(_LIST_URL)

        assert result["image_count"] == 0
        assert result["image_urls"] == []
        assert result["thumbnail"] == ""

    @pytest.mark.asyncio
    async def test_extract_non_list_url_raises(self):
        with pytest.raises(ValueError, match="Not a Douban personage photo URL"):
            await extract_douban_photos(
                "https://www.douban.com/personage/27499516/photo/812130995"
            )


class TestDoubanAccessError:
    @pytest.mark.asyncio
    async def test_redirect_to_sec_without_cookie(self):
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                "<html></html>", url="https://sec.douban.com/c?r=xxx"
            )

            with pytest.raises(DoubanAccessError, match="未配置"):
                await extract_douban_photos(_LIST_URL)

    @pytest.mark.asyncio
    async def test_403_with_cookie_raises_expired_message(self, tmp_path):
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text(
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\tbid\tabc123\n"
        )
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp("", status=403)

            with pytest.raises(DoubanAccessError, match="失效"):
                await extract_douban_photos(_LIST_URL, str(cookie_file))

    @pytest.mark.asyncio
    async def test_other_status_still_raises_http_error(self):
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_resp = _make_resp("", status=500)
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "500", request=MagicMock(), response=mock_resp
            )
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = mock_resp

            with pytest.raises(httpx.HTTPStatusError):
                await extract_douban_photos(_LIST_URL)

    @pytest.mark.asyncio
    async def test_misc_sorry_bot_check_raises(self):
        """跳转 /misc/sorry 风控页（含验证码）时应抛"反爬"提示而非静默空结果。"""
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                "<html>TCaptcha</html>",
                url="https://www.douban.com/misc/sorry?original-url=xxx",
            )

            with pytest.raises(DoubanAccessError, match="反爬"):
                await extract_douban_photos(_LIST_URL)

    @pytest.mark.asyncio
    async def test_misc_sorry_in_html_raises(self):
        """即使 URL 未被重定向，HTML 含验证码脚本也应视为风控。"""
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                '<script src="https://turing.captcha.qcloud.com/TCaptcha.js"></script>'
            )

            with pytest.raises(DoubanAccessError, match="反爬"):
                await extract_douban_photos(_LIST_URL)


class TestVerifyCookie:
    @pytest.mark.asyncio
    async def test_verify_success(self, tmp_path):
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text(
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\tbid\tabc123\n"
        )
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                "<html></html>",
                url="https://www.douban.com/people/syaofox/",
            )

            assert await verify_cookie(str(cookie_file)) is True

    @pytest.mark.asyncio
    async def test_verify_403_fails(self, tmp_path):
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text(
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\tbid\tabc123\n"
        )
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp("", status=403)

            assert await verify_cookie(str(cookie_file)) is False

    @pytest.mark.asyncio
    async def test_verify_redirect_to_sec_fails(self, tmp_path):
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text(
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\tbid\tabc123\n"
        )
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                "<html></html>", url="https://sec.douban.com/c?r=xxx"
            )

            assert await verify_cookie(str(cookie_file)) is False

    @pytest.mark.asyncio
    async def test_verify_redirect_to_sorry_fails(self, tmp_path):
        """风控页（/misc/sorry）即使 200 也不应误判 Cookie 有效。"""
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text(
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\tbid\tabc123\n"
        )
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                "<html>TCaptcha</html>",
                url="https://www.douban.com/misc/sorry?original-url=xxx",
            )

            assert await verify_cookie(str(cookie_file)) is False

    @pytest.mark.asyncio
    async def test_verify_no_cookie_file(self):
        assert await verify_cookie(None) is False
        assert await verify_cookie("") is False

    @pytest.mark.asyncio
    async def test_verify_empty_cookie_file(self, tmp_path):
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text("# only comment\n")
        assert await verify_cookie(str(cookie_file)) is False

    @pytest.mark.asyncio
    async def test_verify_network_error(self, tmp_path):
        cookie_file = tmp_path / "douban.txt"
        cookie_file.write_text(
            ".douban.com\tTRUE\t/\tFALSE\t1767225600\tbid\tabc123\n"
        )
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.side_effect = httpx.ConnectError("boom")

            assert await verify_cookie(str(cookie_file)) is False


class TestResolveOriginalFromDetail:
    @pytest.mark.asyncio
    async def test_resolve_returns_zoom_href(self):
        detail_html = (
            '<a class="photo-zoom" target="_blank" '
            'href="https://nenya.doubanio.com/view/photo/xl/public/p1.jpg'
            '?sa_cv=abc&amp;sa_ct=def" rel="nofollow">查看大图</a>'
        )
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(detail_html)

            result = await _resolve_original_from_detail(
                "https://www.douban.com/personage/27499516/photo/1", None
            )

        assert result == (
            "https://nenya.doubanio.com/view/photo/xl/public/p1.jpg?sa_cv=abc&sa_ct=def"
        )

    @pytest.mark.asyncio
    async def test_resolve_none_on_access_error(self):
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                "<html></html>", url="https://sec.douban.com/c?r=xxx"
            )

            result = await _resolve_original_from_detail(
                "https://www.douban.com/personage/27499516/photo/1", None
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_none_without_zoom_link(self):
        with patch("core.douban_photo.httpx.AsyncClient") as mock_client:
            mock_client_instance = mock_client.return_value.__aenter__.return_value
            mock_client_instance.get.return_value = _make_resp(
                "<html><body>no zoom</body></html>"
            )

            result = await _resolve_original_from_detail(
                "https://www.douban.com/personage/27499516/photo/1", None
            )

        assert result is None


class TestSubsetNoteInfo:
    def _note_info(self) -> dict:
        return {
            "title": "测试人物的图片",
            "thumbnail": "https://img1.doubanio.com/view/photo/photo/public/p1.jpg",
            "image_urls": [
                "https://img1.doubanio.com/view/photo/xl/public/p1.jpg",
                "https://img2.doubanio.com/view/photo/xl/public/p2.jpg",
                "https://img3.doubanio.com/view/photo/xl/public/p3.jpg",
            ],
            "detail_urls": [
                "https://www.douban.com/personage/1/photo/1",
                "https://www.douban.com/personage/1/photo/2",
                "https://www.douban.com/personage/1/photo/3",
            ],
            "thumb_urls": [
                "https://img1.doubanio.com/view/photo/photo/public/p1.jpg",
                "https://img2.doubanio.com/view/photo/photo/public/p2.jpg",
                "https://img3.doubanio.com/view/photo/photo/public/p3.jpg",
            ],
            "image_count": 3,
        }

    def test_subset_by_indexes(self):
        """按索引裁剪后三个并行列表与 image_count 同步。"""
        from core.douban_photo import subset_note_info

        result = subset_note_info(self._note_info(), [0, 2])
        assert result["image_count"] == 2
        assert result["image_urls"] == [
            "https://img1.doubanio.com/view/photo/xl/public/p1.jpg",
            "https://img3.doubanio.com/view/photo/xl/public/p3.jpg",
        ]
        assert result["detail_urls"] == [
            "https://www.douban.com/personage/1/photo/1",
            "https://www.douban.com/personage/1/photo/3",
        ]
        assert result["thumb_urls"] == [
            "https://img1.doubanio.com/view/photo/photo/public/p1.jpg",
            "https://img3.doubanio.com/view/photo/photo/public/p3.jpg",
        ]
        assert result["title"] == "测试人物的图片"
        assert result["thumbnail"] == self._note_info()["thumbnail"]

    def test_subset_all(self):
        from core.douban_photo import subset_note_info

        result = subset_note_info(self._note_info(), [0, 1, 2])
        assert result["image_count"] == 3
        assert len(result["image_urls"]) == 3

    def test_subset_none(self):
        from core.douban_photo import subset_note_info

        result = subset_note_info(self._note_info(), [])
        assert result["image_count"] == 0
        assert result["image_urls"] == []

    def test_subset_ignores_out_of_range(self):
        """越界索引安全忽略。"""
        from core.douban_photo import subset_note_info

        result = subset_note_info(self._note_info(), [0, 99])
        assert result["image_count"] == 1
        assert len(result["image_urls"]) == 1

    def test_subset_missing_lists(self):
        """detail_urls / thumb_urls 缺失时容错为空。"""
        from core.douban_photo import subset_note_info

        note_info = {
            "title": "x",
            "thumbnail": "",
            "image_urls": ["https://img1.doubanio.com/view/photo/xl/public/p1.jpg"],
            "image_count": 1,
        }
        result = subset_note_info(note_info, [0])
        assert result["image_count"] == 1
        assert result["detail_urls"] == []
        assert result["thumb_urls"] == []


class TestDownloadDoubanImages:
    def _note_info(self, count: int = 2) -> dict:
        return {
            "title": "测试人物的图片",
            "thumbnail": "https://img1.doubanio.com/view/photo/photo/public/p1.jpg",
            "image_urls": [
                f"https://img{i}.doubanio.com/view/photo/xl/public/p{i}.jpg"
                for i in range(1, count + 1)
            ],
            "detail_urls": [
                f"https://www.douban.com/personage/27499516/photo/{i}"
                for i in range(1, count + 1)
            ],
            "thumb_urls": [
                f"https://img{i}.doubanio.com/view/photo/photo/public/p{i}.jpg"
                for i in range(1, count + 1)
            ],
            "image_count": count,
        }

    @pytest.mark.asyncio
    async def test_download_with_pre_extracted_info(self, tmp_path, monkeypatch):
        note_info = self._note_info()
        _dl_calls: list[tuple] = []

        async def _fake_dl(media_url, filepath, media_type, client, cancel_event=None):
            _dl_calls.append((media_url, filepath))
            filepath.write_bytes(b"fake_image_data")

        monkeypatch.setattr("core.douban_photo._download_media", _fake_dl)
        monkeypatch.setattr("core.douban_photo.DOWNLOADS_DIR", tmp_path)

        with patch("core.douban_photo.update_download_status") as mock_update:
            result = await download_douban_images(
                _LIST_URL,
                cookie_file=None,
                note_info=note_info,
                progress_callback=None,
                download_id=42,
            )

        assert len(_dl_calls) == 2

        result_path = Path(result)
        assert result_path.is_dir()
        files = list(result_path.iterdir())
        assert len(files) == 3  # 2 images + info.txt
        assert (result_path / "img_001.jpg").exists()
        assert (result_path / "img_002.jpg").exists()
        assert (result_path / "info.txt").exists()

        mock_update.assert_called_once_with(42, "completed", file_path=result)

    @pytest.mark.asyncio
    async def test_download_no_images_raises(self):
        note_info = {
            "title": "空相册",
            "thumbnail": "",
            "image_urls": [],
            "detail_urls": [],
            "thumb_urls": [],
            "image_count": 0,
        }

        with pytest.raises(ValueError, match="未找到可下载的图片"):
            await download_douban_images(
                _LIST_URL, cookie_file=None, note_info=note_info
            )

    @pytest.mark.asyncio
    async def test_download_cancel_event(self, tmp_path, monkeypatch):
        note_info = self._note_info(2)
        cancel_event = asyncio.Event()
        cancel_event.set()

        monkeypatch.setattr("core.douban_photo.DOWNLOADS_DIR", tmp_path)
        monkeypatch.setattr(
            "core.douban_photo._download_media",
            AsyncMock(side_effect=_BE_CancelledError),
        )

        with pytest.raises(DownloadCancelledError):
            await download_douban_images(
                _LIST_URL,
                cookie_file=None,
                note_info=note_info,
                cancel_event=cancel_event,
            )

    @pytest.mark.asyncio
    async def test_download_triggers_progress(self, tmp_path, monkeypatch):
        note_info = self._note_info(1)
        progress_calls: list[tuple[float, str, str]] = []

        def progress_cb(percent: float, speed: str, eta: str) -> None:
            progress_calls.append((percent, speed, eta))

        async def _fake_dl(*args, **kwargs):
            pass

        monkeypatch.setattr("core.douban_photo._download_media", _fake_dl)
        monkeypatch.setattr("core.douban_photo.DOWNLOADS_DIR", tmp_path)

        await download_douban_images(
            _LIST_URL,
            cookie_file=None,
            note_info=note_info,
            progress_callback=progress_cb,
        )

        assert len(progress_calls) >= 2  # progress updates + 100% final
        assert any(pct == 100.0 for pct, _, _ in progress_calls)

    @pytest.mark.asyncio
    async def test_download_fallback_to_detail_zoom(self, tmp_path, monkeypatch):
        """xl 变换下载 404 时，走单页'查看大图'链接重试。"""
        note_info = self._note_info(1)
        xl_url = note_info["image_urls"][0]
        fallback_url = (
            "https://nenya.doubanio.com/view/photo/xl/public/p1.jpg?sa_cv=x"
        )
        _dl_calls: list[str] = []

        async def _flaky_dl(
            media_url, filepath, media_type, client, cancel_event=None
        ):
            if media_url == xl_url:
                req = httpx.Request("GET", media_url)
                resp = httpx.Response(404, request=req)
                raise httpx.HTTPStatusError("404", request=req, response=resp)
            _dl_calls.append(media_url)
            filepath.write_bytes(b"fallback_data")

        monkeypatch.setattr("core.douban_photo._download_media", _flaky_dl)
        monkeypatch.setattr(
            "core.douban_photo._resolve_original_from_detail",
            AsyncMock(return_value=fallback_url),
        )
        monkeypatch.setattr("core.douban_photo.DOWNLOADS_DIR", tmp_path)

        result = await download_douban_images(
            _LIST_URL, cookie_file=None, note_info=note_info
        )

        assert _dl_calls == [fallback_url]
        assert (Path(result) / "img_001.jpg").exists()

    @pytest.mark.asyncio
    async def test_download_all_fail_raises(self, tmp_path, monkeypatch):
        """xl 失败且无可用兜底时，全部失败应抛错。"""
        note_info = self._note_info(1)

        async def _failing_dl(
            media_url, filepath, media_type, client, cancel_event=None
        ):
            req = httpx.Request("GET", media_url)
            resp = httpx.Response(404, request=req)
            raise httpx.HTTPStatusError("404", request=req, response=resp)

        monkeypatch.setattr("core.douban_photo._download_media", _failing_dl)
        monkeypatch.setattr(
            "core.douban_photo._resolve_original_from_detail",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr("core.douban_photo.DOWNLOADS_DIR", tmp_path)

        with pytest.raises(ValueError, match="所有图片下载失败"):
            await download_douban_images(
                _LIST_URL, cookie_file=None, note_info=note_info
            )
