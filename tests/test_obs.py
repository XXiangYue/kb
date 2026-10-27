"""Tracing module (tushen_kb.obs) + trace-based failure attribution (agent_eval.attribute)."""
import json
from datetime import datetime, timezone

import pytest

from tushen_kb import obs

pytestmark = pytest.mark.skipif(not obs.HAVE_OTEL, reason="opentelemetry-sdk not installed")


@pytest.fixture()
def traced(tmp_path):
    p = tmp_path / "t.jsonl"
    obs.setup(p, keep_in_memory=True)
    yield p
    obs.shutdown()


def test_jsonl_schema_parent_child(traced):
    with obs.span("root", kind="SERVER", **{"gen_ai.operation.name": "invoke_agent"}) as r:
        tid = obs.current_trace_id(r)
        with obs.span("child", **{"gen_ai.tool.name": "retrieve", "kb.ids": ["a", "b"], "skip": None}) as c:
            obs.set_attrs(c, n=3)
    obs.shutdown()
    rows = obs.load_spans(traced)
    assert [s["name"] for s in rows] == ["child", "root"]  # children finish first
    child, root = rows
    for k in ("trace_id", "span_id", "parent_span_id", "start_time_unix_nano", "end_time_unix_nano",
              "duration_ms", "status", "attributes", "events", "resource", "kind"):
        assert k in child
    assert child["trace_id"] == root["trace_id"] == tid and len(tid) == 32
    assert child["parent_span_id"] == root["span_id"] and root["parent_span_id"] is None
    assert root["kind"] == "SERVER" and child["kind"] == "INTERNAL"
    assert child["attributes"] == {"gen_ai.tool.name": "retrieve", "kb.ids": ["a", "b"], "n": 3}
    assert root["resource"]["service.name"] == "tushen-kb"
    tree = obs.trace_tree(rows, tid)
    assert tree["name"] == "root" and tree["children"][0]["name"] == "child"
    json.dumps(rows)  # serialisable


def test_exception_and_set_error_mark_status(traced):
    with pytest.raises(ValueError):
        with obs.span("boom"):
            raise ValueError("x")
    with obs.span("soft") as s:
        obs.set_error(s, "HTTP429", "rate limited")
    obs.shutdown()
    boom, soft = obs.load_spans(traced)
    assert boom["status"]["code"] == "ERROR" and boom["attributes"]["error.type"] == "ValueError"
    assert any(e["name"] == "exception" for e in boom["events"])
    assert soft["status"] == {"code": "ERROR", "description": "rate limited"}
    assert soft["attributes"]["error.type"] == "HTTP429"


def test_memory_exporter_pop(traced):
    with obs.span("a") as s:
        tid = obs.current_trace_id(s)
    got = obs.finished_spans(tid)
    assert [x["name"] for x in got] == ["a"]
    assert obs.finished_spans(tid) == []


def test_noop_when_disabled(tmp_path):
    obs.shutdown()
    assert not obs.enabled()
    with obs.span("x", a=1) as s:
        assert s is obs.NOOP
        obs.set_attrs(s, b=2)
        obs.set_error(s, "E")
        assert obs.current_trace_id(s) is None


def test_cost_and_peak():
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "prompt_cache_hit_tokens": 400}
    sat = datetime(2026, 10, 24, 8, tzinfo=timezone.utc)  # Saturday -> off-peak
    mon_peak = datetime(2026, 10, 26, 8, tzinfo=timezone.utc)
    mon_off = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
    base = (400 * 0.003 + 600 * 0.15 + 100 * 0.60) / 1e6
    assert obs.cost_usd("deepseek", "deepseek-flash", usage, sat) == pytest.approx(base)
    assert obs.cost_usd("deepseek", "deepseek-flash", usage, mon_off) == pytest.approx(base)
    assert obs.cost_usd("deepseek", "deepseek-flash", usage, mon_peak) == pytest.approx(2 * base)
    assert obs.cost_usd("nobody", "unknown", usage) is None
    assert obs.cost_usd("deepseek", "deepseek-flash", None) is None
    assert obs.cache_hit_tokens({"prompt_tokens_details": {"cached_tokens": 7}}) == 7


def test_percentile_nearest_rank():
    xs = list(range(1, 101))
    assert obs.percentile(xs, 50) == 50 and obs.percentile(xs, 95) == 95 and obs.percentile(xs, 100) == 100
    assert obs.percentile([3.0], 95) == 3.0 and obs.percentile([], 50) is None


# ------------------------------------------------------------------ attribution from synthetic traces
from tushen_kb.eval.agent_eval import attribute  # noqa: E402

_n = [0]


def _s(name, parent=None, status="UNSET", **attrs):
    _n[0] += 1
    return {"trace_id": "t", "span_id": f"s{_n[0]}", "parent_span_id": parent, "name": name,
            "start_time_unix_nano": _n[0], "end_time_unix_nano": _n[0] + 1, "duration_ms": 1.0,
            "status": {"code": status, "description": None}, "attributes": attrs, "events": []}


def _trace(hits, evidence, refused=False, step=None, mode="llm_answer", rerank_in=None, gen_status="UNSET",
           cites=(), llm_status="ok"):
    root = _s("kb.ask", **{"kb.refused": refused, "kb.refusal_step": step, "kb.mode": mode,
                           "kb.citation_chunk_ids": list(cites)})
    sp = [root, _s("kb.existence_check", root["span_id"]), _s("kb.retrieve", root["span_id"], **{"kb.hit_chunk_ids": hits})]
    if rerank_in is not None:
        sp.append(_s("kb.search.rerank", sp[-1]["span_id"], **{"kb.input_chunk_ids": rerank_in}))
    sp.append(_s("kb.evidence_gate", root["span_id"], **{"kb.evidence_chunk_ids": evidence}))
    sp.append(_s("kb.rerank_gate", root["span_id"], **{"kb.s1": 0.5}))
    if step not in ("evidence_gate", "rerank_gate"):
        sp.append(_s("kb.cite", root["span_id"], **{"kb.n_verified": 1}))
        g = _s("kb.generate", root["span_id"], status=gen_status, **{"kb.llm_status": llm_status})
        sp += [g, _s("chat deepseek-flash", g["span_id"], **{"gen_ai.usage.input_tokens": 10})]
    return sp


G = {"g"}


def test_attr_success_and_hallucination():
    sp = _trace(["g", "x"], ["g"], cites=["g"])
    assert attribute(sp, G, True, {"correct": True, "support": True})["success"]
    r = attribute(sp, G, True, {"correct": False, "support": False})
    assert (r["category"], r["failed_step"]) == ("生成幻觉", "kb.generate")
    assert attribute(sp, G, True, {"correct": None})["category"] == "其他"


def test_attr_retrieval_and_ranking():
    r = attribute(_trace(["x"], ["x"], refused=True, step="rerank_gate"), G, True, {})
    assert (r["category"], r["failed_step"]) == ("检索未召回", "kb.retrieve")
    r = attribute(_trace(["a", "b", "c", "g"], ["a", "b", "c"], refused=True, step="rerank_gate",
                         rerank_in=["g", "a", "b", "c"]), G, True, {})
    assert (r["category"], r["failed_step"]) == ("召回但排序靠后未进上下文", "kb.search.rerank")


def test_attr_refusals_and_errors():
    r = attribute(_trace(["g"], ["g"], refused=True, step="rerank_gate"), G, True, {})
    assert (r["category"], r["failed_step"]) == ("拒答误判", "kb.rerank_gate")
    assert attribute(_trace(["x"], ["x"], refused=True, step="rerank_gate"), G, False, {})["success"]
    r = attribute(_trace(["x"], ["x"]), G, False, {})
    assert (r["category"], r["failed_step"]) == ("该拒未拒", "kb.rerank_gate")
    r = attribute(_trace(["g"], ["g"], mode="evidence_only", gen_status="ERROR", llm_status="error"), G, True, {})
    assert (r["category"], r["failed_step"]) == ("工具或步骤错误", "kb.generate")


def test_ask_emits_step_spans(traced, store):
    from tushen_kb.ask import ask
    r = ask(store, "消防电梯底坑排水井有效容量")
    tid = r["trace"]["otel"]["trace_id"]
    obs.shutdown()
    rows = [s for s in obs.load_spans(traced) if s["trace_id"] == tid]
    names = {s["name"] for s in rows}
    assert {"kb.ask", "kb.existence_check", "kb.retrieve", "kb.evidence_gate"} <= names
    tree = obs.trace_tree(rows, tid)
    assert tree["name"] == "kb.ask" and tree["attributes"]["gen_ai.operation.name"] == "invoke_agent"
    steps = [s for s in rows if s["name"] in ("kb.retrieve", "kb.evidence_gate")]
    assert all(s["attributes"]["gen_ai.operation.name"] == "execute_tool" and "kb.step.outcome" in s["attributes"]
               for s in steps)
    assert any(s["attributes"].get("kb.hit_chunk_ids") for s in rows if s["name"] == "kb.retrieve")


def test_ask_untraced_has_no_otel_ids(store):
    from tushen_kb.ask import ask
    obs.shutdown()
    r = ask(store, "消防电梯底坑排水井有效容量")
    assert not (r["trace"].get("otel") or {}).get("trace_id")


def test_judge_ledger_splits_batches_on_returned_model_change():
    from tushen_kb.eval.agent_eval import judge_ledger

    def pair(i, ret, status="ok", desc=None):
        js = {"name": "eval.judge.correctness", "trace_id": f"t{i}", "span_id": f"j{i}", "parent_span_id": f"r{i}",
              "start_time_unix_nano": int((1_800_000_000 + i) * 1e9), "status": {"code": "UNSET"},
              "attributes": {"eval.judge.role": "judge1", "eval.qid": f"q{i}", "eval.judge.model": "m",
                             "eval.judge.status": status, "eval.judge.verdict": True if status == "ok" else None}}
        ch = {"name": "chat m", "trace_id": f"t{i}", "span_id": f"c{i}", "parent_span_id": f"j{i}",
              "start_time_unix_nano": js["start_time_unix_nano"], "status": {"code": "ERROR" if desc else "UNSET",
                                                                             "description": desc},
              "attributes": {"gen_ai.response.model": ret, "tushen.attempts": 1}}
        return [js, ch]

    spans = pair(1, "m-v1") + pair(2, "m-v1") + pair(3, None, "error", "HTTP 429: busy") + pair(4, "m-v2")
    rows, vb = judge_ledger(spans)
    assert [r["batch"] for r in rows] == ["judge1#1", "judge1#1", "judge1#1", "judge1#2"]
    assert vb["version_changed_roles"] == ["judge1"]
    assert [b["n_calls"] for b in vb["batches"]] == [2, 1] and vb["batches"][0]["n_failed"] == 1
    assert rows[2]["failure_reason"] == "HTTP 429: busy" and rows[0]["called_at_utc"].endswith("Z")
    rows, vb = judge_ledger(pair(1, "m-v1") + pair(2, "m-v1"))
    assert vb["version_changed_roles"] == [] and len(vb["batches"]) == 1
