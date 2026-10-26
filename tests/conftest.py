import pytest

from tushen_kb.chunker import chunk_document
from tushen_kb.normalize import normalize
from tushen_kb.pdfparse import Line, assemble
from tushen_kb.store import Store


def L(text, page=1, bold=0.0, kind="body", title=None, ti=None):
    return Line(text=normalize(text), page_no=page, kind=kind, bold_ratio=bold, size=10.5, table_title=title, table_index=ti)


def synth_lines():
    """Synthetic standard: 公告 with clause list, 目次, body, inline 条文说明, table, end marker."""
    return [
        L("住房和城乡建设部关于发布国家标准的公告"),
        L("现批准《测试通用规范》为国家标准，其中第3.1.1、3.2.2条为强制性条文，必须严格执行。"),
        L("目次", bold=1.0),
        L("1 总则…………1"),
        L("1 总则", page=2, bold=1.0),
        L("1.0.1 为规范测试工作，制定本规范。", page=2),
        L("1.0.2 本规范适用于新建建筑的测试。", page=2),
        L("3 基本规定", page=2, bold=1.0),
        L("3.1 一般规定", page=2, bold=1.0),
        L("3.1.1 防火分区的建筑面积应按其外墙内表面所围合的面积计算。", page=3, bold=1.0),
        L("【条文说明】3.1.1 本条规定了防火分区面积的计算方法。", page=3),
        L("3.1.2 消防电梯底坑应设置排水井，排水井有效容量不应小于2m³。", page=3),
        L("表3.1.2 排水参数", page=3),
        L("项目 | 数值", page=3, kind="table", title="表3.1.2 排水参数", ti=0),
        L("排水量 | 10L/s", page=3, kind="table", title="表3.1.2 排水参数", ti=0),
        L("3.2 其他", page=4, bold=1.0),
        L("3.2.1 疏散楼梯间不应设置卷帘。", page=4),
        L("本规范用词说明", page=4, bold=1.0),
        L("1 表示很严格，非这样做不可的用词。", page=4),
    ]


@pytest.fixture()
def synth():
    lines = synth_lines()
    doc_text, spans = assemble(lines)
    chunks = chunk_document("TEST1", lines, doc_text)
    return lines, doc_text, spans, chunks


@pytest.fixture()
def store(tmp_path, synth):
    _, doc_text, spans, chunks = synth
    s = Store(tmp_path / "t.sqlite")
    s.add_doc({"doc_id": "TEST1", "std_code": "GB 99999-2099", "title": "测试通用规范", "sha256": "x",
               "n_pages": 4}, doc_text, spans, chunks, {})
    yield s
    s.close()
