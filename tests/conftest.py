import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


@pytest.fixture(autouse=True)
def _temp_db_dir(monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """每个测试使用独立的临时数据库目录，避免交叉污染。"""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("NICEVID_DATA_DIR", tmp)
        yield Path(tmp)
