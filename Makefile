PY ?= .venv/bin/python

.PHONY: test eval eval-smoke eval-report

test:
	$(PY) -m pytest -q

# full agent-level e2e regression (test split, 414 questions x 2 configs + stability + judge2 sample + overhead)
eval:
	$(PY) -m tushen_kb.eval.agent_eval --suite agent

# CI-style smoke subset (20 test questions, judge1 only)
eval-smoke:
	$(PY) -m tushen_kb.eval.agent_eval --suite smoke

# rebuild report.md / report.json from existing checkpoints + traces (no API calls)
eval-report:
	$(PY) -m tushen_kb.eval.agent_eval --suite agent --phase report
