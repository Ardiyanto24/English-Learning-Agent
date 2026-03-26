# Knowledge Base — Panduan Indexing

Knowledge base adalah kumpulan materi grammar dalam format Markdown yang digunakan oleh Quiz Agent dan TOEFL Simulator via RAG (Retrieval-Augmented Generation).

---

## Struktur Knowledge Base

```
knowledge_base/
└── grammar/
    ├── present-tenses.md
    ├── past-tenses.md
    ├── future-tenses.md
    ├── verb-tense-consistency.md
    └── ... (47 topik total)
```

Setiap file `.md` mewakili satu topik grammar yang ada di `config/prerequisite_rules.json`.

---

## Cara Menjalankan Indexing

### Index normal (pertama kali atau update incremental)

```bash
python scripts/index_knowledge_base.py
```

### Reset dan index ulang (jika ada perubahan besar)

```bash
python scripts/index_knowledge_base.py --reset
```

Flag `--reset` menghapus seluruh data di ChromaDB dan mengindeks ulang dari awal. Gunakan ini jika ada banyak perubahan isi materi.

---

## Output yang Diharapkan

```
=======================================================
  📖 Knowledge Base Indexer
=======================================================
  Source : knowledge_base/grammar
  Mode   : Incremental (upsert)
=======================================================

  ✅ Indexing Selesai!
  Files indexed : 20
  Total chunks  : 180+
  In ChromaDB   : 180+
  Time elapsed  : 12.3s
=======================================================
```

---

## Verifikasi Hasil Indexing

Setelah indexing selesai, verifikasi bahwa retrieval bekerja dengan benar:

```bash
python -c "
from modules.rag.retriever import retrieve
result = retrieve('subject verb agreement', 'Subject-Verb Agreement')
print('Chunks retrieved:', len(result))
print('First chunk preview:', result[0]['text'][:100] if result else 'EMPTY')
"
```

Output yang diharapkan:

```
Chunks retrieved: 3
First chunk preview: Subject-verb agreement adalah...
```

---

## Kapan Harus Indexing Ulang

Jalankan indexing ulang setiap kali:

- Pertama kali setup project
- Menambah file materi baru ke `knowledge_base/grammar/`
- Mengubah isi file materi yang sudah ada
- Menghapus file materi
- Setelah reset ChromaDB

Lihat [menambah materi](../guides/adding_materials.md) untuk panduan menambah topik baru.

---

## Embedding Provider

Secara default, indexing menggunakan embedding **lokal** via `sentence-transformers` (gratis, tidak butuh API key tambahan).

Untuk beralih ke OpenAI embeddings (kualitas lebih tinggi, berbayar), edit `modules/rag/indexer.py`:

```python
EMBEDDING_PROVIDER = "openai"  # ganti dari "local"
```

Pastikan provider yang sama juga diset di `modules/rag/retriever.py` agar konsisten.