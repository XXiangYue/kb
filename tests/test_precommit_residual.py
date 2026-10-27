"""Pre-commit hook + check_secrets reject staged standards text (synthetic standard, not real text)."""
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SYNTH = ("4.2.7 测试楼梯间的前室应采用乙级防火门，前室的使用面积不应小于六平方米，"
         "且应在前室入口处设置明显的测试标识和应急照明灯具。")


def _db(path: Path, text: str = SYNTH) -> Path:
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE doc_text (doc_id TEXT, text TEXT)")
    c.execute("CREATE TABLE chunks (doc_id TEXT, char_start INT, clause_no TEXT, kind TEXT)")
    c.execute("INSERT INTO doc_text VALUES ('SYN-1', ?)", (text,))
    c.execute("INSERT INTO chunks VALUES ('SYN-1', 0, '4.2.7', 'clause')")
    c.commit()
    c.close()
    return path


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(PY=sys.executable, PYTHONPATH=str(ROOT), STDREF_DB=str(_db(tmp_path / "synth.sqlite")),
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    env.pop("STDREF_REQUIRED", None)
    git = lambda *a, check=True: subprocess.run(["git", *a], cwd=r, env=env, capture_output=True, text=True, check=check)
    git("init", "-q")
    shutil.copytree(ROOT / ".githooks", r / ".githooks")
    (r / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check_secrets.py", r / "scripts" / "check_secrets.py")
    git("config", "core.hooksPath", ".githooks")
    git("add", ".")
    git("commit", "-q", "-m", "init")
    return r, git, env


def _head(git):
    return git("rev-parse", "HEAD").stdout.strip()


def test_staged_standards_text_is_rejected_by_hook(repo):
    r, git, _ = repo
    before = _head(git)
    (r / "answer.jsonl").write_text('{"answer": "依据：前室的使用面积不应小于六平方米，且应在前室入口处设置明显的测试标识。"}\n',
                                    encoding="utf-8")
    git("add", "answer.jsonl")
    p = git("commit", "-m", "leak", check=False)
    out = p.stdout + p.stderr                            # git relays hook output on stderr
    assert p.returncode != 0
    assert "NOT CLEAN" in out and "standards text" in out and "answer.jsonl" in out
    assert "六平方米" not in out          # matched text is never echoed
    assert _head(git) == before


def test_staged_content_checked_even_if_worktree_cleaned(repo):
    r, git, _ = repo
    f = r / "notes.md"
    f.write_text("前室的使用面积不应小于六平方米，且应在前室入口处设置明显的测试标识和应急照明灯具。\n", encoding="utf-8")
    git("add", "notes.md")
    f.write_text("改写后的说明，没有原文。\n", encoding="utf-8")   # work tree clean, index still has the text
    p = git("commit", "-m", "leak", check=False)
    assert p.returncode != 0 and "notes.md (staged)" in p.stdout + p.stderr


def test_reference_token_and_clean_text_pass(repo):
    r, git, _ = repo
    from tushen_kb.stdref import Reference
    ref = Reference({"SYN-1": SYNTH}, {"SYN-1": [(0, "4.2.7")]})
    red, n = ref.redact("依据：前室的使用面积不应小于六平方米，且应在前室入口处设置明显的测试标识。")
    assert n > 0
    (r / "answer.jsonl").write_text('{"answer": "%s"}\n' % red, encoding="utf-8")
    (r / "readme.md").write_text("自己写的说明文字，与标准无关，长度超过二十个字符的一句话。\n", encoding="utf-8")
    git("add", ".")
    p = git("commit", "-q", "-m", "ok", check=False)
    assert p.returncode == 0, p.stdout + p.stderr


def test_missing_index_skips_or_fails_when_required(repo, tmp_path):
    r, git, env = repo
    cmd = [sys.executable, str(r / "scripts" / "check_secrets.py"), "--stdref-db", str(tmp_path / "missing.sqlite")]
    p = subprocess.run(cmd, cwd=r, env=env, capture_output=True, text=True)
    assert p.returncode == 0 and "SKIPPED" in p.stdout
    p = subprocess.run(cmd + ["--require-residual"], cwd=r, env=env, capture_output=True, text=True)
    assert p.returncode == 1 and "SKIPPED" in p.stdout
