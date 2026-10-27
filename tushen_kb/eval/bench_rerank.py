"""Standalone rerank latency benchmark (CPU).

For each query (block-2 test answerable + low-overlap test, in file order):
  - the hybrid candidate list is built first (not timed),
  - then ONLY the cross-encoder call over the top-k candidates is timed (k = SearchConfig.rerank_n),
  - full search() wall time for hybrid vs hybrid_rerank is also timed, for context.
The first WARMUP queries are excluded.  No memoisation.  No other heavy job runs at the same time.
"""
from __future__ import annotations

from .. import stdref  # resolves standards-text references in committed artifacts

import json
import os
import platform
import resource
import time

from ..ingest import ROOT
from ..search import SearchConfig, search
from ..store import Store

REPORTS = ROOT / "reports"
WARMUP = 5


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, max(0, int(round(p / 100 * len(xs))) - 1))], 1)


def cpu_model():
    for l in open("/proc/cpuinfo"):
        if l.startswith("model name"):
            return l.split(":", 1)[1].strip()
    return platform.processor()


def main():
    from .. import dense
    store = Store(str(ROOT / "data" / "tushen.sqlite"))
    qs = []
    for f in ("questions_block2.jsonl", "questions_lowoverlap.jsonl"):
        qs += [q for q in [stdref.loads(l) for l in open(REPORTS / f, encoding="utf-8")][1:]
               if q["split"] == "test" and q["answerable"]]
    k = SearchConfig().rerank_n
    rr, s_h, s_hr = [], [], []
    for i, q in enumerate(qs):
        rows = search(store, q["question"], SearchConfig(mode="hybrid", k=k))[:k]
        ps = [dense.passage_text(r) for r in rows]
        t0 = time.perf_counter(); dense.rerank_scores(q["question"], ps); t1 = time.perf_counter()
        search(store, q["question"], SearchConfig(mode="hybrid")); t2 = time.perf_counter()
        search(store, q["question"], SearchConfig(mode="hybrid_rerank")); t3 = time.perf_counter()
        if i >= WARMUP:
            rr.append((t1 - t0) * 1000); s_h.append((t2 - t1) * 1000); s_hr.append((t3 - t2) * 1000)
    mem_kb = int([l for l in open("/proc/meminfo") if l.startswith("MemTotal")][0].split()[1])
    rep = {"time_local": time.strftime("%Y-%m-%d %H:%M %Z"),
           "hardware": {"cpu_model": cpu_model(), "logical_cpus": os.cpu_count(),
                        "torch_threads": int(os.environ.get("TUSHEN_THREADS", "4")),
                        "ram_total_GB": round(mem_kb / 1024 ** 2, 1), "gpu": "none (CPU only)"},
           "reranker": dense.RERANK_MODEL, "candidates_k": k, "batch": 8,
           "n_queries": len(rr), "warmup_excluded": WARMUP,
           "rerank_only_ms": {"median": pct(rr, 50), "p95": pct(rr, 95), "mean": round(sum(rr) / len(rr), 1)},
           "search_hybrid_ms": {"median": pct(s_h, 50), "p95": pct(s_h, 95)},
           "search_hybrid_rerank_ms": {"median": pct(s_hr, 50), "p95": pct(s_hr, 95)},
           "peak_rss_MB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024}
    (REPORTS / "rerank_latency.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
