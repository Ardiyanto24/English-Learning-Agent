# Menambah Materi ke Knowledge Base

Panduan ini menjelaskan cara menambah topik grammar baru ke knowledge base agar bisa digunakan oleh Quiz Agent dan TOEFL Simulator.

---

## Langkah 1 — Buat File Materi

Buat file Markdown baru di folder `knowledge_base/grammar/`. Nama file menggunakan format `kebab-case`:

```
knowledge_base/grammar/nama-topik-baru.md
```

### Format yang Disarankan

```markdown
# Nama Topik Grammar

## Definisi

Penjelasan singkat tentang topik ini...

## Aturan Utama

1. Aturan pertama
2. Aturan kedua
3. ...

## Contoh Kalimat

**Benar:**
- She walks to school every day.

**Salah:**
- She walk to school every day.

## Common Mistakes

Kesalahan yang sering terjadi...

## Contoh Soal

Q: Choose the correct form...
A: ...
```

Semakin detail dan terstruktur isinya, semakin baik kualitas soal yang di-generate oleh LLM.

---

## Langkah 2 — Daftarkan Topik di Config

Buka `config/prerequisite_rules.json` dan tambahkan entry baru:

```json
{
  "Nama Topik Baru": {
    "requires": ["Topik Prerequisite"],
    "cluster": "Nama Cluster"
  }
}
```

**Penjelasan field:**

- `requires`: daftar topik yang harus dikuasai dulu sebelum topik ini bisa diakses. Kosongkan `[]` jika tidak ada prerequisite.
- `cluster`: nama kelompok topik yang berkaitan. Lihat `config/cluster_metadata.json` untuk daftar cluster yang sudah ada.

---

## Langkah 3 — Daftarkan di Cluster Metadata (opsional)

Jika topik baru masuk ke cluster yang sudah ada, tidak perlu mengubah apa-apa. Jika membuat cluster baru, buka `config/cluster_metadata.json` dan tambahkan:

```json
{
  "clusters": {
    "Nama Cluster Baru": {
      "topics": ["Topik A", "Topik B", "Nama Topik Baru"],
      "description": "Deskripsi singkat cluster ini"
    }
  }
}
```

---

## Langkah 4 — Jalankan Indexing Ulang

```bash
python scripts/index_knowledge_base.py
```

Gunakan flag `--reset` jika mengubah topik yang sudah ada:

```bash
python scripts/index_knowledge_base.py --reset
```

---

## Langkah 5 — Verifikasi

Test bahwa topik baru bisa di-retrieve dengan benar:

```bash
python -c "
from modules.rag.retriever import retrieve
result = retrieve('nama topik baru', 'Nama Topik Baru')
print('OK' if result else 'FAILED — chunk tidak ditemukan')
"
```

---

## Tips Menulis Materi yang Baik

- Gunakan **contoh kalimat yang konkret** — LLM akan menggunakannya sebagai referensi saat generate soal
- Sertakan **common mistakes** — membantu generate distractor yang plausible untuk soal multiple choice
- Tulis dalam **Bahasa Inggris** — konsisten dengan materi yang sudah ada
- Batasi panjang file sekitar **500–1000 kata** agar chunk tidak terlalu panjang atau pendek