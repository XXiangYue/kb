"""Optional structured tracing for /ask, retrieval steps and every model call.

* Backend: the OpenTelemetry SDK (``requirements-obs.txt``) with a local JSONL file exporter -
  one finished span per line, OTel field names (trace_id / span_id / parent_span_id, unix-nano
  start/end, kind, status, attributes, events, resource).  No collector or SaaS is needed; the
  file can be replayed into any OTLP backend.
* Attribute names follow the OTel GenAI semantic conventions where one exists
  (gen_ai.operation.name, gen_ai.provider.name, gen_ai.request.model, gen_ai.response.model,
  gen_ai.usage.input_tokens / output_tokens, gen_ai.request.max_tokens / temperature,
  gen_ai.tool.name, error.type); project-specific ones are prefixed ``tushen.`` / ``kb.``.
* Off by default.  Enabled by ``setup(path)`` or the env var ``TUSHEN_TRACE_FILE``.  When off,
  or when the SDK is not installed, ``span()`` yields a no-op object (one dict lookup + a
  context manager per step).  A private TracerProvider is used; the global OTel provider is never
  touched, so embedding apps keep their own setup.
* Prompt / completion text is NOT recorded (only sizes); API keys never reach this module.
* Cost: ``cost_usd`` uses the documented list-price table ``config/prices.json``; a model not in
  the table gets ``None`` (never guessed).
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:  # optional dependency
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
    from opentelemetry.trace import SpanKind, Status, StatusCode
    HAVE_OTEL = True
except ImportError:  # pragma: no cover - exercised only without the SDK
    HAVE_OTEL = False

PRICES = Path(__file__).resolve().parents[1] / "config" / "prices.json"
_state = {"provider": None, "tracer": None, "env_checked": False, "memory": None}
_lock = threading.Lock()


class _NoopSpan:
    def set_attribute(self, *a, **k):
        pass

    def set_attributes(self, *a, **k):
        pass

    def add_event(self, *a, **k):
        pass

    def set_status(self, *a, **k):
        pass

    def record_exception(self, *a, **k):
        pass

    def is_recording(self):
        return False


NOOP = _NoopSpan()


def span_to_dict(s) -> dict:
    ctx, parent = s.get_span_context(), s.parent
    return {
        "trace_id": format(ctx.trace_id, "032x"),
        "span_id": format(ctx.span_id, "016x"),
        "parent_span_id": format(parent.span_id, "016x") if parent else None,
        "name": s.name,
        "kind": s.kind.name,
        "start_time_unix_nano": s.start_time,
        "end_time_unix_nano": s.end_time,
        "duration_ms": round((s.end_time - s.start_time) / 1e6, 3),
        "status": {"code": s.status.status_code.name, "description": s.status.description},
        "attributes": dict(s.attributes or {}),
        "events": [{"name": e.name, "time_unix_nano": e.timestamp, "attributes": dict(e.attributes or {})}
                   for e in s.events],
        "resource": dict(s.resource.attributes),
        "scope": s.instrumentation_scope.name if s.instrumentation_scope else None,
    }


if HAVE_OTEL:
    class JsonlFileExporter(SpanExporter):
        """Appends one JSON object per finished span (thread-safe)."""

        def __init__(self, path):
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._lock = threading.Lock()

        def export(self, spans):
            lines = "".join(json.dumps(span_to_dict(s), ensure_ascii=False) + "\n" for s in spans)
            with self._lock, open(self.path, "a", encoding="utf-8") as f:
                f.write(lines)
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass


    class MemoryExporter(SpanExporter):
        """Keeps finished spans (as dicts) per trace_id so a caller can read a trace back
        before its root span has ended (used by the eval to attribute failures from the trace)."""

        def __init__(self):
            self._lock = threading.Lock()
            self.by_trace: dict[str, list[dict]] = {}

        def export(self, spans):
            with self._lock:
                for s in spans:
                    d = span_to_dict(s)
                    self.by_trace.setdefault(d["trace_id"], []).append(d)
            return SpanExportResult.SUCCESS

        def pop(self, trace_id: str) -> list[dict]:
            with self._lock:
                return self.by_trace.pop(trace_id, [])

        def shutdown(self):
            pass


def setup(path=None, service_name: str = "tushen-kb", keep_in_memory: bool = False):
    """Start writing spans to ``path`` (JSONL) and/or keep them in memory (``finished_spans``).
    Returns the provider, or None without the SDK."""
    if not HAVE_OTEL:
        return None
    shutdown()
    from . import __version__ as ver
    prov = TracerProvider(resource=Resource.create({"service.name": service_name, "service.version": ver}))
    if path:
        prov.add_span_processor(SimpleSpanProcessor(JsonlFileExporter(path)))
    _state["memory"] = MemoryExporter() if keep_in_memory else None
    if _state["memory"] is not None:
        prov.add_span_processor(SimpleSpanProcessor(_state["memory"]))
    with _lock:
        _state["provider"], _state["tracer"] = prov, prov.get_tracer("tushen_kb", ver)
    return prov


def finished_spans(trace_id: str) -> list[dict]:
    """Pop the finished spans of one trace from the in-memory exporter (setup(keep_in_memory=True))."""
    mem = _state.get("memory")
    return mem.pop(trace_id) if mem is not None else []


def current_trace_id(s) -> str | None:
    if s is NOOP or not s.is_recording():
        return None
    return format(s.get_span_context().trace_id, "032x")


def shutdown():
    with _lock:
        prov, _state["provider"], _state["tracer"] = _state["provider"], None, None
    if prov is not None:
        prov.shutdown()


def _tracer():
    if not _state["env_checked"]:
        _state["env_checked"] = True
        if os.environ.get("TUSHEN_TRACE_FILE") and _state["tracer"] is None:
            setup(os.environ["TUSHEN_TRACE_FILE"])
    return _state["tracer"]


def enabled() -> bool:
    return _tracer() is not None


def _clean(attrs: dict) -> dict:
    out = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = [x if isinstance(x, (str, bool, int, float)) else str(x) for x in v if x is not None]
        elif not isinstance(v, (str, bool, int, float)):
            v = str(v)
        out[k] = v
    return out


@contextmanager
def span(name: str, kind: str = "INTERNAL", **attrs):
    """``with span("kb.retrieve", **{"gen_ai.tool.name": "retrieve"}) as s: ...``
    Exceptions are recorded on the span (status ERROR, error.type) and re-raised."""
    tr = _tracer()
    if tr is None:
        yield NOOP
        return
    with tr.start_as_current_span(name, kind=getattr(SpanKind, kind), attributes=_clean(attrs),
                                  record_exception=True, set_status_on_exception=True) as s:
        try:
            yield s
        except BaseException as e:
            s.set_attribute("error.type", type(e).__name__)
            raise


def set_attrs(s, **attrs):
    s.set_attributes(_clean(attrs))


def set_error(s, error_type: str, description: str = ""):
    if s is NOOP or not HAVE_OTEL:
        return
    s.set_attribute("error.type", error_type)
    s.set_status(Status(StatusCode.ERROR, description[:200]))


# ---------------------------------------------------------------- cost
_prices_cache: dict = {}


def prices() -> dict:
    if not _prices_cache:
        _prices_cache.update(json.loads(PRICES.read_text(encoding="utf-8"))["models"])
    return _prices_cache


def is_peak(spec: dict, ts_utc: datetime) -> bool:
    if "peak_utc_hours" not in spec:
        return False
    if spec.get("peak_weekdays_only") and ts_utc.weekday() >= 5:
        return False
    return any(a <= ts_utc.hour < b for a, b in spec["peak_utc_hours"])


def cache_hit_tokens(usage: dict) -> int:
    if not usage:
        return 0
    if usage.get("prompt_cache_hit_tokens") is not None:  # DeepSeek
        return int(usage["prompt_cache_hit_tokens"])
    det = usage.get("prompt_tokens_details") or {}
    return int(det.get("cached_tokens") or 0)


def cost_usd(provider: str, model: str, usage: dict | None, ts_utc: datetime | None = None) -> float | None:
    """List-price cost of one call from ``config/prices.json``; None if the model is not listed."""
    if not usage:
        return None
    spec = prices().get(f"{provider}/{model}")
    if spec is None:
        return None
    ts_utc = ts_utc or datetime.now(timezone.utc)
    mult = spec.get("peak_multiplier", 1.0) if is_peak(spec, ts_utc) else 1.0
    pin = int(usage.get("prompt_tokens") or 0)
    pout = int(usage.get("completion_tokens") or 0)
    hit = min(cache_hit_tokens(usage), pin)
    c = (hit * spec["input_cache_hit"] + (pin - hit) * spec["input_cache_miss"] + pout * spec["output"]) / 1e6
    return round(c * mult, 10)


# ---------------------------------------------------------------- reading traces back
def load_spans(path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def trace_tree(spans: list[dict], trace_id: str) -> dict:
    """Nest the spans of one trace (children sorted by start time)."""
    S = [dict(s, children=[]) for s in spans if s["trace_id"] == trace_id]
    by = {s["span_id"]: s for s in S}
    roots = []
    for s in sorted(S, key=lambda x: x["start_time_unix_nano"]):
        (by[s["parent_span_id"]]["children"] if s["parent_span_id"] in by else roots).append(s)
    assert len(roots) == 1, f"trace {trace_id}: {len(roots)} roots"
    return roots[0]


def percentile(xs: list[float], p: float) -> float | None:
    """Nearest-rank percentile (p in (0, 100])."""
    if not xs:
        return None
    s = sorted(xs)
    import math
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
