# Changelog

Semua perubahan penting pada project ini didokumentasikan di sini.
Format mengikuti [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.1.0] — 2026-04-10

Feature release — penambahan mode **Grammar Tutor** di dalam Quiz Agent.

### Added — Grammar Tutor Stack

- **`agents/quiz_tutor/planner.py`** — Planner dua tahap: prerequisite check murni
  Python (threshold 60% dari `tutor_topic_tracking` atau `quiz_topic_tracking`),
  lalu distribusi tipe soal via LLM Haiku berdasarkan proficiency level per topik
  (`cold_start` / `familiar` / `advanced`)
- **`agents/quiz_tutor/generator.py`** — Generator soal 6 tipe isian open-ended
  via Claude Sonnet + RAG ChromaDB per topik
- **`agents/quiz_tutor/validator.py`** — Validator struktur soal via Haiku,
  loop regenerate max 3x, forced adjustment sebagai fallback terakhir
- **`agents/quiz_tutor/corrector.py`** — Corrector penilaian 3-tier
  (`full_credit=1.0`, `partial_credit=0.5`, `no_credit=0.0`) via Claude Sonnet,
  feedback 3 layer: `verdict`, `concept_rule`, `feedback_tip`. Fallback
  `is_graded=False` memastikan sesi tidak terputus saat LLM gagal
- **`agents/quiz_tutor/analytics.py`** — Analytics Agent via Claude Sonnet,
  threshold minimum 3 sesi, output: `weak_topics`, `weak_question_types`,
  `recall_vs_application`, `recommendations`, `overall_insight`
- **`prompts/quiz_tutor/`** — 5 prompt file untuk seluruh Grammar Tutor stack
- **`database/repositories/tutor_repository.py`** — Repository CRUD untuk 3 tabel baru
- **`database/models.py`** — 3 tabel baru: `tutor_sessions`, `tutor_questions`,
  `tutor_topic_tracking` (total DB: 14 → 17 tabel)

### Changed

- **`pages/quiz.py`** — Tambah mode selector ("📝 TOEFL Style" / "🎓 Grammar Tutor")
  di bagian atas halaman. TOEFL flow dibungkus ke `_run_toefl_quiz_flow()` tanpa
  perubahan logika. Grammar Tutor flow baru di `_run_tutor_flow()` dengan state
  machine: `config → loading → answering → completed`. UI answering menggunakan
  navigasi Previous/Next bebas + tombol Submit All (batch evaluation)
- **`pages/dashboard.py`** — Tambah panel Grammar Tutor di tab Quiz: total sesi,
  topik terkuat, dan topik terlemah dari `tutor_topic_tracking`

### Technical Notes

- Grammar Tutor stack sepenuhnya independen dari TOEFL Quiz stack —
  tidak ada cross-import antara `agents/quiz/` dan `agents/quiz_tutor/`
- Semua state Grammar Tutor menggunakan prefix `tutor_` di session state
  agar tidak konflik dengan state TOEFL Quiz (prefix `quiz_`)
- Prerequisite check membaca dua tabel secara berurutan: `tutor_topic_tracking`
  lalu `quiz_topic_tracking` — cukup salah satu yang memenuhi threshold 60%

---

## [1.0.2] — 2026-04-05

Patch release — peningkatan UX Vocab Agent berdasarkan feedback testing.

### Changed

- **`pages/vocab.py`** — Ubah flow answering dari evaluasi per soal menjadi
  batch evaluation setelah semua soal selesai dijawab, menghilangkan latency
  yang muncul setiap kali user submit jawaban.
- **`pages/vocab.py`** — Tambah navigasi bebas antar soal (tombol Sebelumnya /
  Berikutnya) dengan pre-fill jawaban saat user kembali ke soal sebelumnya.
  Tombol Submit Semua hanya aktif setelah semua soal terisi.
- **`pages/vocab.py`** — Ganti pilihan jumlah soal dari slider menjadi radio
  button dengan 4 pilihan tetap: 5, 10, 15, 20 soal.
- **`config/settings.py`** — Tambah `VOCAB_FORMAT_PCT` dan `VOCAB_MIN_WORDS`,
  `VOCAB_MAX_WORDS` untuk distribusi format berbasis persentase per difficulty.
- **`prompts/vocab/planner_prompt.py`** — Ganti `DEFAULT_PLANNER_CONFIG` static
  dengan `build_default_planner_config()` yang menghitung distribusi format
  secara dinamis berdasarkan `total_words`.
- **`prompts/vocab/planner_prompt.py`** — Update system prompt untuk membatasi
  `sinonim_antonim` maksimal 20% di semua level difficulty.
- **`agents/vocab/planner.py`** — `run_planner()` dan `_call_planner_llm()`
  menerima parameter `total_words` dari UI. Tambah `_fix_format_distribution()`
  sebagai safety net di semua return path.

### Fixed

- **`pages/vocab.py`** — Filter word object dengan `format`, `question_text`,
  atau `correct_answer` bernilai `None` sebelum disimpan ke DB, mencegah
  `NOT NULL constraint failed` pada review words dari spaced repetition.

### Technical Notes

- Tidak ada perubahan pada agent layer (evaluator, generator, validator) —
  semua perbaikan terjadi di UI layer dan config
- Jumlah soal kini bisa dipilih user (5/10/15/20), default tetap 10


## [1.0.1] — 2026-04-02

Patch release — perbaikan bug runtime, fitur tidak lengkap, dan CI pipeline.

### Fixed — Kategori 1: Runtime Crash

- **`agents/toefl/validator.py`** — F821 `attempt` undefined: lambda di `regen_map`
  mencoba menangkap variabel `attempt` sebelum loop didefinisikan. Diperbaiki
  dengan mengoper `attempt` sebagai parameter lambda saat dipanggil.
- **`prompts/speaking/assessor_prompt.py`** — F821 `PROMPTED_RESPONSE_MAX` undefined:
  konstanta dipakai di `build_assessor_prompt()` tapi tidak pernah didefinisikan
  di file ini. Diperbaiki dengan menambahkan definisi konstanta langsung di file prompt.

### Fixed — Kategori 2: Fitur Tidak Lengkap

- **`pages/toefl.py`** — `update_current_section()` diimport tapi tidak pernah
  dipanggil. Diperbaiki dengan memanggil fungsi ini saat user klik "Lanjut ke
  Section berikutnya" di pause screen, agar DB selalu tahu posisi user untuk
  keperluan resume.
- **`modules/session/toefl_session_manager.py`** — `get_abandoned_sessions()` dan
  `update_session_status()` diimport tapi tidak ada fungsi yang memakainya.
  Diperbaiki dengan menambahkan fungsi `cleanup_expired_toefl_sessions()` yang
  menandai sesi TOEFL expired sebagai `abandoned`.
- **`pages/toefl.py`** — Tambahkan pemanggilan `cleanup_expired_toefl_sessions()`
  di state `init` sebelum mencari sesi paused, agar sesi expired tidak muncul
  di resume screen.
- **`tests/integration/test_toefl_flow.py`** — `l_ids`, `s_ids`, `r_ids` diassign
  tapi assertion-nya tidak ditulis. Diperbaiki dengan menambahkan assertion
  `all(i is not None for i in ...)` untuk memverifikasi semua soal berhasil
  disimpan ke DB.
- **`tests/integration/test_vocab_flow.py`** — `eval_resp` diassign sebagai JSON
  string tapi tidak dipakai. Dihapus karena mock LLM sudah menggunakan dict
  langsung.

### Fixed — Kategori 3: Import Tidak Terpakai

- `agents/toefl/listening_generator.py` — hapus `import time`
- `modules/audio/recorder.py` — hapus `import os` dan `import tempfile`
- `modules/audio/stt.py` — hapus `import os`
- `modules/session/toefl_session_manager.py` — hapus `field` dari dataclasses import
- `modules/speaking/audio_pipeline.py` — hapus import lokal `transcribe_audio_bytes`
  di dalam `_transcribe_file()`, pakai top-level import
- `pages/dashboard.py` — hapus `import traceback` dan import lokal `update_user_profile`
  di dalam `_render_profile_editor()`
- `pages/speaking.py` — hapus `from utils.logger import logger`
- `pages/toefl.py` — hapus `from typing import Optional`
- `pages/quiz.py` — hapus variable `results` yang diassign tapi tidak dipakai
- `pages/vocab.py` — hapus variable `results` yang diassign tapi tidak dipakai
- `prompts/quiz/generator_prompt.py` — hapus `import json`
- `prompts/vocab/planner_prompt.py` — hapus `import json` dan trailing whitespace
- `prompts/toefl/validator_prompt.py` — pindahkan `json.dumps()` dari dalam f-string
  ke variable terpisah untuk menghindari E122 dan E999
- `scripts/reset_database.py` — tambah `# noqa: E402, F401` untuk import yang
  harus berada di luar top-level karena `sys.path` manipulation
- `utils/helpers.py` — hapus `from typing import Optional`
- `utils/retry.py` — hapus `import functools`, `Callable`, `Type`, dan `after_log`
- `tests/unit/test_toefl_agent.py` — rename variable `l` menjadi `listening_count`
- Berbagai file test — hapus top-level import yang orphan karena dipakai secara
  lokal di dalam method

### Fixed — CI/CD

- **`.github/workflows/ci.yml`** — ganti `libportaudio-dev` dengan `portaudio19-dev`
  karena package lama sudah tidak tersedia di Ubuntu runner GitHub Actions terbaru.

### Technical Notes

- Semua 105+ test tetap passing setelah seluruh perubahan
- Tidak ada perubahan pada logika bisnis atau fitur yang sudah ada
- CI pipeline (flake8 + black + pytest) kini hijau penuh

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
- `agents/quiz/planner.py` — prerequisite awareness + 5 logic hierarki
- `agents/quiz/generator.py` — generate soal via Claude Sonnet + RAG
- `agents/quiz/validator.py` — validasi + auto-adjust
- `agents/quiz/corrector.py` — 4 lapisan feedback
- `agents/quiz/analytics.py` — insight per topik
- `prompts/quiz/` — semua prompt quiz agent
- `pages/quiz.py` — UI Streamlit quiz
- `config/prerequisite_rules.json` — aturan prerequisite antar topik
- `config/cluster_metadata.json` — cluster metadata topik grammar

### Added — Phase 4: Speaking Agent
- `agents/speaking/generator.py` — generate prompt per sub-mode
- `agents/speaking/assessor.py` — sliding window conversation assessor
- `agents/speaking/follow_up.py` — follow up generator
- `agents/speaking/evaluator.py` — scoring multi-kriteria per sub-mode
- `agents/speaking/analytics.py` — insight per sesi
- `prompts/speaking/` — semua prompt speaking agent
- `pages/speaking.py` — UI Streamlit speaking (3 sub-mode)
- `config/speaking_metadata.json` — 14 kategori TOEFL Preparation

### Added — Phase 5: TOEFL Simulator
- `agents/toefl/planner.py` — distribusi soal per mode
- `agents/toefl/listening_generator.py` — generate dialog + TTS multi-voice
- `agents/toefl/structure_generator.py` — generate soal grammar + RAG
- `agents/toefl/reading_generator.py` — generate passage + soal
- `agents/toefl/validator.py` — quality gate 80% threshold
- `agents/toefl/evaluator.py` — konversi skor ITP resmi
- `agents/toefl/analytics.py` — trend skor per simulasi
- `prompts/toefl/` — semua prompt TOEFL agent
- `pages/toefl.py` — UI Streamlit TOEFL dengan timer dan pause/resume

### Added — Phase 6: Orchestrator & Dashboard
- `agents/orchestrator/router.py` — routing + onboarding + profile management
- `agents/orchestrator/master_analytics.py` — cross-mode analysis
- `prompts/analytics/` — prompt analytics per mode
- `pages/dashboard.py` — 3-layer dashboard (Quick Snapshot, Per-Mode Summary, Deep Analysis)
- `app.py` — entry point dengan Router Guard dan navigasi sidebar

### Added — Phase 7: Testing & Documentation
- `tests/unit/test_vocab_agent.py` — 16 unit test
- `tests/unit/test_quiz_agent.py` — 20 unit test
- `tests/unit/test_speaking_agent.py` — 22 unit test
- `tests/unit/test_toefl_agent.py` — 45 unit test
- `tests/integration/test_vocab_flow.py` — 12 integration test
- `tests/integration/test_quiz_flow.py` — 12 integration test
- `tests/integration/test_speaking_flow.py` — 13 integration test
- `tests/integration/test_toefl_flow.py` — 12 integration test
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