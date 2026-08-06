"""Tests for pages.home helper functions."""
import pytest

from core.db import init_db
from core.ytdlp_handler import create_download_record, delete_download_record
from pages.home import classify_urls, split_existing_urls


class TestClassifyUrls:
    @pytest.mark.parametrize(
        ("urls", "expected"),
        [
            (["https://www.youtube.com/watch?v=abc"], "video"),
            (
                [
                    "https://www.youtube.com/watch?v=abc",
                    "https://www.bilibili.com/video/BV123",
                ],
                "video",
            ),
            (["https://www.douyin.com/note/123"], "douyin_note"),
            (
                [
                    "https://www.douyin.com/note/123",
                    "https://www.douyin.com/note/456",
                ],
                "douyin_note",
            ),
            (["https://www.zhihu.com/question/1/answer/2"], "zhihu_answer"),
            (
                [
                    "https://www.zhihu.com/question/1/answer/2",
                    "https://www.zhihu.com/question/3/answer/4",
                ],
                "zhihu_answer",
            ),
            (
                [
                    "https://zhuanlan.zhihu.com/p/2068622649132054152?"
                    "share_code=M0kiYWKW280p"
                ],
                "zhihu_answer",
            ),
            (
                [
                    "https://www.zhihu.com/pin/12345",
                    "https://zhuanlan.zhihu.com/p/123",
                ],
                "zhihu_answer",
            ),
            (
                [
                    "https://www.youtube.com/watch?v=abc",
                    "https://zhuanlan.zhihu.com/p/123",
                ],
                "mixed",
            ),
            (
                [
                    "https://www.youtube.com/watch?v=abc",
                    "https://www.zhihu.com/question/1/answer/2",
                ],
                "mixed",
            ),
            (
                [
                    "https://www.youtube.com/watch?v=abc",
                    "https://www.douyin.com/note/123",
                ],
                "mixed",
            ),
            ([], "video"),
        ],
    )
    def test_classification(self, urls, expected):
        assert classify_urls(urls) == expected


class TestSplitExistingUrls:
    def test_all_new(self):
        init_db()
        urls = ["http://example.com/a", "http://example.com/b"]
        new_urls, existing = split_existing_urls(urls)
        assert new_urls == urls
        assert existing == []

    def test_all_existing(self):
        init_db()
        url = "http://example.com/dup"
        rid = create_download_record(url, "test", "", "best")
        new_urls, existing = split_existing_urls([url])
        assert new_urls == []
        assert len(existing) == 1
        assert existing[0]["id"] == rid
        delete_download_record(rid)

    def test_mixed(self):
        init_db()
        url_existing = "http://example.com/existing"
        rid = create_download_record(url_existing, "existing", "", "best")
        url_new = "http://example.com/new"
        new_urls, existing = split_existing_urls([url_existing, url_new])
        assert new_urls == [url_new]
        assert len(existing) == 1
        assert existing[0]["id"] == rid
        delete_download_record(rid)

    def test_empty(self):
        init_db()
        new_urls, existing = split_existing_urls([])
        assert new_urls == []
        assert existing == []

    def test_duplicates_return_latest(self):
        init_db()
        url = "http://example.com/multi"
        r1 = create_download_record(url, "old", "", "best")
        r2 = create_download_record(url, "new", "", "worst")
        new_urls, existing = split_existing_urls([url])
        assert new_urls == []
        assert len(existing) == 1
        assert existing[0]["title"] == "new"
        delete_download_record(r1)
        delete_download_record(r2)
