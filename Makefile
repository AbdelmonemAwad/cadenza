SHELL := /bin/bash
IMAGE := cadenza/cadenza:1.0.0
PKG_VERS ?= 1.0.0-0001

.PHONY: help image spk build test lint dev-api dev-ui clean

help:
	@echo "Cadenza - available targets:"
	@echo "  make image    Build the Docker image (linux/amd64)"
	@echo "  make spk      Build the .spk into dist/ (reuses an existing image)"
	@echo "  make build    image, then spk"
	@echo "  make test     Run the backend test suite and frontend type check"
	@echo "  make lint     Run ruff over the backend"
	@echo "  make dev-api  Run the API locally with reload"
	@echo "  make dev-ui   Run the UI dev server"
	@echo "  make clean    Remove build artefacts"

image:
	docker build --platform linux/amd64 -f docker/Dockerfile -t $(IMAGE) .

spk:
	PKG_VERS=$(PKG_VERS) SKIP_IMAGE=1 bash packaging/synology/build-spk.sh

build:
	PKG_VERS=$(PKG_VERS) bash packaging/synology/build-spk.sh

test:
	cd backend && pytest
	cd frontend && npx tsc --noEmit

lint:
	cd backend && ruff check app tests

dev-api:
	cd backend && \
		CADENZA_MUSIC_ROOT=$${CADENZA_MUSIC_ROOT:-./_devmusic} \
		CADENZA_CONFIG_DIR=$${CADENZA_CONFIG_DIR:-./_devconfig} \
		CADENZA_QUARANTINE_ROOT=$${CADENZA_QUARANTINE_ROOT:-./_devquarantine} \
		python -m uvicorn app.main:app --reload --port 8000

dev-ui:
	cd frontend && npm run dev

clean:
	rm -rf build dist frontend/dist backend/.pytest_cache backend/.ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
