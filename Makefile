.PHONY: install web test lint serve dev backtest demo docker

install:
	pip install -e ".[dev]" && cd web && npm ci

web:
	cd web && npm run build

test:
	pytest -q && cd web && npm run typecheck

lint:
	ruff check src tests scripts

serve: web          ## API + dashboard on http://localhost:8000
	agora serve

dev:                ## API on :8000 and Vite dev server on :5173 (proxying /api)
	agora serve & cd web && npm run dev

backtest:
	for s in 7 21 42; do agora backtest --days 120 --seed $$s; done

demo:               ## static, backend-free dashboard of a recorded 60-day run in web/dist-demo
	python scripts/export_demo.py --days 60 && cd web && npm run build:demo

docker:
	docker build -t agora . && docker run -p 8000:8000 -v agora-data:/data --env-file .env agora
