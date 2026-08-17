# PAVE — the checks that must pass before a commit or a deploy.
#
# Everything here runs offline with the system Python and no pip install: the sandbox has
# no network, and a gate nobody can run is not a gate. Real providers stay disabled, so
# `make check` can never touch a live workspace.

PY ?= python3
APP := src/app

.DEFAULT_GOAL := check

.PHONY: check test parity js run bundle clean

## check: everything that gates a commit (tests + parity + SPA syntax)
check: test parity js
	@echo "\nAll PAVE checks passed."

## test: logic + service layer in demo mode (in-memory store, real providers off)
test:
	@echo "== smoke =="
	@PAVE_ALLOW_REAL=0 $(PY) tests/smoke.py

## parity: simulated providers must be a faithful stand-in for the real ones
parity:
	@echo "\n== parity =="
	@PAVE_ALLOW_REAL=0 $(PY) tests/parity.py

## js: syntax gate for the build-free SPA (there is no bundler to catch this)
js:
	@echo "\n== spa =="
	@node --check $(APP)/backend/static/assets/app.js && echo "  PASS  app.js parses"
	@$(PY) -c "import pathlib,html.parser as h; \
p=pathlib.Path('$(APP)/backend/static/index.html'); \
h.HTMLParser().feed(p.read_text()); print('  PASS  index.html parses')"

## run: local demo on http://127.0.0.1:8731 (in-memory unless LAKEBASE_INSTANCE is set)
run:
	@cd $(APP) && PAVE_ALLOW_REAL=0 $(PY) -m uvicorn backend.main:app --host 127.0.0.1 --port 8731

## bundle: validate the asset bundle against the dev target
bundle:
	@databricks bundle validate -t dev

clean:
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
