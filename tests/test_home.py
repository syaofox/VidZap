"""Tests for pages.home helper functions."""
import pytest

from core.db import init_db
from core.ytdlp_handler import create_download_record, delete_download_record
from pages.home import classify_urls, extract_urls, split_existing_urls


class TestExtractUrls:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "https://www.zhihu.com/question/1/answer/2",
                ["https://www.zhihu.com/question/1/answer/2"],
            ),
            # 标题 + URL（多行说明文字）
            (
                "夏雨为什么不选高圆圆？反而回去找袁泉？ - 林大路的回答 - 知乎\n"
                "https://www.zhihu.com/question/349262581/answer/1944275098866541196",
                [
                    "https://www.zhihu.com/question/349262581/answer/1944275098866541196"
                ],
            ),
            # 说明文字在前，URL 在中
            ("看这个回答：https://www.zhihu.com/question/1/answer/2 太精彩了",
             ["https://www.zhihu.com/question/1/answer/2"]),
            # 尾随中文标点
            ("链接：https://www.zhihu.com/question/1/answer/2。",
             ["https://www.zhihu.com/question/1/answer/2"]),
            ("https://www.zhihu.com/question/1/answer/2，",
             ["https://www.zhihu.com/question/1/answer/2"]),
            # 多个 URL 保序 + 去重
            ("https://a.com/v1 和 https://b.com/v2 和 https://a.com/v1",
             ["https://a.com/v1", "https://b.com/v2"]),
            # 批量模式：每行带说明
            ("第一行说明\nhttps://www.douban.com/personage/1/photos/\n"
             "第二行说明\nhttps://www.douban.com/personage/2/photos/",
             [
                 "https://www.douban.com/personage/1/photos/",
                 "https://www.douban.com/personage/2/photos/",
             ]),
            # 无 URL / 空输入
            ("没有任何链接", []),
            ("", []),
            (None, []),
        ],
    )
    def test_extract(self, text, expected):
        assert extract_urls(text) == expected

    def test_query_params_preserved(self):
        """URL 尾部的 query 参数不应被清洗。"""
        assert extract_urls("https://x.com/p?a=1") == ["https://x.com/p?a=1"]

    def test_extract_then_classify(self):
        """"标题 + URL"文本提取后应能正确分类为知乎图片。"""
        text = "夏雨为什么不选高圆圆？ - 林大路的回答 - 知乎\nhttps://www.zhihu.com/question/349262581/answer/1944275098866541196"
        assert classify_urls(extract_urls(text)) == "zhihu_answer"

    @pytest.mark.parametrize(
        ("text", "expected_url", "expected_type"),
        [
            # YouTube
            (
                "【4K 风景】绝美延时摄影 - YouTube\n"
                "https://www.youtube.com/watch?v=abc123DEF",
                "https://www.youtube.com/watch?v=abc123DEF",
                "video",
            ),
            # Bilibili
            (
                "【风】测试视频_哔哩哔哩_bilibili "
                "https://www.bilibili.com/video/BV1xx411c7mD",
                "https://www.bilibili.com/video/BV1xx411c7mD",
                "video",
            ),
            # 抖音图文笔记（说明文字在前，URL 后带中文句号）
            (
                "看看这个抖音图文 https://www.douyin.com/note/1234567890。",
                "https://www.douyin.com/note/1234567890",
                "douyin_note",
            ),
            # 豆瓣人物主页（URL 后带说明文字）
            (
                "豆瓣人物主页：https://www.douban.com/personage/27499516/ 收藏了",
                "https://www.douban.com/personage/27499516/",
                "douban_photo",
            ),
            # 知乎想法
            (
                "这个想法不错 https://www.zhihu.com/pin/123456789",
                "https://www.zhihu.com/pin/123456789",
                "zhihu_answer",
            ),
        ],
    )
    def test_multi_site_extract_and_classify(self, text, expected_url, expected_type):
        """所有站点（视频/抖音/豆瓣/知乎）都应支持从说明文字中自动提取 URL。"""
        urls = extract_urls(text)
        assert urls == [expected_url]
        assert classify_urls(urls) == expected_type


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
                ["https://www.douban.com/personage/27499516/photos/"],
                "douban_photo",
            ),
            (
                [
                    "https://www.douban.com/personage/27499516/photos/",
                    "https://www.douban.com/personage/123456/photos",
                ],
                "douban_photo",
            ),
            (
                ["https://www.douban.com/personage/27499516/"],
                "douban_photo",
            ),
            (
                [
                    "https://www.douban.com/personage/27499516/",
                    "https://www.douban.com/personage/123456/photos",
                ],
                "douban_photo",
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
            (
                [
                    "https://www.youtube.com/watch?v=abc",
                    "https://www.douban.com/personage/27499516/photos/",
                ],
                "mixed",
            ),
            (
                [
                    "https://www.douban.com/personage/27499516/photos/",
                    "https://www.zhihu.com/question/1/answer/2",
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
