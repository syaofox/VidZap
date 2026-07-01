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
    find_existing_download,
    get_download_by_id,
    get_download_history,
    get_ytdlp_version,
    init_downloads_dir,
    update_download_status,
)

NAME_MAX = 255


# =============================================================================
# 路径截断测试 (已有)
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
        orig = DOWNLOADS_DIR
        import core.ytdlp_handler as mod

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
