# Panduan Instalasi Detail

Dokumen ini menjelaskan instalasi lengkap English Learning AI Agent dari nol.

---

## Persyaratan Sistem

| Komponen | Minimum | Rekomendasi |
|---|---|---|
| Python | 3.10 | 3.11+ |
| RAM | 4 GB | 8 GB |
| Disk | 2 GB | 5 GB |
| OS | Windows 10 / macOS 12 / Ubuntu 20.04 | — |

---

## Langkah 1 — Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/english_learning_agent.git
cd english_learning_agent
```

---

## Langkah 2 — Setup Python Environment

```bash
# Buat virtual environment
python -m venv myvenv

# Aktifkan — Windows
myvenv\Scripts\activate

# Aktifkan — macOS / Linux
source myvenv/bin/activate

# Verifikasi Python version
python --version   # harus 3.10+
```

---

## Langkah 3 — Install Dependencies

```bash
pip install -r requirements.txt
```

Proses ini menginstall semua library yang dibutuhkan. Pastikan koneksi internet tersedia.

Untuk development (opsional):

```bash
pip install -r requirements-dev.txt
```

---

## Langkah 4 — Setup API Keys

### 4.1 Anthropic API Key

1. Buka [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
2. Buat API key baru
3. Simpan key-nya — hanya ditampilkan sekali

### 4.2 Google Cloud Service Account

Diperlukan untuk Speech-to-Text (STT) dan Text-to-Speech (TTS).

1. Buka [Google Cloud Console](https://console.cloud.google.com)
2. Buat project baru **tanpa organisasi**
3. Aktifkan dua API berikut di **APIs & Services → Library**:
   - Cloud Speech-to-Text API
   - Cloud Text-to-Speech API
4. Buka **IAM & Admin → Service Accounts → Create Service Account**
5. Tambahkan dua role:
   - `Cloud Speech Client`
   - `Cloud Speech Editor`
6. Download JSON key → rename menjadi `gcp-service-account.json`
7. Letakkan file di root folder project

---

## Langkah 5 — Konfigurasi File `.env`

```bash
cp .env.example .env
```

Buka `.env` dan isi semua nilai:

```env
ANTHROPIC_API_KEY=sk-ant-xxxx...
GOOGLE_APPLICATION_CREDENTIALS=./gcp-service-account.json
DATABASE_PATH=./english_agent.db
CHROMA_DB_PATH=./vector_store/chroma_db
```

Lihat [konfigurasi.md](configuration.md) untuk penjelasan detail setiap variabel.

---

## Langkah 6 — Index Knowledge Base

Knowledge base berisi materi grammar yang digunakan oleh Quiz Agent dan TOEFL Simulator via RAG.

```bash
python scripts/index_knowledge_base.py
```

Output yang diharapkan:

```
=======================================================
  📖 Knowledge Base Indexer
=======================================================
  ✅ Indexing Selesai!
  Files indexed : 20
  Total chunks  : 180+
=======================================================
```

Proses ini hanya perlu dijalankan **sekali** saat pertama kali setup, atau setiap kali ada perubahan materi di `knowledge_base/grammar/`.

---

## Langkah 7 — Verifikasi Instalasi

```bash
python verify_setup.py
```

Output yang diharapkan:

```
[TEST 1] Google Credentials... OK
[TEST 2] Google Text-to-Speech... OK
[TEST 3] Google Speech-to-Text... OK
[TEST 4] Anthropic API... OK
```

---

## Langkah 8 — Jalankan Aplikasi

```bash
streamlit run app.py
```

Buka browser di `http://localhost:8501`. Aplikasi akan menampilkan halaman onboarding untuk setup profil pertama kali.

---

## Instalasi dengan Docker (Alternatif)

```bash
# Build image
docker build -f docker/Dockerfile -t english-learning-agent .

# Jalankan
docker-compose -f docker/docker-compose.yml up
```

Pastikan file `.env` dan `gcp-service-account.json` sudah ada di root project sebelum menjalankan Docker.