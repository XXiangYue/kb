"""Step 4: calibrate AskConfig.min_coverage on the DEV split only (hybrid_rerank, evidence-only /ask).

Rule fixed before running (written here, committed with the result):
  grid       0.00, 0.05, ..., 0.80
  objective  minimise errors = false refusals (answerable dev) + false answers (U2 dev, not refused)
  ties       higher citation precision (口径 B), then the HIGHER threshold (prefer refusing)
Dev = block-2 dev split + low-overlap dev split.  Before calibrating, dev/test overlap is checked
(qid, whitespace-normalised question text, and source chunk) and written to the report.
Test is not read here except for the overlap check.
"""
from __future__ import annotations

import json
import re
import time

from ..ask import AskConfig, ask
from ..ingest import ROOT
from ..search import SearchConfig
from ..store import Store
from .metrics import rate
from .run_block2 import _memo

REPORTS = ROOT / "reports"
QFILES = [REPORTS / "questions_block2.jsonl", REPORTS / "questions_lowoverlap.jsonl"]
GRID = [round(0.05 * i, 2) for i in range(17)]
MODE = "hybrid_rerank"


def load_all():
    qs = []
    for f in QFILES:
        qs += [json.loads(l) for l in open(f, encoding="utf-8")][1:]
    return qs


def overlap_check(qs):
    norm = lambda s: re.sub(r"\s", "", s)
    dev = [q for q in qs if q["split"] == "dev"]
    test = [q for q in qs if q["split"] == "test"]
    src = lambda L: {q["src_chunk"] for q in L if q.get("src_chunk")}
    return {"n_dev": len(dev), "n_test": len(test),
            "qid_overlap": len({q["qid"] for q in dev} & {q["qid"] for q in test}),
            "question_text_overlap": len({norm(q["question"]) for q in dev} & {norm(q["question"]) for q in test}),
            "src_chunk_overlap(informational)": len(src(dev) & src(test))}


def main():
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    from .. import dense
    _memo(dense)
    qs = load_all()
    ov = overlap_check(qs)
    print(json.dumps(ov))
    assert ov["qid_overlap"] == 0 and ov["question_text_overlap"] == 0, ov
    dev = [q for q in qs if q["split"] == "dev"]
    ans = [q for q in dev if q["answerable"]]
    u2 = [q for q in dev if q["type"] == "U2"]
    grid = []
    for th in GRID:
        cfg = AskConfig(search=SearchConfig(mode=MODE), min_coverage=th)
        fr, ok, nc = 0, 0, 0
        for q in ans:
            r = ask(store, q["question"], cfg)
            fr += r["refused"]
            if not r["refused"]:
                nc += len(r["citations"])
                ok += sum(c["chunk_id"] in set(q["acceptable"]) for c in r["citations"])
        fa = sum(not ask(store, q["question"], cfg)["refused"] for q in u2)
        grid.append({"min_coverage": th, "false_refusal": rate(fr, len(ans))["str"],
                     "false_answer_U2": rate(fa, len(u2))["str"], "errors": fr + fa,
                     "citation_precision": rate(ok, nc)["str"], "_p": ok / nc if nc else 0.0})
        print(grid[-1])
    best = sorted(grid, key=lambda g: (g["errors"], -g["_p"], -g["min_coverage"]))[0]
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"), "mode": MODE, "rule": __doc__,
           "dev_test_overlap": ov, "n_dev_answerable": len(ans), "n_dev_U2": len(u2),
           "n_dev_by_file": {f.name: sum(1 for q in [json.loads(l) for l in open(f, encoding='utf-8')][1:]
                                         if q["split"] == "dev") for f in QFILES},
           "grid": [{k: v for k, v in g.items() if k != "_p"} for g in grid],
           "chosen_min_coverage": best["min_coverage"]}
    (REPORTS / "threshold_calibration_dev.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                                                           encoding="utf-8")
    print("chosen", best["min_coverage"])


if __name__ == "__main__":
    main()
