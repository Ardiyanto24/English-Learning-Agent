# English Learning AI Agent

Aplikasi personal berbasis AI untuk persiapan **TOEFL ITP** yang fully automated — dari generate materi, latihan, evaluasi, hingga analitik performa.

## Fitur Utama

- 📚 **Vocab Agent** — Latihan kosakata dengan spaced repetition
- 📝 **Quiz Agent** — Latihan grammar 47 topik dengan feedback 4 lapisan
- 🎙️ **Speaking Agent** — 3 sub-mode: Prompted Response, Conversation Practice, Oral Presentation
- 🎯 **TOEFL Simulator** — Simulasi penuh ITP dengan estimasi skor 310–677

## Tech Stack

- **Frontend**: Streamlit
- **LLM**: Claude Haiku & Claude Sonnet (Anthropic)
- **Orchestration**: LangGraph + LangChain
- **Database**: SQLite + ChromaDB
- **Audio**: OpenAI Whisper (STT) + OpenAI TTS

## Quick Start

```bash
# 1. Clone repository
git clone https://github.com/YOUR_USERNAME/english_learning_agent.git
cd english_learning_agent

# 2. Setup environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Konfigurasi API keys
cp .env.example .env
# Edit .env dan isi API keys

# 4. Jalankan aplikasi
streamlit run app.py
```

## Dokumentasi

Lihat folder `docs/` untuk dokumentasi lengkap.
