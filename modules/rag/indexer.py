"""
modules/rag/indexer.py
----------------------
Indexer untuk knowledge base grammar.

Embedding provider:
- Development : sentence-transformers (lokal, gratis)
- Production  : OpenAI text-embedding-3-small (ganti EMBEDDING_PROVIDER)

Untuk switch ke OpenAI saat production:
  Ubah EMBEDDING_PROVIDER = "openai"
"""

import os
import re
from pathlib import Path
from typing import Optional

import chromadb
from dotenv import load_dotenv

load_dotenv()

# ===================================================
# KONFIGURASI — ubah ini untuk switch provider
# ===================================================
EMBEDDING_PROVIDER = "local"  # "local" | "openai"

GRAMMAR_KB_PATH = Path("knowledge_base/grammar")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./vector_store/chroma_db")
COLLECTION_NAME = "grammar_knowledge_base"

MAX_CHUNK_TOKENS = 500
OVERLAP_RATIO = 0.2


def _get_embedding_function():
    """
    Return embedding function sesuai provider yang dipilih.

    Local  : SentenceTransformer('all-MiniLM-L6-v2') — gratis, lokal
    OpenAI : text-embedding-3-small — berbayar, kualitas lebih tinggi
    """
    if EMBEDDING_PROVIDER == "openai":
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        def embed_openai(texts: list[str]) -> list[list[float]]:
            response = client.embeddings.create(
                model="text-embedding-3-small",
                input=texts,
            )
            return [item.embedding for item in response.data]

        return embed_openai

    else:
        # Local: sentence-transformers
        # Install: pip install sentence-transformers
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")

        def embed_local(texts: list[str]) -> list[list[float]]:
            embeddings = model.encode(texts, show_progress_bar=False)
            return embeddings.tolist()

        return embed_local


def estimate_tokens(text: str) -> int:
    """Estimasi jumlah token. Aturan kasar: 1 token ≈ 4 karakter."""
    return len(text) // 4


def split_by_heading(content: str, filename: str) -> list[dict]:
    """
    Split dokumen berdasarkan heading level 2 (##).
    Setiap section menjadi satu chunk dasar.
    """
    sections = re.split(r'\n(?=## )', content)
    chunks = []

    for section in sections:
        if not section.strip():
            continue
        lines = section.strip().split('\n')
        section_title = lines[0].lstrip('#').strip() if lines else "General"
        chunks.append({
            "text": section.strip(),
            "section_title": section_title,
            "filename": filename,
        })

    return chunks


def split_long_chunk(chunk: dict) -> list[dict]:
    """
    Jika chunk terlalu panjang, split dengan overlap 20%.
    """
    if estimate_tokens(chunk["text"]) <= MAX_CHUNK_TOKENS:
        return [chunk]

    paragraphs = [p.strip() for p in chunk["text"].split('\n\n') if p.strip()]
    result_chunks = []
    current_paragraphs = []
    current_tokens = 0
    overlap_size = max(1, int(len(paragraphs) * OVERLAP_RATIO))

    for para in paragraphs:
        para_tokens = estimate_tokens(para)

        if current_tokens + para_tokens > MAX_CHUNK_TOKENS and current_paragraphs:
            result_chunks.append({
                **chunk,
                "text": '\n\n'.join(current_paragraphs),
                "section_title": f"{chunk['section_title']} (part {len(result_chunks)+1})",
            })
            overlap_start = max(0, len(current_paragraphs) - overlap_size)
            current_paragraphs = current_paragraphs[overlap_start:]
            current_tokens = sum(estimate_tokens(p) for p in current_paragraphs)

        current_paragraphs.append(para)
        current_tokens += para_tokens

    if current_paragraphs:
        part_label = f" (part {len(result_chunks)+1})" if result_chunks else ""
        result_chunks.append({
            **chunk,
            "text": '\n\n'.join(current_paragraphs),
            "section_title": f"{chunk['section_title']}{part_label}",
        })

    return result_chunks if result_chunks else [chunk]


def parse_topic_from_file(filepath: Path) -> tuple[str, str]:
    """
    Ekstrak topic name dan cluster dari path file.
    Cluster diambil dari nama folder parent.

    Struktur: knowledge_base/grammar/<ClusterName>/<topic>.md
    Contoh  : grammar/Tenses/perfect_tenses.md → cluster = "Tenses"
    """
    cluster = filepath.parent.name
    if cluster == "grammar":
        cluster = "General"

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
        topic = first_line.lstrip('#').strip() if first_line.startswith('#') else filepath.stem
    except Exception:
        topic = filepath.stem.replace('_', ' ').title()

    return topic, cluster


def index_knowledge_base(
    kb_path: Optional[Path] = None,
    chroma_path: Optional[str] = None,
    reset: bool = False,
) -> dict:
    """
    Main function: index semua dokumen grammar ke ChromaDB.

    Args:
        kb_path    : Path ke folder grammar
        chroma_path: Path ke ChromaDB
        reset      : Jika True, hapus collection lama sebelum index ulang
    """
    kb_path = kb_path or GRAMMAR_KB_PATH
    chroma_path = chroma_path or CHROMA_DB_PATH

    chroma_client = chromadb.PersistentClient(path=chroma_path)

    if reset:
        try:
            chroma_client.delete_collection(COLLECTION_NAME)
            print(f"  🗑️  Collection '{COLLECTION_NAME}' dihapus")
        except Exception:
            pass

    collection = chroma_client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # Baca semua .md secara rekursif (termasuk subfolder cluster)
    md_files = sorted(kb_path.rglob("*.md"))
    if not md_files:
        return {"total_files": 0, "total_chunks": 0, "status": "no_files"}

    total_chunks = 0
    all_ids = []
    all_texts = []
    all_metadatas = []

    print(f"  📚 Membaca {len(md_files)} file grammar...")

    for md_file in md_files:
        with open(md_file, 'r', encoding='utf-8') as f:
            content = f.read()

        topic_name, cluster = parse_topic_from_file(md_file)
        base_chunks = split_by_heading(content, md_file.name)

        final_chunks = []
        for base_chunk in base_chunks:
            final_chunks.extend(split_long_chunk(base_chunk))

        for i, chunk in enumerate(final_chunks):
            chunk_id = f"{md_file.stem}_{i:03d}"
            all_ids.append(chunk_id)
            all_texts.append(chunk["text"])
            all_metadatas.append({
                "filename": md_file.name,
                "topic": topic_name,
                "cluster": cluster,
                "section_title": chunk["section_title"],
                "chunk_index": i,
            })

        total_chunks += len(final_chunks)
        print(f"    ✓ {md_file.name}: {len(final_chunks)} chunks")

    # Embed semua chunks
    print(f"\n  🔢 Embedding {total_chunks} chunks "
          f"(provider: {EMBEDDING_PROVIDER})...")

    embed_fn = _get_embedding_function()
    BATCH_SIZE = 64
    all_embeddings = []

    for i in range(0, len(all_texts), BATCH_SIZE):
        batch = all_texts[i:i + BATCH_SIZE]
        embeddings = embed_fn(batch)
        all_embeddings.extend(embeddings)
        batch_num = i // BATCH_SIZE + 1
        total_batches = -(-len(all_texts) // BATCH_SIZE)
        print(f"    ✓ Batch {batch_num}/{total_batches} selesai")

    # Simpan ke ChromaDB
    print(f"\n  💾 Menyimpan ke ChromaDB...")
    collection.upsert(
        ids=all_ids,
        embeddings=all_embeddings,
        documents=all_texts,
        metadatas=all_metadatas,
    )

    final_count = collection.count()
    print(f"  ✅ Total chunks tersimpan: {final_count}")

    return {
        "total_files": len(md_files),
        "total_chunks": total_chunks,
        "chromadb_count": final_count,
        "status": "success",
    }