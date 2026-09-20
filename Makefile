PYTHON ?= python3
PORT ?= 8000
.PHONY: demo install build test test-ui actor clean-data reproduce-uncertain

.venv/.installed: pyproject.toml requirements.lock
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -r requirements.lock -e .
	touch $@

frontend/node_modules/.installed: frontend/package.json frontend/package-lock.json
	cd frontend && npm ci
	touch $@

install: .venv/.installed frontend/node_modules/.installed

build: frontend/node_modules/.installed
	cd frontend && npm run build

demo: install build
	.venv/bin/python scripts/demo.py --port $(PORT)

test: .venv/.installed
	.venv/bin/python -m pytest -q

test-ui: install build
	cd frontend && npx playwright install chromium && npm test

reproduce-uncertain: install
	cd frontend && npx playwright install chromium
	.venv/bin/python scripts/reproduce_uncertain.py --checks all

actor: .venv/.installed
	SAAC_ACTOR_TOKEN="$$(cat .runtime/actor.token)" .venv/bin/python -m saac.actor --url http://127.0.0.1:$(PORT)

clean-data:
	@echo "To start a fresh book without deleting evidence, run: .venv/bin/python scripts/demo.py --data-dir .runtime/fresh --port 8001"
