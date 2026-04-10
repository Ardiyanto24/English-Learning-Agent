# 🎓 English Learning AI Agent

Aplikasi personal berbasis AI untuk persiapan **TOEFL ITP** — fully automated dari generate materi, latihan adaptif, evaluasi mendalam, hingga analitik performa layaknya tutor profesional.

> Dibangun sebagai personal project untuk menggantikan kebutuhan persiapan TOEFL manual yang memakan waktu.

---

## ✨ Fitur Utama

| Mode | Deskripsi |
|---|---|
| 📚 **Vocab Agent** | Latihan kosakata dengan spaced repetition — kata lemah diprioritaskan otomatis |
| 📝 **Quiz Agent — TOEFL Style** | Latihan grammar 47 topik bergaya TOEFL ITP dengan feedback 4 lapisan: verdict, explanation, concept, example |
| 🎓 **Quiz Agent — Grammar Tutor** | Mode tutor konseptual: 6 tipe soal isian open-ended, penilaian 3-tier (full/partial/no credit), feedback berlapis per soal |
| 🎙️ **Speaking Agent** | 3 sub-mode: Prompted Response, Conversation Practice, Oral Presentation |
| 🎯 **TOEFL Simulator** | Simulasi penuh ITP mode 50%/75%/100% dengan estimasi skor 310–677 |
| 📊 **Dashboard Analytics** | 3-layer dashboard: quick snapshot, per-mode summary, deep AI analysis |

---

## 🏗️ Arsitektur

```
┌─────────────────────────────────────────────────────────────┐
│                        Streamlit UI                         │
├──────────┬───────────────────┬──────────┬───────────────────┤
│  Vocab   │   Quiz Agent      │ Speaking │  TOEFL Simulator  │
│  Agent   │ TOEFL │ Tutor     │  Agent   │                   │
├──────────┴───────────────────┴──────────┴───────────────────┤
│                   Orchestrator & Router                     │
├─────────────────────┬───────────────────────────────────────┤
│   Claude API        │   Google Cloud STT/TTS               │
│ (Haiku + Sonnet)    │   (Speech-to-Text/Text-to-Speech)    │
├─────────────────────┼───────────────────────────────────────┤
│   SQLite (17 tabel) │   ChromaDB (RAG)                     │
└─────────────────────┴───────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

- **Frontend**: Streamlit
- **LLM**: Claude Haiku & Claude Sonnet (Anthropic API)
- **RAG**: ChromaDB + sentence-transformers
- **Database**: SQLite dengan WAL mode
- **Audio STT**: Google Cloud Speech-to-Text
- **Audio TTS**: Google Cloud Text-to-Speech
- **Orchestration**: LangChain + LangGraph

---

## 📋 Prerequisites

- Python 3.10+
- [Anthropic API Key](https://console.anthropic.com/settings/keys)
- [Google Cloud Service Account](https://console.cloud.google.com/iam-admin/serviceaccounts) dengan role:
  - Cloud Speech Client
  - Cloud Speech Editor
- Git

---

## 🚀 Instalasi

### 1. Clone repository

```bash
git clone https://github.com/YOUR_USERNAME/english_learning_agent.git
cd english_learning_agent
```

### 2. Buat virtual environment

```bash
python -m venv myvenv

# Windows
myvenv\Scripts\activate

# macOS / Linux
source myvenv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Konfigurasi environment variables

```bash
cp .env.example .env
```

Buka `.env` dan isi:

```env
ANTHROPIC_API_KEY=sk-ant-xxxx...
GOOGLE_APPLICATION_CREDENTIALS=./gcp-service-account.json
DATABASE_PATH=./english_agent.db
CHROMA_DB_PATH=./vector_store/chroma_db
```

Letakkan file `gcp-service-account.json` di root project.

### 5. Index knowledge base

```bash
python scripts/index_knowledge_base.py
```

Proses ini mengindeks materi grammar ke ChromaDB. Hanya perlu dijalankan sekali.

### 6. Jalankan aplikasi

```bash
streamlit run app.py
```

Buka browser di `http://localhost:8501`.

---

## 🐳 Menjalankan dengan Docker

```bash
# Build image
docker build -f docker/Dockerfile .

# Jalankan dengan docker-compose
docker-compose -f docker/docker-compose.yml up
```

---

## 📁 Struktur Project

```
english_learning_agent/
├── agents/                  # AI Agent per mode
│   ├── vocab/               # Planner, Generator, Validator, Evaluator
│   ├── quiz/                # Planner, Generator, Validator, Corrector (TOEFL Style)
│   ├── quiz_tutor/          # Planner, Generator, Validator, Corrector, Analytics (Grammar Tutor)
│   ├── speaking/            # Generator, Assessor, Follow-up, Evaluator
│   ├── toefl/               # Listening/Structure/Reading Generator
│   └── orchestrator/        # Router, Master Analytics
├── database/
│   ├── models.py            # 17 tabel SQLite
│   └── repositories/        # CRUD per mode
├── docs/                    # Dokumentasi lengkap
├── knowledge_base/          # Materi grammar (Markdown)
├── modules/
│   ├── audio/               # STT, TTS, Recorder
│   ├── rag/                 # Indexer, Retriever
│   ├── scoring/             # TOEFL ITP Converter
│   └── session/             # TOEFL Session Manager
├── pages/                   # Halaman Streamlit per mode
├── prompts/                 # System & user prompts per agent
├── tests/
│   ├── unit/                # Unit test per agent
│   └── integration/         # Integration test per flow
├── app.py                   # Entry point
└── config/
    ├── settings.py
    ├── prerequisite_rules.json
    └── cluster_metadata.json
```

---

## 🧪 Menjalankan Test

```bash
# Semua test
pytest tests/ -v

# Unit test saja
pytest tests/unit/ -v

# Integration test saja
pytest tests/integration/ -v
```

---

## 📚 Dokumentasi

| Dokumen | Deskripsi |
|---|---|
| [Instalasi Detail](docs/setup/installation.md) | Panduan instalasi lengkap step-by-step |
| [Konfigurasi](docs/setup/configuration.md) | Penjelasan setiap environment variable |
| [Knowledge Base](docs/setup/knowledge_base.md) | Cara menjalankan indexing KB |
| [Menambah Materi](docs/guides/adding_materials.md) | Cara menambah dokumen baru ke KB |
| [Troubleshooting](docs/guides/troubleshooting.md) | Common issues dan solusinya |

---

## 📄 License

Personal project — not licensed for redistribution.