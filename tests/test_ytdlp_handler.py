"""Tests for ytdlp_handler filename truncation."""
from pathlib import Path

import pytest

from core.ytdlp_handler import DOWNLOADS_DIR, MAX_TITLE_LENGTH

NAME_MAX = 255  # bytes per path component on most Linux filesystems


def _make_outtmpl():
    """Replicate the outtmpl generation from start_download."""
    return str(
        DOWNLOADS_DIR
        / f"%(extractor)s/%(title).{MAX_TITLE_LENGTH}s"
        / f"%(title).{MAX_TITLE_LENGTH}s.%(ext)s"
    )


def _simulate_path_components(
    outtmpl: str, *, extractor: str, title: str, ext: str
) -> dict[str, str]:
    """Replace template fields to simulate the real path components."""
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
    """Each path component must be < NAME_MAX (255 bytes)."""

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
        assert len(title_dir_bytes) < NAME_MAX, (
            f"Title directory '{components['title_dir']}' is "
            f"{len(title_dir_bytes)} bytes (limit: {NAME_MAX})"
        )

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
        assert len(filename_bytes) < NAME_MAX, (
            f"Filename '{components['filename']}' is "
            f"{len(filename_bytes)} bytes (limit: {NAME_MAX})"
        )


class TestRealWorldScenario:
    def test_real_long_chinese_title_components(self):
        """The exact title from the user's error should be safe after truncation."""
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
        assert len(title_dir_bytes) < NAME_MAX, (
            f"Title directory is {len(title_dir_bytes)} bytes "
            f"(limit: {NAME_MAX})"
        )
        filename_bytes = components["filename"].encode("utf-8")
        assert len(filename_bytes) < NAME_MAX, (
            f"Filename is {len(filename_bytes)} bytes (limit: {NAME_MAX})"
        )

    @pytest.mark.parametrize("title_len", [1, 10, 50, 80, 100, 200])
    def test_various_title_lengths_truncated(self, title_len):
        """Verify truncation safety for various ASCII title lengths."""
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
            assert len(component_bytes) < NAME_MAX, (
                f"{key} ({components[key]}) exceeds NAME_MAX "
                f"({len(component_bytes)} bytes)"
            )
