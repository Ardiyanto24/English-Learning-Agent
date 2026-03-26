# Changelog

Semua perubahan penting pada project ini didokumentasikan di sini.
Format mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.0] — 2026-03-26

Rilis pertama English Learning AI Agent — project selesai sepenuhnya.

### Added — Phase 0: Project Setup
- Struktur folder project lengkap
- `requirements.txt` dengan semua dependency ter-pin
- `pyproject.toml` dengan konfigurasi Black dan pytest
- `docker/Dockerfile` dan `docker-compose.yml`
- `.gitignore` komprehensif

### Added — Phase 1: Foundation Layer
- `database/models.py` — 14 tabel SQLite dengan index lengkap
- `database/connection.py` — koneksi WAL mode dengan context manager
- `database/repositories/` — 5 repository (session, vocab, quiz, speaking, toefl)
- `modules/rag/indexer.py` — ChromaDB indexer dengan chunking
- `modules/rag/retriever.py` — retriever dengan similarity threshold 0.75
- `modules/audio/stt.py` — Google Cloud Speech-to-Text
- `modules/audio/tts.py` — Google Cloud Text-to-Speech
- `modules/audio/recorder.py` — audio recorder
- `modules/audio/audio_pipeline.py` — pipeline koordinasi audio
- `modules/scoring/toefl_converter.py` — konversi skor ITP resmi
- `modules/session/toefl_session_manager.py` — pause/resume TOEFL
- `utils/logger.py`, `utils/retry.py`, `utils/helpers.py`
- `scripts/index_knowledge_base.py` — runner indexing KB
- `scripts/reset_database.py` — reset DB

### Added — Phase 2: Vocab Agent
- `agents/vocab/planner.py` — cold start + spaced repetition
- `agents/vocab/generator.py` — generate soal via Claude Haiku
- `agents/vocab/validator.py` — validasi + auto-adjust
- `agents/vocab/evaluator.py` — penilaian kontekstual
- `agents/vocab/analytics.py` — insight per sesi
- `prompts/vocab/` — semua prompt vocab agent
- `pages/vocab.py` — UI Streamlit vocab
- `config/settings.py` — konfigurasi global
- Knowledge base vocabulary topik situasi (8 topik)

### Added — Phase 3: Quiz Agent
- `agents/quiz/planner.py` — 5 logic hierarki tanpa LLM
- `agents/quiz/generator.py` — generate soal dengan RAG context
- `agents/quiz/validator.py` — validasi + regenerate max 3x
- `agents/quiz/corrector.py` — 4 lapisan feedback pedagogis
- `agents/quiz/analytics.py` — insight per topik + prerequisite
- `prompts/quiz/` — semua prompt quiz agent
- `pages/quiz.py` — UI Streamlit quiz dengan Human-in-the-Loop
- `config/prerequisite_rules.json` — 47 topik dengan dependency graph
- `config/cluster_metadata.json` — pengelompokan topik
- Knowledge base grammar 47 topik

### Added — Phase 4: Speaking Agent
- `agents/speaking/generator.py` — generate prompt pembuka
- `agents/speaking/assessor.py` — sliding window assessment
- `agents/speaking/follow_up.py` — generate follow-up question
- `agents/speaking/evaluator.py` — evaluasi full transcript
- `agents/speaking/analytics.py` — insight speaking
- `prompts/speaking/` — semua prompt speaking agent
- `pages/speaking.py` — 3 sub-mode: Prompted Response, Conversation Practice, Oral Presentation
- `config/speaking_metadata.json` — 14 kategori topik TOEFL

### Added — Phase 5: TOEFL Simulator
- `agents/toefl/listening_generator.py` — generate dialog/monolog
- `agents/toefl/structure_generator.py` — generate soal grammar
- `agents/toefl/reading_generator.py` — generate passage + soal
- `prompts/toefl/` — semua prompt TOEFL agent
- `pages/toefl.py` — simulator lengkap dengan timer dan pause/resume
- Konversi skor ITP resmi (tabel Listening/Structure/Reading)
- Mode 50% / 75% / 100% dengan distribusi soal proporsional

### Added — Phase 6: Orchestrator & Dashboard
- `agents/orchestrator/router.py` — routing context + onboarding
- `agents/orchestrator/master_analytics.py` — cross-mode analysis
- `prompts/analytics/` — prompt analytics per mode
- `pages/dashboard.py` — 3-layer dashboard (Quick Snapshot, Per-Mode Summary, Deep Analysis)
- `app.py` — entry point dengan Router Guard dan navigasi sidebar

### Added — Phase 7: Testing & Documentation
- `tests/unit/test_vocab_agent.py` — 16 unit test
- `tests/unit/test_quiz_agent.py` — 20 unit test
- `tests/unit/test_speaking_agent.py` — 22 unit test
- `tests/unit/test_toefl_agent.py` — 45 unit test (ModeConfig, Converter, SessionManager)
- `tests/integration/test_vocab_flow.py` — 12 integration test
- `tests/integration/test_quiz_flow.py` — 12 integration test (termasuk 4-layer feedback)
- `tests/integration/test_speaking_flow.py` — 13 integration test (termasuk recovery flow)
- `tests/integration/test_toefl_flow.py` — 12 integration test (termasuk pause/resume)
- `docs/setup/installation.md`
- `docs/setup/configuration.md`
- `docs/setup/knowledge_base.md`
- `docs/guides/adding_materials.md`
- `docs/guides/troubleshooting.md`

### Changed
- Migrasi audio STT dari OpenAI Whisper ke **Google Cloud Speech-to-Text**
- Migrasi audio TTS dari OpenAI TTS ke **Google Cloud Text-to-Speech**
- `verify_setup.py` diupdate untuk test koneksi Google Cloud

### Technical Notes
- Total test: 105+ test (unit + integration), semua passing
- Database: SQLite dengan WAL mode, 14 tabel, foreign key enforcement
- LLM calls: semua di-wrap dengan `@retry_llm` (max 3x, exponential backoff)
- Error handling: setiap agent punya fallback — tidak ada yang crash ke user

---

## [Unreleased] - Initial project setup

### Added
- Inisialisasi struktur folder project
- Konfigurasi file dasar (.gitignore, README.md, CHANGELOG.md)