import pytest

from tushen_kb.eval.judges import Judge, agreement, Judgment
from tushen_kb.eval.metrics import cohen_kappa, mcnemar_exact, mrr_at_k, rate, recall_at_k, bootstrap_diff_ci
from tushen_kb.eval.questions import build_u, closed_book_leak, dedup
from tushen_kb.llm import ChatModel, ConfigError, ModelSpec, load_models, validate
from tushen_kb.server import make_server


def spec(role, prov, fam, env="NOPE_TEST_KEY_ENV"):
    return ModelSpec(role, prov, fam, "https://example.invalid/v1", "m", env)


def test_metrics_with_denominators():
    ranks = [1, 3, None, 7]
    assert recall_at_k(ranks, 5)["str"] == "2/4 = 0.500"
    assert abs(mrr_at_k(ranks, 10)["value"] - (1 + 1 / 3 + 1 / 7) / 4) < 1e-9
    assert rate(0, 0)["str"].endswith("待测")


def test_mcnemar_and_bootstrap():
    a = [True] * 10 + [False] * 10
    b = [True] * 10 + [True] * 10
    m = mcnemar_exact(a, b)
    assert m["b01"] == 10 and m["b10"] == 0 and m["p_value"] < 0.01
    assert mcnemar_exact(a, a)["p_value"] == 1.0
    ci = bootstrap_diff_ci([float(x) for x in a], [float(x) for x in b])["ci"]
    assert ci[0] > 0


def test_kappa():
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert abs(cohen_kappa([True, True, False, False], [True, False, True, False])) < 1e-9


def test_repo_config_valid_and_deepseek_not_judge():
    gen, judges = load_models()
    assert gen.family == "deepseek"
    assert all(j.family != gen.family for j in judges if j.configured)


def test_judge_same_family_as_generator_rejected():
    with pytest.raises(ConfigError):
        validate(spec("generator", "deepseek", "deepseek"), [spec("judge1", "deepseek", "deepseek")])


def test_two_judges_same_family_rejected():
    with pytest.raises(ConfigError):
        validate(spec("generator", "deepseek", "deepseek"),
                 [spec("judge1", "xiaomi-mimo", "xiaomi-mimo"), spec("judge2", "mimo-alt", "xiaomi-mimo")])


def test_missing_key_is_skipped_never_fabricated(monkeypatch):
    monkeypatch.delenv("NOPE_TEST_KEY_ENV", raising=False)
    r = ChatModel(spec("generator", "deepseek", "deepseek")).complete([{"role": "user", "content": "hi"}])
    assert r["status"].startswith("skipped") and r["text"] is None
    j = Judge(spec("judge1", "xiaomi-mimo", "xiaomi-mimo")).faithfulness("q1", "证据", "回答")
    assert j.status.startswith("skipped") and j.verdict is None and j.judge_type == "model"
    assert j.judge_model == "m"


def test_agreement_needs_two_judges():
    j1 = [Judgment("a", "judge1", "m1", "v", "f1", "model", "faithfulness", "-", "ok", True)]
    assert agreement(j1, [])["agree"] == "待测"
    j2 = [Judgment("a", "judge2", "m2", "v", "f2", "model", "faithfulness", "-", "ok", False)]
    ag = agreement(j1, j2)
    assert ag["n_both_ok"] == 1 and ag["agree"] == 0 and ag["disagreements"][0]["item_id"] == "a"
    j1.append(Judgment("b", "judge1", "m1", "v", "f1", "model", "faithfulness", "-", "ok", True))
    j2.append(Judgment("b", "judge2", "m2", "v", "f2", "model", "faithfulness", "-", "error", None))
    ag = agreement(j1, j2)
    assert ag["n_total"] == 2 and ag["failed_items"][0]["item_id"] == "b"  # failure kept, not dropped
    # coverage 1/2 < 0.8: agreement and kappa are withheld, only coverage is reported
    assert ag["coverage_both_ok"] == "1/2" and str(ag["agree"]).startswith("待测") and str(ag["kappa"]).startswith("待测")


def test_closed_book_leak_program_check():
    gold = "消防电梯底坑应设置排水井，排水井有效容量不应小于2m³。"
    assert closed_book_leak("我记得是：消防电梯底坑应设置排水井，排水井有效", gold)
    assert not closed_book_leak("应设置排水设施。", gold)


def test_unanswerable_have_zero_occurrences(store):
    us = build_u(store, per_doc=5)
    text = store.doc_text("TEST1").replace("\n", "")
    assert us and all(q["clause_no"] not in text for q in us)


def test_dedup():
    qs = [{"question": "A 第1条？", "doc_id": "d", "acceptable": ["x"], "clause_no": "1"},
          {"question": "A 第1条？", "doc_id": "d", "acceptable": ["y"], "clause_no": "2"},
          {"question": "B 第1条？", "doc_id": "d", "acceptable": ["x"], "clause_no": "1"},
          {"question": "A 第2条？", "doc_id": "d", "acceptable": ["z"], "clause_no": "2"}]
    kept, removed = dedup(qs)
    assert removed == 2 and len(kept) == 2


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.2", "localhost", "::"])
def test_server_refuses_non_loopback(tmp_path, host):
    with pytest.raises(ValueError):
        make_server(str(tmp_path / "x.sqlite"), host=host, port=0)


def test_server_roundtrip(store, tmp_path):
    import json, threading, urllib.request
    srv = make_server(str(store.path) if hasattr(store, "path") else str(tmp_path / "t.sqlite"), port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        req = urllib.request.Request(base + "/search", data=json.dumps({"q": "排水井", "mode": "keyword"}).encode(),
                                     headers={"Content-Type": "application/json"})
        body = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert body["hits"]
        req = urllib.request.Request(base + "/search", data=json.dumps({"q": "x", "mode": "vector"}).encode())
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 501
    finally:
        srv.shutdown()


def test_repo_judge2_is_third_family():
    gen, judges = load_models()
    fams = [f for j in judges if j.configured for f in j.families()]
    assert "deepseek" not in fams and len(fams) == len(set(fams))
    assert judges[1].model == "gemini-3.8-flash"


def test_fallback_family_equal_to_generator_rejected():
    j = spec("judge2", "openrouter", "google")
    j.fallback_models = [{"model": "deepseek/x", "family": "deepseek"}]
    with pytest.raises(ConfigError):
        validate(spec("generator", "deepseek", "deepseek"), [j])


class _Fake:
    """Local fake OpenAI-compatible endpoint: primary model 429s N times / always."""
    def __init__(self, fail_primary):
        import http.server, json, threading
        calls = self.calls = []
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                calls.append(body["model"])
                if body["model"] == "primary" and (fail_primary == "always" or calls.count("primary") <= fail_primary):
                    self.send_response(429); self.end_headers(); self.wfile.write(b"{}"); return
                out = json.dumps({"model": body["model"], "choices": [{"message": {"content": "是"}}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
                self.wfile.write(out)
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


def _judge_spec(url, monkeypatch, fallback=True):
    monkeypatch.setenv("FAKE_JUDGE_KEY", "dummy")
    s = ModelSpec("judge2", "openrouter", "google", url, "primary", "FAKE_JUDGE_KEY",
                  max_retries=2, backoff_base_s=0.01)
    if fallback:
        s.fallback_models = [{"model": "backup", "family": "nvidia"}]
    return s


def test_retry_then_success_no_fallback(monkeypatch):
    f = _Fake(fail_primary=2)
    r = ChatModel(_judge_spec(f.url, monkeypatch)).complete([{"role": "user", "content": "x"}])
    f.srv.shutdown()
    assert r["status"] == "ok" and r["model_name"] == "primary" and not r["fallback_used"] and len(r["attempts"]) == 3


def test_fallback_only_after_primary_fails_and_named(monkeypatch):
    f = _Fake(fail_primary="always")
    j = Judge(_judge_spec(f.url, monkeypatch)).faithfulness("i", "证据", "回答")
    f.srv.shutdown()
    assert j.status == "ok" and j.judge_model == "backup" and j.judge_family == "nvidia" and j.fallback_used


def test_ultimate_failure_recorded(monkeypatch):
    f = _Fake(fail_primary="always")
    j = Judge(_judge_spec(f.url, monkeypatch, fallback=False)).faithfulness("i", "证据", "回答")
    f.srv.shutdown()
    assert j.status == "error" and j.verdict is None and "429" in j.error


def test_verdict_parsing():
    from tushen_kb.eval.judges import _parse_binary, _parse_choice
    assert _parse_binary("是") is True and _parse_binary("否。") is False
    assert _parse_binary("The user wants... let me check.\n\n否") is False
    assert _parse_binary("The user wants me to act as a strict fact-checker. I need to determine") is None
    assert _parse_binary("我认为可以") is None
    assert _parse_choice("A") == "A" and _parse_choice("reasoning...\n答案：B") == "B"
    assert _parse_choice("Analysis of options") is None


def test_backup_judge_forbidden_when_generator_is_openai():
    j2 = spec("judge2", "uuapi", "google")
    j2.backup_models = [{"model": "gpt-x", "family": "openai"}]
    with pytest.raises(ConfigError):
        validate(spec("generator", "openai", "openai"), [spec("judge1", "mimo", "xiaomi-mimo"), j2])
    validate(spec("generator", "deepseek", "deepseek"), [spec("judge1", "mimo", "xiaomi-mimo"), j2])


def test_config_judge2_is_uuapi_gemini():
    _, js = load_models()
    j2 = [j for j in js if j.role == "judge2"][0]
    assert j2.model == "gemini-3.8-flash" and j2.family == "google" and not j2.fallback_models
    assert j2.timeout_s >= 180 and j2.api_key_env == "NEW_API_KEY_2"
