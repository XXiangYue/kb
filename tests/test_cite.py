from tushen_kb.cite import verify_citation, verify_quote
from tushen_kb.normalize import normalize

# stored text is always normalized (NFKC except CJK punctuation), as in the pipeline
SRC = normalize("3.1.2 消防电梯底坑应设置排水井，排水井有效容量不应小于2m³。")


def test_exact_quote_ok():
    ok, s, e = verify_quote("排水井有效容量不应小于2m³", SRC)
    assert ok and SRC[s:e] == normalize("排水井有效容量不应小于2m³")


def test_whitespace_tolerant():
    assert verify_quote("排水井 有效容量\n不应小于", SRC)[0]


def test_changed_digit_rejected():
    assert not verify_quote("排水井有效容量不应小于3m³", SRC)[0]


def test_absent_text_rejected():
    assert not verify_quote("排水井有效容量应大于5m³", SRC)[0]


def test_too_short_rejected():
    assert not verify_quote("排水", SRC)[0]


def test_paraphrase_rejected():
    assert not verify_quote("底坑须设排水井", SRC)[0]


def test_fullwidth_variant_normalized_same_way():
    # the quote goes through the same normalization as the stored text
    assert verify_quote("不应小于２m³", SRC)[0]
    assert not verify_quote("不应小于２m⁴", SRC)[0]


def _chunk():
    doc = "前文。\n" + SRC
    return {"text": SRC, "char_start": 4, "char_end": 4 + len(SRC)}, doc


def test_citation_offsets_checked():
    ch, doc = _chunk()
    ok_cit = {"quote": "消防电梯底坑应设置排水井", "char_start": 4 + 6, "char_end": 4 + 18}
    assert verify_citation(ok_cit, ch, doc)
    bad = dict(ok_cit, char_start=0, char_end=12)
    assert not verify_citation(bad, ch, doc)


def test_citation_wrong_quote():
    ch, doc = _chunk()
    assert not verify_citation({"quote": "消防电梯底坑可不设排水井", "char_start": 10, "char_end": 22}, ch, doc)
