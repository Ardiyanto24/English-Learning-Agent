# Troubleshooting

Panduan untuk mengatasi masalah umum yang mungkin muncul.

---

## Masalah Instalasi

### `pip install` gagal dengan error dependency conflict

```
ERROR: pip's dependency resolver does not currently take into account all the packages that are installed.
```

**Solusi:** Install di virtual environment yang bersih.

```bash
# Hapus env lama
rm -rf myvenv/

# Buat env baru
python -m venv myvenv
myvenv\Scripts\activate   # Windows
pip install -r requirements.txt
```

---

### `ModuleNotFoundError` saat menjalankan aplikasi

```
ModuleNotFoundError: No module named 'anthropic'
```

**Solusi:** Pastikan virtual environment sudah aktif sebelum menjalankan aplikasi.

```bash
# Windows
myvenv\Scripts\activate

# macOS / Linux
source myvenv/bin/activate

# Verifikasi
pip list | grep anthropic
```

---

## Masalah API

### `AuthenticationError: Invalid API Key`

**Penyebab:** `ANTHROPIC_API_KEY` di file `.env` salah atau kosong.

**Solusi:**
1. Cek isi file `.env` — pastikan key tidak ada spasi atau karakter tersembunyi
2. Verifikasi key masih aktif di [console.anthropic.com](https://console.anthropic.com/settings/keys)
3. Jalankan `python verify_setup.py` untuk test koneksi

---

### Google STT/TTS error: `DefaultCredentialsError`

```
google.auth.exceptions.DefaultCredentialsError: Could not automatically determine credentials
```

**Penyebab:** `GOOGLE_APPLICATION_CREDENTIALS` tidak diset atau file JSON tidak ditemukan.

**Solusi:**
1. Pastikan `gcp-service-account.json` ada di root project
2. Pastikan `.env` berisi: `GOOGLE_APPLICATION_CREDENTIALS=./gcp-service-account.json`
3. Jalankan `python verify_setup.py` untuk verifikasi

---

### `PermissionDenied` saat memanggil Google STT/TTS

```
google.api_core.exceptions.PermissionDenied: 403
```

**Penyebab:** Service account tidak punya role yang cukup.

**Solusi:** Tambahkan role yang diperlukan di Google Cloud Console:
- `Cloud Speech Client` (untuk STT)
- `Cloud Speech Editor` (untuk TTS)

---

## Masalah Database

### `OperationalError: database is locked`

**Penyebab:** Ada proses lain yang sedang mengakses database.

**Solusi:**
1. Tutup semua instance aplikasi yang sedang berjalan
2. Restart aplikasi

Jika masalah berlanjut, cek apakah WAL mode aktif:

```bash
python -c "
from database.connection import get_connection
conn = get_connection()
result = conn.execute('PRAGMA journal_mode').fetchone()
print('Journal mode:', result[0])   # harus 'wal'
conn.close()
"
```

---

### Database corrupt atau data tidak konsisten

**Solusi:** Reset database (semua data akan hilang).

```bash
python scripts/reset_database.py
```

> ⚠️ Peringatan: perintah ini menghapus semua history latihan.

---

## Masalah RAG / Knowledge Base

### Soal quiz tidak relevan dengan topik yang dipilih

**Penyebab:** Knowledge base belum di-index atau index sudah lama.

**Solusi:**

```bash
python scripts/index_knowledge_base.py --reset
```

---

### `CollectionNotFoundError` dari ChromaDB

```
chromadb.errors.InvalidCollectionException: Collection grammar_knowledge_base does not exist.
```

**Penyebab:** Knowledge base belum pernah di-index.

**Solusi:**

```bash
python scripts/index_knowledge_base.py
```

---

## Masalah Audio (Speaking Agent)

### STT selalu return `None` / fallback ke text input

**Kemungkinan penyebab:**
1. Microphone tidak terdeteksi
2. File audio kosong atau terlalu pendek
3. Google STT API error

**Solusi:**
1. Pastikan microphone berfungsi dan tidak di-mute di OS
2. Coba rekam dengan durasi lebih panjang (minimal 2 detik)
3. Cek log error di terminal untuk detail lebih lanjut

---

### TTS tidak menghasilkan audio

**Penyebab:** Google TTS gagal atau `generate_speech()` return `None`.

**Solusi:** Aplikasi sudah punya fallback — teks prompt akan ditampilkan saja. Untuk debug:

```bash
python -c "
from modules.audio.tts import generate_speech
audio = generate_speech('Hello test')
print('TTS OK, bytes:', len(audio) if audio else 'FAILED')
"
```

---

## Masalah Streamlit

### Port 8501 sudah dipakai

```
OSError: [Errno 98] Address already in use
```

**Solusi:** Jalankan di port berbeda:

```bash
streamlit run app.py --server.port 8502
```

---

### Halaman tidak update setelah perubahan kode

**Solusi:** Klik tombol **Rerun** di pojok kanan atas aplikasi Streamlit, atau tekan `R` di keyboard.

---

## Tidak Ada di Sini?

Jika masalah kamu tidak ada di halaman ini, cek:

1. Log error di terminal tempat `streamlit run app.py` dijalankan
2. File log di folder `logs/` jika ada
3. Jalankan `python verify_setup.py` untuk diagnostik awal