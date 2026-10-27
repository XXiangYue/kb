"""Pluggable LLM providers (OpenAI-compatible chat completions via stdlib).

* Provider / model / env-var names come from config/models.json.
* API keys are read from the environment at call time and never logged,
  returned, or written anywhere.  Error messages are scrubbed of the key.
* A model whose key env var is unset is reported with status "skipped";
  callers must record "skipped", never a fabricated result.
* Config validation enforces: judge family != generator family, and the two
  judges are of different families (DeepSeek can never be a judge while it is
  the generator).
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import obs

ROOT = Path(__file__).resolve().parent.parent
MODELS_CONFIG = ROOT / "config" / "models.json"


class ConfigError(ValueError):
    pass


@dataclass
class ModelSpec:
    role: str
    provider: str | None
    family: str | None
    base_url: str | None
    model: str | None
    api_key_env: str | None
    auth_header: str = "Authorization"
    fallback_models: list | None = None  # [{"model", "family"}]; used only if primary fails after retries
    max_retries: int = 2
    backoff_base_s: float = 1.0
    min_max_tokens: int = 0
    timeout_s: float = 60.0

    def families(self) -> list[str]:
        fs = [self.family] + [f.get("family") for f in (self.fallback_models or [])]
        return [f.lower() for f in fs if f]

    @property
    def configured(self) -> bool:
        return bool(self.provider and self.model and self.base_url and self.api_key_env)

    def available(self) -> bool:
        return self.configured and bool(os.environ.get(self.api_key_env or "", ""))

    def status(self) -> str:
        if not self.configured:
            return "skipped:not_configured"
        if not self.available():
            return f"skipped:no_key({self.api_key_env})"
        return "ready"


# Banned outright (any role, any use): the gpt-5.6-luna relay answered as "gpt-5.6-terra"
# (suspected hidden prompt injection).  There is no backup judge any more.
BANNED_MODELS = {"gpt-5.6-luna", "gpt-5.6-terra"}


def _spec(d: dict) -> ModelSpec:
    if "backup_models" in d:
        raise ConfigError(f"{d.get('role')}: backup_models removed - judge 2 is a single model, never switched")
    fields = ModelSpec.__dataclass_fields__
    return ModelSpec(**{k: v for k, v in d.items() if k in fields and v is not None or k in
                        ("role", "provider", "family", "base_url", "model", "api_key_env")})


def load_models(path: Path = MODELS_CONFIG) -> tuple[ModelSpec, list[ModelSpec]]:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    gen = _spec(cfg["generator"])
    judges = [_spec(j) for j in cfg.get("judges", [])]
    validate(gen, judges)
    return gen, judges


def validate(gen: ModelSpec, judges: list[ModelSpec]) -> None:
    """judge families (incl. fallbacks) must differ from the generator family and from each other."""
    for m in [gen] + list(judges):
        names = {m.model} | {f.get("model") for f in (m.fallback_models or [])}
        if names & BANNED_MODELS:
            raise ConfigError(f"{m.role}: banned model {sorted(names & BANNED_MODELS)}")
    gfam = (gen.family or "").lower()
    seen: dict[str, str] = {}
    for j in judges:
        if not j.configured:
            continue
        if not j.family:
            raise ConfigError(f"{j.role}: family is required")
        if j.role == "judge2" and j.fallback_models:
            raise ConfigError("judge2: no fallback/backup models - one model per agreement rate")
        for f in (j.fallback_models or []):
            if not f.get("family") or not f.get("model"):
                raise ConfigError(f"{j.role}: fallback model needs model+family")
        for fam in j.families():
            if gfam and fam == gfam:
                raise ConfigError(f"{j.role}: judge family '{fam}' equals generator family — not allowed")
            if fam in seen and seen[fam] != j.role:
                raise ConfigError(f"{j.role}: family '{fam}' already used by {seen[fam]} — judges must differ")
            seen[fam] = j.role
        if gen.provider and j.provider and j.provider.lower() == gen.provider.lower() and gen.provider != "openrouter":
            raise ConfigError(f"{j.role}: judge provider equals generator provider — not allowed")


def _scrub(msg: str, spec: ModelSpec) -> str:
    key = os.environ.get(spec.api_key_env or "", "")
    if key:
        msg = msg.replace(key, "***")
    return re.sub(r"(sk-|tp-|ttp-)[A-Za-z0-9_\-]{6,}", r"\1***", msg)


class ChatModel:
    """Minimal OpenAI-compatible client.  ``complete`` returns a call record:
    {status, text, model_name, model_version, provider, family, usage, ms}."""

    def __init__(self, spec: ModelSpec, timeout: float | None = None):
        self.spec = spec
        self.timeout = timeout or spec.timeout_s

    @property
    def name(self) -> str:
        return self.spec.model or "-"

    @property
    def family(self) -> str:
        return self.spec.family or "-"

    def complete(self, messages: list[dict], max_tokens: int = 256, temperature: float = 0.0) -> dict:
        """Try the primary model with exponential-backoff retries; only if it ultimately
        fails, try fallback models in order.  The record names the model actually used
        and keeps every attempt (status/http code only — never headers or keys)."""
        sp = self.spec
        rec = {"role": sp.role, "provider": sp.provider, "family": sp.family, "model_name": sp.model,
               "model_version": None, "status": sp.status(), "text": None, "usage": None, "ms": None,
               "error": None, "attempts": [], "fallback_used": False}
        with obs.span(f"chat {sp.model}", kind="CLIENT", **{
                "gen_ai.operation.name": "chat", "gen_ai.provider.name": sp.provider, "gen_ai.request.model": sp.model,
                "gen_ai.request.max_tokens": max_tokens, "gen_ai.request.temperature": temperature,
                "tushen.role": sp.role, "tushen.family": sp.family,
                "tushen.prompt_chars": sum(len(m.get("content") or "") for m in messages)}) as sp_:
            if rec["status"] != "ready":
                obs.set_attrs(sp_, **{"tushen.status": rec["status"]})
                return rec
            ts = obs.now_utc()
            t0 = time.perf_counter()
            chain = [(sp.model, sp.family, sp.min_max_tokens or 0)] + [
                (f["model"], f["family"], f.get("min_max_tokens", 0)) for f in (sp.fallback_models or [])]
            for i, (model, fam, mmt) in enumerate(chain):
                ok, out = self._try_model(model, messages, max(max_tokens, mmt), temperature, rec)
                rec.update(model_name=model, family=fam, fallback_used=i > 0)
                if ok:
                    rec.update(status="ok", error=None, **out)
                    break
                rec.update(status="error", error=out.get("error"))
            rec["ms"] = round((time.perf_counter() - t0) * 1000)
            u = rec.get("usage") or {}
            rec["cost_usd"] = obs.cost_usd(sp.provider, rec["model_name"], u, ts)
            for a in rec["attempts"]:
                if a["status"] != "ok":
                    sp_.add_event("gen_ai.attempt.failed", {"attempt": a["attempt"], "http.status": a.get("http") or 0})
            obs.set_attrs(sp_, **{
                "gen_ai.response.model": rec.get("model_version"), "gen_ai.usage.input_tokens": u.get("prompt_tokens"),
                "gen_ai.usage.output_tokens": u.get("completion_tokens"),
                "tushen.usage.cache_hit_tokens": obs.cache_hit_tokens(u) if u else None,
                "tushen.cost_usd": rec["cost_usd"], "tushen.provider_cost_usd": u.get("cost"),
                "tushen.attempts": len(rec["attempts"]), "tushen.fallback_used": rec["fallback_used"],
                "tushen.status": rec["status"], "tushen.completion_chars": len(rec.get("text") or "")})
            if rec["status"] != "ok":
                obs.set_error(sp_, "http_or_network", rec.get("error") or "")
            return rec

    def _try_model(self, model, messages, max_tokens, temperature, rec) -> tuple[bool, dict]:
        sp = self.spec
        key = os.environ[sp.api_key_env]  # type: ignore[index]
        url = sp.base_url.rstrip("/") + "/chat/completions"  # type: ignore[union-attr]
        body = {"model": model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature, "stream": False}
        if sp.provider == "xiaomi-mimo":
            body["max_completion_tokens"] = body.pop("max_tokens")
            body["thinking"] = {"type": "disabled"}
        if sp.provider == "deepseek":
            body["thinking"] = {"type": "disabled"}
        hdr = {"Content-Type": "application/json"}
        if (sp.auth_header or "Authorization").lower() == "authorization":
            hdr["Authorization"] = f"Bearer {key}"
        else:
            hdr[sp.auth_header] = key
        last = {"error": None}
        for attempt in range(sp.max_retries + 1):
            if attempt:
                time.sleep(sp.backoff_base_s * (2 ** (attempt - 1)))
            code, retry = None, False
            try:
                req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdr, method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    resp = json.loads(r.read())
                if resp.get("error"):
                    raise RuntimeError(str(resp["error"])[:200])
                msg = resp["choices"][0]["message"]
                text = msg.get("content")
                if not (text or "").strip():
                    raise RuntimeError("empty content")
                ver = resp.get("model") or model
                if resp.get("system_fingerprint"):
                    ver += f"/{resp['system_fingerprint']}"
                rec["attempts"].append({"model": model, "attempt": attempt, "status": "ok"})
                return True, {"text": text, "model_version": ver, "usage": resp.get("usage")}
            except urllib.error.HTTPError as e:
                code = e.code
                retry = code in (408, 409, 425, 429) or code >= 500
                detail = e.read()[:300].decode("utf-8", "replace")
                last = {"error": _scrub(f"HTTP {code}: {detail}", sp)}
            except Exception as e:  # network / timeout / empty
                retry = True
                last = {"error": _scrub(repr(e)[:300], sp)}
            rec["attempts"].append({"model": model, "attempt": attempt, "status": "error", "http": code})
            if not retry:
                break
        return False, last


def get_generator() -> ChatModel | None:
    gen, _ = load_models()
    return ChatModel(gen) if gen.available() else None


def count_tokens(text: str) -> int:
    """Cheap, model-agnostic token estimate used for context-size logging:
    1 per CJK char + 1 per ASCII word.  Provider-reported usage is logged too
    when a model is called."""
    return len(re.findall(r"[\u4e00-\u9fff]", text)) + len(re.findall(r"[A-Za-z0-9.]+", text))
