# ================================================
# Makefile — Shortcut commands untuk development
# Penggunaan: make <command>
# Contoh: make run, make test, make lint
# ================================================

# Deteksi OS untuk kompatibilitas Windows vs Unix
ifeq ($(OS),Windows_NT)
	PYTHON = python
	VENV_ACTIVATE = venv\Scripts\activate
else
	PYTHON = python3
	VENV_ACTIVATE = source venv/bin/activate
endif

.PHONY: run test lint docker-up docker-down index-kb install help

## help: Tampilkan daftar semua command yang tersedia
help:
	@echo "Daftar command yang tersedia:"
	@echo ""
	@echo "  make run        - Jalankan Streamlit app di localhost:8501"
	@echo "  make test       - Jalankan semua unit dan integration test"
	@echo "  make lint       - Jalankan flake8 + black check"
	@echo "  make docker-up  - Jalankan aplikasi via Docker Compose"
	@echo "  make docker-down- Hentikan Docker Compose"
	@echo "  make index-kb   - Index knowledge base ke ChromaDB"
	@echo "  make install    - Install semua dependencies"
	@echo ""

## run: Jalankan Streamlit app
run:
	streamlit run app.py --server.port=8501

## test: Jalankan pytest
test:
	pytest tests/ -v --tb=short

## lint: Jalankan flake8 dan black check
lint:
	flake8 .
	black --check .

## format: Auto-format kode dengan Black
format:
	black .

## docker-up: Jalankan Docker Compose (production)
docker-up:
	docker-compose -f docker/docker-compose.yml up --build

## docker-down: Hentikan Docker Compose
docker-down:
	docker-compose -f docker/docker-compose.yml down

## docker-dev: Jalankan Docker Compose (development mode)
docker-dev:
	docker-compose -f docker/docker-compose.dev.yml up --build

## index-kb: Jalankan script indexing knowledge base ke ChromaDB
index-kb:
	$(PYTHON) scripts/index_knowledge_base.py

## install: Install semua dependencies
install:
	pip install -r requirements.txt -r requirements-dev.txt