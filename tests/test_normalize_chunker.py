from tushen_kb.chunker import chunk_document
from tushen_kb.normalize import normalize, squash
from tushen_kb.pdfparse import _accept_table

from conftest import L, synth_lines
from tushen_kb.pdfparse import assemble


def test_normalize_keeps_cjk_punct_and_nfkc_ascii():
    assert normalize("ＡＢ１２３　测试，（一）") == "AB123 测试，（一）"


def test_squash_index_map():
    s, m = squash("a b\nc")
    assert s == "abc" and m == [0, 2, 4]


def test_offsets_invariant(synth):
    _, doc_text, _, chunks = synth
    for c in chunks:
        assert doc_text[c.start:c.end] == c.text


def test_announcement_clause_list_not_chunked(synth):
    chunks = synth[3]
    assert all("现批准" not in c.text and "目次" not in c.text for c in chunks)
    assert chunks[0].clause_no == "1.0.1"


def test_kinds_and_mandatory(synth):
    by = {c.chunk_id: c for c in synth[3]}
    assert by["TEST1:clause:3.1.1:0"].is_mandatory  # bold clause body
    assert not by["TEST1:clause:3.1.2:0"].is_mandatory
    com = by["TEST1:commentary:3.1.1:0"]
    assert com.ref_clause == "3.1.1" and not com.is_mandatory
    tab = by["TEST1:table:3.1.2:0"]
    assert tab.table_title.startswith("表3.1.2") and "10L/s" in tab.text
    assert "表3.1.2 排水参数" not in by["TEST1:clause:3.1.2:0"].text  # title line not glued into clause


def test_end_marker_stops_content(synth):
    assert all("用词说明" not in c.text and "很严格" not in c.text for c in synth[3])


def test_doc_level_mandatory():
    lines = synth_lines()
    t, _ = assemble(lines)
    cs = chunk_document("T", lines, t, all_mandatory=True, mandatory_source="doc:statement")
    assert all(c.is_mandatory for c in cs if c.kind == "clause")
    assert not any(c.is_mandatory for c in cs if c.kind == "commentary")


def test_page_numbers(synth):
    by = {c.chunk_id: c for c in synth[3]}
    assert by["TEST1:clause:1.0.1:0"].page_start == 2 and by["TEST1:clause:3.2.1:0"].page_start == 4


def test_non_monotonic_numbered_list_is_not_clause():
    lines = [L("1 总则", bold=1), L("1.0.1 第一条内容很长很长。"), L("1.0.2 第二条：", ),
             L("1.0.1 被错误重复的编号不应开启新条文。")]
    t, _ = assemble(lines)
    cs = chunk_document("T", lines, t)
    assert [c.clause_no for c in cs] == ["1.0.1", "1.0.2"]


def test_table_acceptance():
    assert not _accept_table([["整页框"], ["文字"]])  # 1-column page frame
    assert _accept_table([["项目", "数值"], ["a", "1"]])
