# Konfigurasi Environment Variables

Semua konfigurasi aplikasi disimpan di file `.env` di root project. File ini **tidak boleh di-commit** ke Git.

---

## Daftar Variabel

### `ANTHROPIC_API_KEY`

**Wajib.** API key untuk memanggil Claude Haiku dan Claude Sonnet.

```env
ANTHROPIC_API_KEY=sk-ant-api03-xxxx...
```

Dipakai oleh: semua agent (Planner, Generator, Validator, Corrector, Evaluator, Assessor, Analytics).

Cara mendapatkan: [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)

---

### `GOOGLE_APPLICATION_CREDENTIALS`

**Wajib.** Path ke file JSON service account Google Cloud.

```env
GOOGLE_APPLICATION_CREDENTIALS=./gcp-service-account.json
```

Dipakai oleh:
- `modules/audio/stt.py` — Google Speech-to-Text untuk transkrip suara user
- `modules/audio/tts.py` — Google Text-to-Speech untuk generate audio soal

Nilai default: `./gcp-service-account.json` (di root project)

---

### `DATABASE_PATH`

**Wajib.** Path ke file SQLite database.

```env
DATABASE_PATH=./english_agent.db
```

Database dibuat otomatis saat pertama kali aplikasi dijalankan. Berisi 14 tabel untuk semua mode latihan.

Nilai default: `./english_agent.db`

> Jangan hapus file ini — berisi seluruh history latihan dan progress kamu.

---

### `CHROMA_DB_PATH`

**Wajib.** Path ke folder ChromaDB untuk RAG (Retrieval-Augmented Generation).

```env
CHROMA_DB_PATH=./vector_store/chroma_db
```

Folder ini dibuat otomatis saat pertama kali menjalankan `scripts/index_knowledge_base.py`. Berisi embedding materi grammar.

Nilai default: `./vector_store/chroma_db`

---

## File `.env.example`

Template `.env.example` sudah tersedia di root project. Gunakan sebagai referensi:

```bash
cp .env.example .env
```

---

## Catatan Keamanan

- File `.env` sudah masuk `.gitignore` — tidak akan ter-commit secara tidak sengaja
- File `gcp-service-account.json` juga sudah masuk `.gitignore`
- Jangan pernah membagikan kedua file ini ke siapapun
- Jika API key ter-expose (misalnya ter-commit ke GitHub), segera revoke dan buat key baru