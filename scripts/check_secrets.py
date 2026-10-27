#!/usr/bin/env python3
"""Pre-commit secret self-check.  Never prints secret values.

1. For each env var in KEY_ENVS that is set, check whether its VALUE occurs in
   any tracked or staged file (and in untracked, non-ignored files).
2. Grep the same files for key-like patterns (sk-..., tp-..., API_KEY=...,
   Bearer <token>).
3. Check that no forbidden file types are tracked/staged (pdf, sqlite, db,
   .env, weights).
4. Standards residual scan (`tushen_kb.stdref`): fail if any tracked, untracked
   (non-ignored) or STAGED file contains a 20-char window of standards text.
   Needs the local standards index (data/tushen.sqlite, or --stdref-db / $STDREF_DB);
   without it the step is SKIPPED (reported), or fails with --require-residual /
   STDREF_REQUIRED=1.  Matched text is never printed, only paths and counts.
Exit code 0 = clean.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KEY_ENVS = ["DEEPSEEK_API_KEY", "MIMO_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "HF_TOKEN"]
# plus ANY env var that looks like a credential (…_API_KEY, …_API_KEY_<n>, …_TOKEN, …_SECRET) with a long value,
# so retired/renamed provider keys are still scanned without naming them here
KEY_ENV_RE = re.compile(r"(_API_KEY(_\d+)?|_TOKEN|_SECRET)$")
PATTERNS = {
    "sk-token": re.compile(r"\bsk-[A-Za-z0-9]{16,}"),
    "tp-token": re.compile(r"\bt?tp-[A-Za-z0-9]{16,}"),
    "API_KEY=value": re.compile(r"API_KEY\s*=\s*['\"]?[A-Za-z0-9_\-]{8,}"),
    "openrouter-token": re.compile(r"\bsk-or-[A-Za-z0-9\-]{16,}"),
    "bearer-literal": re.compile(r"Bearer\s+[A-Za-z0-9_\-]{20,}"),
}
FORBIDDEN = re.compile(r"(\.pdf|\.sqlite3?|\.db|\.env(\..*)?|\.key|\.pem|\.safetensors|\.bin|\.onnx|\.pt|\.gguf)$", re.I)


def files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    return sorted({f for f in out if f and os.path.isfile(f)})


def residual(db: Path, required: bool) -> list[str]:
    """Standards residual scan of tracked/untracked/staged content in the current repo."""
    if not db.exists():
        msg = f"standards residual scan SKIPPED: {db} not found (download_corpus.sh + ingest to enable)"
        print(msg)
        return [msg] if required else []
    from tushen_kb import stdref
    out = stdref.scan_worktree(stdref.Reference.from_db(db), repo=Path.cwd())
    print(f"standards residual scan (k={out['k']}): {out['files_scanned']} files, "
          f"{out['files_with_hits']} with hits, {out['total_windows']} windows")
    return [f"standards text ({h['windows']} windows, {h['chars']} chars) in {h['path']}" for h in out["hits"]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stdref-db", default=os.environ.get("STDREF_DB", str(ROOT / "data" / "tushen.sqlite")))
    ap.add_argument("--require-residual", action="store_true", default=os.environ.get("STDREF_REQUIRED") == "1")
    a = ap.parse_args(argv)
    fs = files()
    names = set(KEY_ENVS) | {n for n, v in os.environ.items() if KEY_ENV_RE.search(n) and len(v) >= 16}
    keys = {name: os.environ[name] for name in sorted(names) if os.environ.get(name)}
    problems = []
    for f in fs:
        if FORBIDDEN.search(f):
            problems.append(f"forbidden file type: {f}")
        try:
            data = open(f, "rb").read().decode("utf-8", "ignore")
        except OSError:
            continue
        for name, val in keys.items():
            if val in data:
                problems.append(f"value of ${name} found in {f}")
        for pname, pat in PATTERNS.items():
            if pat.search(data):
                problems.append(f"pattern {pname} in {f}")
    print(f"checked {len(fs)} files; env key values compared: {sorted(keys)} (values not shown); "
          f"patterns: {sorted(PATTERNS)}")
    problems += residual(Path(a.stdref_db), a.require_residual)
    for p in problems:
        print("PROBLEM:", p)
    print("CLEAN" if not problems else f"NOT CLEAN ({len(problems)})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
