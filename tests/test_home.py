"""Tests for pages.home helper functions."""
import pytest

from core.db import init_db
from core.ytdlp_handler import create_download_record, delete_download_record
from pages.home import classify_urls, extract_urls, split_existing_urls


class TestReadEventModifiers:
    @staticmethod
    def _read(args):
        from pages.home import _read_event_modifiers

        class E:
            def __init__(self, args):
                self.args = args

        return _read_event_modifiers(E(args))

    def test_dict_args(self):
        assert self._read({"shiftKey": True, "ctrlKey": False}) == (True, False)
        assert self._read({"shiftKey": False, "ctrlKey": True}) == (False, True)
        assert self._read({"shiftKey": False, "ctrlKey": False}) == (False, False)

    def test_sequence_args(self):
        """兼容 e.args 为值序列的情况。"""
        assert self._read([True, False]) == (True, False)
        assert self._read([False, True]) == (False, True)

    def test_missing_or_short(self):
        assert self._read(None) == (False, False)
        assert self._read({}) == (False, False)
        assert self._read([True]) == (False, False)


class TestApplySelectionAction:
    @staticmethod
    def _apply(state, anchor, idx, shift):
        from pages.home import _apply_selection_action
        return _apply_selection_action(state, anchor, idx, shift)

    def test_toggle_single(self):
        state = [True, True, True]
        new_state, anchor = self._apply(state, 0, 1, shift=False)
        assert new_state == [True, False, True]
        assert anchor == 1

    def test_toggle_back(self):
        state = [True, False, True]
        new_state, anchor = self._apply(state, 1, 1, shift=False)
        assert new_state == [True, True, True]
        assert anchor == 1

    def test_shift_range_from_selected_anchor(self):
        """锚点选中时 Shift 范围全部选中，且锚点不移动。"""
        state = [True, False, False, True]
        new_state, anchor = self._apply(state, 0, 2, shift=True)
        assert new_state == [True, True, True, True]
        assert anchor == 0

    def test_shift_range_from_unselected_anchor(self):
        """锚点未选中时 Shift 范围全部取消。"""
        state = [True, False, False, True]
        new_state, anchor = self._apply(state, 1, 3, shift=True)
        assert new_state == [True, False, False, False]
        assert anchor == 1

    def test_shift_reversed_range(self):
        """锚点大于点击索引（反向范围）同样成立。"""
        state = [False, False, True, True]
        new_state, anchor = self._apply(state, 3, 1, shift=True)
        assert new_state == [False, True, True, True]
        assert anchor == 3

    def test_shift_single_index_is_anchor(self):
        """Shift 点击锚点自身：状态不变。"""
        state = [True, False, True]
        new_state, anchor = self._apply(state, 1, 1, shift=True)
        assert new_state == state
        assert anchor == 1

    def test_original_state_untouched(self):
        """函数不应修改传入的 state（返回新列表）。"""
        state = [True, False]
        self._apply(state, 0, 1, shift=False)
        assert state == [True, False]


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
