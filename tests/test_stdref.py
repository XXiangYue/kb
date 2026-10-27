"""Standards-text references (synthetic texts only; no standards text in the repo)."""
import json
import subprocess
from pathlib import Path

import pytest

from tushen_kb import stdref
from tushen_kb.stdref import TOKEN, Reference

ROOT = Path(__file__).resolve().parent.parent
DOC = ("1.0.1 为规范测试工作，制定本规范。\n3.1.2 消防测试井的有效容量不应小于两立方米，"
       "且应设置独立的测试\n排水管道，管道坡度不应小于千分之五。")


@pytest.fixture()
def ref():
    return Reference({"D1": DOC}, {"D1": [(0, "1.0.1"), (DOC.index("3.1.2"), "3.1.2")]})


def test_redact_restores_exactly_with_whitespace_and_punctuation(ref):
    s = "答：消防测试井的有效容量不应小于两立方米,且应设置独立的测试排水管道。其余为模型自写的话。"
    red, n = ref.redact(s)
    assert n > 0 and "有效容量" not in red
    m = TOKEN.search(red)
    assert m and m.group(1) == "D1" and m.group(2) == "3.1.2"
    assert ref.resolve(red) == s                        # byte-exact, incl. ASCII comma and removed line break
    assert ref.resolve_obj({"a": [red]}) == {"a": [s]}


def test_short_overlap_not_redacted(ref):
    s = "制定本规范的目的"                                  # < 12 shared chars
    assert ref.redact(s) == (s, 0)


def test_mismatched_local_text_raises(ref):
    red, _ = ref.redact("消防测试井的有效容量不应小于两立方米")
    other = Reference({"D1": DOC.replace("两立方米", "三立方米")})
    with pytest.raises(ValueError):
        other.resolve(red)


def test_scan_counts_windows(ref):
    assert ref.scan_text("消防测试井的有效容量不应小于两立方米，且应设置", k=20)["windows"] >= 1
    assert ref.scan_text("完全无关的一句话，没有重合。" * 3, k=20)["windows"] == 0
    red, _ = ref.redact("消防测试井的有效容量不应小于两立方米，且应设置独立的测试排水管道")
    assert ref.scan_text(red, k=20)["windows"] == 0


@pytest.mark.skipif(not stdref.DB.exists(), reason="needs the local standards (download_corpus.sh + ingest)")
def test_committed_artifacts_resolve_and_no_residual():
    ref = stdref.default_reference()
    man = json.loads((ROOT / "corpus" / "manifest.json").read_text(encoding="utf-8"))["docs"]
    assert all(ref.sha[m["doc_id"]] == m["text_sha256"] for m in man)
    rows = stdref.load_jsonl(ROOT / "reports" / "questions_block2.jsonl")
    assert all("⟦std:" not in json.dumps(r, ensure_ascii=False) for r in rows)
    files = subprocess.check_output(["git", "ls-files", "reports"], cwd=ROOT).decode().split()
    for f in files:
        t = stdref.blob_text((ROOT / f).read_bytes())
        if t is not None:
            assert ref.scan_text(t)["windows"] == 0, f
