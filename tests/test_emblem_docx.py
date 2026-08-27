import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from emblem import replace_emblems_in_docx_bytes


def test_docx_in_memory_output_is_real_zip():
    src_path = Path("/mnt/data/v6work/test_emblem.docx")
    tpl_path = Path("/mnt/data/1000039602.png")
    new_path = Path("/mnt/data/new_emblem.png")
    if not (src_path.exists() and tpl_path.exists() and new_path.exists()):
        return
    out, report = replace_emblems_in_docx_bytes(
        src_path.read_bytes(), tpl_path.read_bytes(), new_path.read_bytes(),
        min_confidence=.35,
    )
    assert report["total_media"] >= 1
    assert len(out) > 1000
    with zipfile.ZipFile(io.BytesIO(out), "r") as zf:
        assert zf.testzip() is None
        assert any(n.startswith("word/media/") for n in zf.namelist())
