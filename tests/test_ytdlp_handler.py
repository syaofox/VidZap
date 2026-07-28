"""Tests for ytdlp_handler."""

from pathlib import Path

import pytest

from core.ytdlp_handler import (
    DOWNLOADS_DIR,
    MAX_TITLE_LENGTH,
    _format_eta,
    _format_speed,
    _is_format_error,
    _is_subtitle_error,
    _strip_subtitle_opts,
    check_ffmpeg,
    clear_completed_records,
    create_download_record,
    delete_download_record,
    extract_info,
    find_existing_download,
    get_download_by_id,
    get_download_history,
    get_suggested_formats,
    get_ytdlp_version,
    init_downloads_dir,
    normalize_url,
    start_download,
    update_download_status,
)

NAME_MAX = 255


# =============================================================================
# URL 规范化测试
# =============================================================================


class TestNormalizeUrl:
    @pytest.mark.parametrize(
        ("input_url", "expected"),
        [
            (
                "https://www.douyin.com/jingxuan?modal_id=7635097254491251362",
                "https://www.douyin.com/video/7635097254491251362",
            ),
            (
                "https://douyin.com/jingxuan?modal_id=12345",
                "https://www.douyin.com/video/12345",
            ),
            # 不匹配的 URL 原样返回
            ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
            (
                "https://www.douyin.com/video/7635097254491251362",
                "https://www.douyin.com/video/7635097254491251362",
            ),
            (
                "https://www.douyin.com/note/7635097254491251362",
                "https://www.douyin.com/note/7635097254491251362",
            ),
            # 没有 modal_id 参数
            ("https://www.douyin.com/jingxuan", "https://www.douyin.com/jingxuan"),
            ("", ""),
        ],
    )
    def test_normalization(self, input_url, expected):
        assert normalize_url(input_url) == expected


# =============================================================================
# 路径截断测试
# =============================================================================


def _make_outtmpl():
    return str(
        DOWNLOADS_DIR
        / f"%(extractor)s/%(title).{MAX_TITLE_LENGTH}s"
        / f"%(title).{MAX_TITLE_LENGTH}s.%(ext)s"
    )


def _simulate_path_components(
    outtmpl: str, *, extractor: str, title: str, ext: str
) -> dict[str, str]:
    result = outtmpl.replace("%(extractor)s", extractor)
    result = result.replace(f"%(title).{MAX_TITLE_LENGTH}s", title)
    result = result.replace("%(ext)s", ext)
    p = Path(result)
    return {
        "extractor_dir": str(p.parent.parent.name),
        "title_dir": str(p.parent.name),
        "filename": str(p.name),
    }


class TestConstants:
    def test_max_title_length_is_80(self):
        assert MAX_TITLE_LENGTH == 80

    def test_outtmpl_has_truncation_in_both_places(self):
        outtmpl = _make_outtmpl()
        token = f"%(title).{MAX_TITLE_LENGTH}s"
        assert outtmpl.count(token) == 2

    def test_outtmpl_contains_all_required_fields(self):
        outtmpl = _make_outtmpl()
        assert "%(extractor)s" in outtmpl
        assert "%(ext)s" in outtmpl


class TestPathComponentsUnderNameMax:
    @pytest.mark.parametrize(
        ("extractor", "title", "ext"),
        [
            ("youtube", "A" * 80, "mp4"),
            ("youtube", "中" * 80, "mp4"),
            ("youtube", "A" * 80, "mp4.part"),
            ("douyin", "中" * 80, "mp4"),
            ("bilibili", "A" * 80, "mkv"),
            ("youtube", "卫视禁播版，尺度超大！郭德纲张口"[:80], "mp4.part"),
        ],
    )
    def test_title_dir_under_name_max(self, extractor, title, ext):
        components = _simulate_path_components(
            _make_outtmpl(), extractor=extractor, title=title, ext=ext
        )
        title_dir_bytes = components["title_dir"].encode("utf-8")
        assert len(title_dir_bytes) < NAME_MAX

    @pytest.mark.parametrize(
        ("extractor", "title", "ext"),
        [
            ("youtube", "A" * 80, "mp4"),
            ("youtube", "中" * 80, "mp4"),
            ("youtube", "A" * 80, "mp4.part"),
            ("douyin", "中" * 80, "mp4"),
            ("bilibili", "A" * 80, "mkv"),
            ("youtube", "卫视禁播版，尺度超大！郭德纲张口"[:80], "mp4.part"),
        ],
    )
    def test_filename_under_name_max(self, extractor, title, ext):
        components = _simulate_path_components(
            _make_outtmpl(), extractor=extractor, title=title, ext=ext
        )
        filename_bytes = components["filename"].encode("utf-8")
        assert len(filename_bytes) < NAME_MAX


class TestRealWorldScenario:
    def test_real_long_chinese_title_components(self):
        long_title = (
            "卫视禁播版，尺度超大！郭德纲张口就是虎狼之词，给于谦急坏了！"
            "带你一次性看完郭德纲于谦早期荤段子合集二！｜ 德云社相声大全 ｜ "
            "#郭德纲 #于谦 #岳云鹏 #孙越 #张鹤伦 #郎鹤炎 #高峰"
        )
        assert len(long_title) > MAX_TITLE_LENGTH

        truncated = long_title[:MAX_TITLE_LENGTH]
        assert len(truncated) == MAX_TITLE_LENGTH

        components = _simulate_path_components(
            _make_outtmpl(),
            extractor="youtube",
            title=truncated,
            ext="mp4.part",
        )
        title_dir_bytes = components["title_dir"].encode("utf-8")
        assert len(title_dir_bytes) < NAME_MAX
        filename_bytes = components["filename"].encode("utf-8")
        assert len(filename_bytes) < NAME_MAX

    @pytest.mark.parametrize("title_len", [1, 10, 50, 80, 100, 200])
    def test_various_title_lengths_truncated(self, title_len):
        title = "A" * title_len
        truncated = title[:MAX_TITLE_LENGTH]
        components = _simulate_path_components(
            _make_outtmpl(),
            extractor="youtube",
            title=truncated,
            ext="mp4",
        )
        for key in ("title_dir", "filename"):
            component_bytes = components[key].encode("utf-8")
            assert len(component_bytes) < NAME_MAX


# =============================================================================
# 辅助函数测试
# =============================================================================


class TestFormatSpeed:
    @pytest.mark.parametrize(
        ("speed", "expected_part"),
        [
            (None, "N/A"),
            (0, "0 B/s"),
            (500, "500 B/s"),
            (1024, "1.0 KB/s"),
            (5 * 1024 * 1024, "5.0 MB/s"),
            (10.5 * 1024 * 1024, "10.5 MB/s"),
        ],
    )
    def test_format_speed(self, speed, expected_part):
        result = _format_speed(speed)
        assert expected_part in result


class TestFormatEta:
    @pytest.mark.parametrize(
        ("eta", "expected"),
        [
            (None, "N/A"),
            (0, "0s"),
            (30, "30s"),
            (90, "1:30"),
            (3661, "1:01:01"),
            (7200, "2:00:00"),
        ],
    )
    def test_format_eta(self, eta, expected):
        assert _format_eta(eta) == expected


class TestCheckFfmpeg:
    def test_returns_bool(self):
        result = check_ffmpeg()
        assert isinstance(result, bool)


class TestInitDownloadsDir:
    def test_creates_directory(self, tmp_path):
        target = tmp_path / "downloads"
        import core.ytdlp_handler as mod

        orig = mod.DOWNLOADS_DIR
        mod.DOWNLOADS_DIR = target
        init_downloads_dir()
        assert target.is_dir()
        mod.DOWNLOADS_DIR = orig


class TestIsFormatError:
    @pytest.mark.parametrize(
        ("msg", "expected"),
        [
            ("Requested format is not available", True),
            ("requested format is not available", True),
            ("No video formats found", True),
            ("some other error", False),
            ("", False),
        ],
    )
    def test_detection(self, msg, expected):
        assert _is_format_error(Exception(msg)) == expected


class TestIsSubtitleError:
    @pytest.mark.parametrize(
        ("msg", "expected"),
        [
            ("Unable to download video subtitles", True),
            ("unable to download video subtitles", True),
            ("Unable to download subtitles", True),
            ("some other error", False),
            ("", False),
        ],
    )
    def test_detection(self, msg, expected):
        assert _is_subtitle_error(Exception(msg)) == expected


class TestStripSubtitleOpts:
    def test_removes_subtitle_keys(self):
        opts = {
            "format": "best",
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en", "zh"],
            "quiet": True,
        }
        result = _strip_subtitle_opts(opts)
        assert "writesubtitles" not in result
        assert "writeautomaticsub" not in result
        assert "subtitleslangs" not in result
        assert result["format"] == "best"
        assert result["quiet"] is True

    def test_no_side_effect_on_original(self):
        opts = {"writesubtitles": True, "format": "best"}
        _strip_subtitle_opts(opts)
        assert "writesubtitles" in opts

    def test_handles_missing_keys(self):
        opts = {"format": "best"}
        result = _strip_subtitle_opts(opts)
        assert result == opts


# =============================================================================
# 数据库操作测试
# =============================================================================


class TestCreateDownloadRecord:
    def test_returns_int_id(self):
        from core.db import init_db

        init_db()
        rid = create_download_record(
            url="http://example.com/video",
            title="Test",
            thumbnail="http://example.com/thumb.jpg",
            format_id="best",
        )
        assert isinstance(rid, int)
        assert rid > 0

    def test_persists_record(self):
        from core.db import init_db

        init_db()
        rid = create_download_record(
            url="http://example.com/v2",
            title="Test2",
            thumbnail="",
            format_id="worst",
        )
        rec = get_download_by_id(rid)
        assert rec is not None
        assert rec["url"] == "http://example.com/v2"
        assert rec["title"] == "Test2"
        assert rec["format_id"] == "worst"
        assert rec["status"] == "downloading"


class TestUpdateDownloadStatus:
    def test_update_to_completed(self):
        from core.db import init_db

        init_db()
        rid = create_download_record("http://x.com", "x", "", "best")
        update_download_status(rid, "completed", file_path="/tmp/video.mp4")
        rec = get_download_by_id(rid)
        assert rec["status"] == "completed"
        assert rec["file_path"] == "/tmp/video.mp4"

    def test_update_to_failed_with_error(self):
        from core.db import init_db

        init_db()
        rid = create_download_record("http://x.com", "x", "", "best")
        update_download_status(rid, "failed", error_msg="connection error")
        rec = get_download_by_id(rid)
        assert rec["status"] == "failed"
        assert "connection error" in rec["error_msg"]

    def test_update_to_pending(self):
        from core.db import init_db

        init_db()
        rid = create_download_record("http://x.com", "x", "", "best")
        update_download_status(rid, "pending")
        rec = get_download_by_id(rid)
        assert rec["status"] == "pending"


class TestGetDownloadById:
    def test_returns_none_for_missing(self):
        from core.db import init_db

        init_db()
        rec = get_download_by_id(99999)
        assert rec is None

    def test_returns_record(self):
        from core.db import init_db

        init_db()
        rid = create_download_record("http://x.com", "x", "", "best")
        rec = get_download_by_id(rid)
        assert rec is not None
        assert rec["id"] == rid


class TestGetDownloadHistory:
    def test_returns_list(self):
        from core.db import init_db

        init_db()
        history = get_download_history()
        assert isinstance(history, list)

    def test_returns_records_in_order(self):
        from core.db import init_db

        init_db()
        rid1 = create_download_record("http://a.com", "a", "", "best")
        rid2 = create_download_record("http://b.com", "b", "", "best")
        history = get_download_history()
        assert len(history) >= 2
        ids = [r["id"] for r in history]
        assert rid2 in ids
        assert rid1 in ids

    def test_priority_sort(self):
        from core.db import init_db

        init_db()
        rid_dl = create_download_record("http://dl.com", "dl", "", "best")
        rid_fail = create_download_record("http://fail.com", "fail", "", "best")
        rid_done = create_download_record("http://done.com", "done", "", "best")
        update_download_status(rid_dl, "downloading")
        update_download_status(rid_fail, "failed")
        update_download_status(rid_done, "completed")

        history = get_download_history()
        ids = [r["id"] for r in history]
        # 所有记录都在结果中
        assert rid_dl in ids
        assert rid_fail in ids
        assert rid_done in ids
        # downloading 和 failed 排在 completed 前面
        dl_idx = ids.index(rid_dl)
        fail_idx = ids.index(rid_fail)
        done_idx = ids.index(rid_done)
        assert dl_idx < done_idx, "downloading 应排在 completed 前面"
        assert fail_idx < done_idx, "failed 应排在 completed 前面"

        # 同优先级内按 created_at DESC，rid_fail 先创建，所以排在后面
        assert fail_idx > dl_idx, "同优先级内最新的在前"


class TestFindExistingDownload:
    def test_finds_by_url(self):
        from core.db import init_db

        init_db()
        create_download_record("http://x.com", "x", "", "best")
        result = find_existing_download("http://x.com")
        assert result is not None
        assert result["url"] == "http://x.com"

    def test_returns_none_for_unknown(self):
        from core.db import init_db

        init_db()
        assert find_existing_download("http://unknown.com") is None

    def test_returns_latest_for_duplicate_urls(self):
        from core.db import init_db

        init_db()
        create_download_record("http://x.com", "old", "", "best")
        create_download_record("http://x.com", "new", "", "worst")
        result = find_existing_download("http://x.com")
        assert result is not None
        assert result["title"] == "new"


class TestDeleteDownloadRecord:
    def test_deletes_record(self):
        from core.db import init_db

        init_db()
        rid = create_download_record("http://x.com", "x", "", "best")
        delete_download_record(rid)
        assert get_download_by_id(rid) is None

    def test_no_error_for_missing(self):
        from core.db import init_db

        init_db()
        delete_download_record(99999)


class TestClearCompletedRecords:
    def test_clears_only_completed(self):
        from core.db import init_db

        init_db()
        r1 = create_download_record("http://a.com", "a", "", "best")
        r2 = create_download_record("http://b.com", "b", "", "best")
        r3 = create_download_record("http://c.com", "c", "", "best")
        update_download_status(r1, "completed")
        update_download_status(r2, "completed")
        update_download_status(r3, "failed")

        deleted = clear_completed_records()
        assert deleted >= 2
        assert get_download_by_id(r1) is None
        assert get_download_by_id(r2) is None
        assert get_download_by_id(r3) is not None

    def test_returns_zero_when_none(self):
        from core.db import init_db

        init_db()
        assert clear_completed_records() >= 0


class TestGetYtdlpVersion:
    def test_returns_version_string(self):
        version = get_ytdlp_version()
        assert isinstance(version, str)
        assert len(version) > 0


class TestOptsDefaults:
    """extract_info / start_download 的默认 opts 不含 extractor_args（使用 yt-dlp 默认 client）。"""

    @pytest.mark.asyncio
    async def test_extract_info_opts_structure(self, monkeypatch):
        captured_opts: dict | None = None

        def mock_io_bound(fn, url, opts):
            nonlocal captured_opts
            captured_opts = opts
            raise ValueError("mock stop")

        monkeypatch.setattr("core.ytdlp_handler.run.io_bound", mock_io_bound)

        with pytest.raises(ValueError, match="mock stop"):
            await extract_info("https://www.youtube.com/watch?v=test")

        assert captured_opts is not None
        assert "quiet" in captured_opts
        assert "noplaylist" in captured_opts
        assert "extractor_args" not in captured_opts

    @pytest.mark.asyncio
    async def test_extract_info_passes_cookiefile(self, monkeypatch):
        captured_opts: dict | None = None

        def mock_io_bound(fn, url, opts):
            nonlocal captured_opts
            captured_opts = opts
            raise ValueError("mock stop")

        monkeypatch.setattr("core.ytdlp_handler.run.io_bound", mock_io_bound)

        url = "https://www.youtube.com/watch?v=test"
        with pytest.raises(ValueError, match="mock stop"):
            await extract_info(url, cookie_file="/tmp/cookies.txt")

        assert captured_opts is not None
        assert captured_opts.get("cookiefile") == "/tmp/cookies.txt"
        assert "extractor_args" not in captured_opts

    @pytest.mark.asyncio
    async def test_start_download_normalizes_url(self, monkeypatch):
        captured_url: str | None = None

        def mock_download_sync(url, opts):
            nonlocal captured_url
            captured_url = url
            raise ValueError("mock stop")

        monkeypatch.setattr(
            "core.ytdlp_handler._download_sync", mock_download_sync
        )

        jingxuan_url = "https://www.douyin.com/jingxuan?modal_id=7635097254491251362"
        with pytest.raises(ValueError, match="mock stop"):
            await start_download(
                url=jingxuan_url,
                format_id="best",
                cookie_file=None,
            )

        assert captured_url == "https://www.douyin.com/video/7635097254491251362"

    @pytest.mark.asyncio
    async def test_start_download_opts_structure(self, monkeypatch):
        captured_opts: dict | None = None

        def mock_io_bound(fn, url, opts):
            nonlocal captured_opts
            captured_opts = opts
            raise ValueError("mock stop")

        monkeypatch.setattr("core.ytdlp_handler.run.io_bound", mock_io_bound)

        with pytest.raises(ValueError, match="mock stop"):
            await start_download(
                url="https://www.youtube.com/watch?v=test",
                format_id="best",
                cookie_file=None,
            )

        assert captured_opts is not None
        assert "quiet" in captured_opts
        assert "noplaylist" in captured_opts
        assert "format" in captured_opts
        assert "extractor_args" not in captured_opts


# =============================================================================
# get_suggested_formats 测试
# =============================================================================


class TestGetSuggestedFormats:
    """get_suggested_formats() 在各种格式输入下的行为。"""

    def _make_format(
        self,
        format_id: str,
        resolution: str = "1920x1080",
        ext: str = "mp4",
        filesize: int = 100,
        vcodec: str = "avc1",
        acodec: str = "mp4a",
    ) -> dict:
        return {
            "format_id": format_id,
            "resolution": resolution,
            "ext": ext,
            "filesize": filesize,
            "vcodec": vcodec,
            "acodec": acodec,
        }

    def test_combined_formats_no_ffmpeg(self, monkeypatch):
        """combined 格式（vcodec+acodec 都有）在无 ffmpeg 时返回推荐列表。"""
        monkeypatch.setattr("core.ytdlp_handler.check_ffmpeg", lambda: False)
        formats = [
            self._make_format("hd", resolution="1920x1080"),
            self._make_format("sd", resolution="640x360"),
        ]
        result = get_suggested_formats(formats)
        assert len(result) == 2
        assert result[0]["label"] == "1080p"
        assert result[1]["label"] == "360p"

    def test_unknown_codec_formats_no_ffmpeg(self, monkeypatch):
        """未知 codec（vcodec/acodec 均为 none）在无 ffmpeg 时也应返回推荐。"""
        monkeypatch.setattr("core.ytdlp_handler.check_ffmpeg", lambda: False)
        formats = [
            self._make_format("low", resolution="640x360", vcodec="none", acodec="none"),
            self._make_format("high", resolution="1920x1080", vcodec="none", acodec="none"),
        ]
        result = get_suggested_formats(formats)
        assert len(result) == 2
        assert result[0]["label"] == "1080p"
        assert result[1]["label"] == "360p"

    def test_unknown_codec_with_ffmpeg_uses_fallback(self, monkeypatch):
        """未知 codec + 有 ffmpeg 但无 video_only，应走 else fallback 并返回推荐。"""
        monkeypatch.setattr("core.ytdlp_handler.check_ffmpeg", lambda: True)
        formats = [
            self._make_format("low", resolution="640x360", vcodec="none", acodec="none"),
            self._make_format("high", resolution="1920x1080", vcodec="none", acodec="none"),
        ]
        result = get_suggested_formats(formats)
        assert len(result) == 2
        assert result[0]["label"] == "1080p"
        assert result[1]["label"] == "360p"

    def test_mixed_formats_with_ffmpeg(self, monkeypatch):
        """混合格式 + 有 ffmpeg + 有 video_only，走 ffmpeg merge 路径。"""
        monkeypatch.setattr("core.ytdlp_handler.check_ffmpeg", lambda: True)
        formats = [
            self._make_format(
                "vid-1080", resolution="1920x1080",
                vcodec="avc1", acodec="none", filesize=200,
            ),
            self._make_format(
                "vid-720", resolution="1280x720",
                vcodec="avc1", acodec="none", filesize=100,
            ),
            self._make_format(
                "aud-best", resolution="0x0", ext="m4a",
                vcodec="none", acodec="mp4a", filesize=50,
            ),
        ]
        result = get_suggested_formats(formats)
        # 两条 video_only + 一条音频 → 1080p+audio、720p+audio、仅音频
        assert len(result) == 3
        assert "1080p" in [r["label"] for r in result]
        assert "720p" in [r["label"] for r in result]
        assert "仅音频" in [r["label"] for r in result]

    def test_empty_formats(self, monkeypatch):
        """空 formats 列表应返回空列表。"""
        monkeypatch.setattr("core.ytdlp_handler.check_ffmpeg", lambda: False)
        assert get_suggested_formats([]) == []
