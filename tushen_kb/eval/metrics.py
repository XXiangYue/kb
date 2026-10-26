"""Program-computed metrics and paired statistics.  Every rate is returned with
its numerator and denominator."""
from __future__ import annotations

import math
import random


def rate(num: int, den: int) -> dict:
    return {"value": (num / den) if den else None, "num": num, "den": den,
            "str": f"{num}/{den} = {num / den:.3f}" if den else f"{num}/0 = 待测"}


def first_hit_rank(ranked_ids: list, acceptable: set, k: int) -> int | None:
    for i, cid in enumerate(ranked_ids[:k]):
        if cid in acceptable:
            return i + 1
    return None


def recall_at_k(ranks: list[int | None], k: int) -> dict:
    return rate(sum(1 for r in ranks if r is not None and r <= k), len(ranks))


def mrr_at_k(ranks: list[int | None], k: int) -> dict:
    n = len(ranks)
    s = sum(1.0 / r for r in ranks if r is not None and r <= k)
    return {"value": s / n if n else None, "sum_rr": round(s, 4), "den": n,
            "str": f"{s:.2f}/{n} = {s / n:.3f}" if n else "待测"}


def mcnemar_exact(a: list[bool], b: list[bool]) -> dict:
    """Exact (binomial) McNemar test on paired binary outcomes."""
    assert len(a) == len(b)
    b01 = sum(1 for x, y in zip(a, b) if (not x) and y)
    b10 = sum(1 for x, y in zip(a, b) if x and (not y))
    n = b01 + b10
    if n == 0:
        return {"b01": 0, "b10": 0, "n_discordant": 0, "p_value": 1.0}
    k = min(b01, b10)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return {"b01": b01, "b10": b10, "n_discordant": n, "p_value": min(1.0, 2 * p)}


def bootstrap_diff_ci(a: list[float], b: list[float], iters: int = 2000, seed: int = 0, alpha: float = 0.05) -> dict:
    """Paired bootstrap CI for mean(b) - mean(a)."""
    assert len(a) == len(b)
    n = len(a)
    if n == 0:
        return {"diff": None, "ci": None}
    rng = random.Random(seed)
    diffs = []
    for _ in range(iters):
        idx = [rng.randrange(n) for _ in range(n)]
        diffs.append(sum(b[i] - a[i] for i in idx) / n)
    diffs.sort()
    lo = diffs[int(alpha / 2 * iters)]
    hi = diffs[int((1 - alpha / 2) * iters) - 1]
    return {"diff": sum(b) / n - sum(a) / n, "ci": [lo, hi], "iters": iters, "seed": seed}


def bootstrap_mean_ci(x: list[float], iters: int = 2000, seed: int = 0, alpha: float = 0.05) -> list | None:
    n = len(x)
    if n == 0:
        return None
    rng = random.Random(seed)
    ms = sorted(sum(x[rng.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return [ms[int(alpha / 2 * iters)], ms[int((1 - alpha / 2) * iters) - 1]]


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1:
        return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)


def significance_label(p: float, ci: list | None) -> str:
    if ci is not None and ci[0] <= 0 <= ci[1]:
        return "无显著差异"
    return "显著" if p < 0.05 else "无显著差异"


def bootstrap_ratio_diff_ci(num_a, den_a, num_b, den_b, iters: int = 2000, seed: int = 0, alpha: float = 0.05) -> dict:
    """Paired (cluster) bootstrap over items for sum(num_b)/sum(den_b) - sum(num_a)/sum(den_a).
    Used for citation precision, whose unit (citation) is clustered within questions."""
    n = len(num_a)
    assert n == len(den_a) == len(num_b) == len(den_b)
    if n == 0 or not sum(den_a) or not sum(den_b):
        return {"diff": None, "ci": None}
    rng = random.Random(seed)
    diffs = []
    for _ in range(iters):
        idx = [rng.randrange(n) for _ in range(n)]
        da, db = sum(den_a[i] for i in idx), sum(den_b[i] for i in idx)
        if da and db:
            diffs.append(sum(num_b[i] for i in idx) / db - sum(num_a[i] for i in idx) / da)
    diffs.sort()
    m = len(diffs)
    return {"diff": sum(num_b) / sum(den_b) - sum(num_a) / sum(den_a),
            "ci": [diffs[int(alpha / 2 * m)], diffs[int((1 - alpha / 2) * m) - 1]], "iters": iters, "seed": seed,
            "unit": "question (cluster bootstrap)"}
