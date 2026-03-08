"""
modules/rag/retriever.py
------------------------
Retriever untuk mencari dokumen grammar yang relevan dari ChromaDB.

Embedding provider harus SAMA dengan yang dipakai saat indexing.
Ubah EMBEDDING_PROVIDER di sini jika switch ke OpenAI saat production.
"""

import os

import chromadb
from dotenv import load_dotenv

load_dotenv()

# ===================================================
# KONFIGURASI — harus sama dengan indexer.py
# ===================================================
EMBEDDING_PROVIDER = "local"  # "local" | "openai"

CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", "./vector_store/chroma_db")
COLLECTION_NAME = "grammar_knowledge_base"

THRESHOLD_STRICT = 0.75
THRESHOLD_RELAXED = 0.60
TOP_K = 3

# Singleton untuk efisiensi — tidak buka koneksi berulang
_chroma_client = None
_collection = None
_embed_fn = None


def _get_embed_fn():
    """Lazy-load embedding function (singleton)."""
    global _embed_fn
    if _embed_fn is None:
        if EMBEDDING_PROVIDER == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

            def embed_openai(text: str) -> list[float]:
                response = client.embeddings.create(
                    model="text-embedding-3-small",
                    input=[text],
                )
                return response.data[0].embedding

            _embed_fn = embed_openai

        else:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer("all-MiniLM-L6-v2")

            def embed_local(text: str) -> list[float]:
                return model.encode([text])[0].tolist()

            _embed_fn = embed_local

    return _embed_fn


def _get_collection():
    """Lazy-load ChromaDB collection (singleton)."""
    global _chroma_client, _collection
    if _collection is None:
        _chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        _collection = _chroma_client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _distance_to_similarity(distance: float) -> float:
    """Konversi cosine distance ke similarity. similarity = 1 - distance."""
    return 1.0 - distance


def retrieve(query: str, topic: str, top_k: int = TOP_K) -> dict:
    """
    Retrieve chunks grammar yang relevan dengan query.

    Args:
        query : Query string (nama topik atau pertanyaan grammar)
        topic : Nama topik sebagai konteks tambahan
        top_k : Jumlah maksimal chunks yang dikembalikan

    Returns:
        dict: {
            "chunks"         : [{"text", "metadata", "similarity"}],
            "source"         : "chromadb" | "chromadb_relaxed" | "fallback",
            "threshold_used" : float,
            "query_used"     : str,
        }
    """
    collection = _get_collection()

    if collection.count() == 0:
        return _make_fallback(topic, reason="collection_empty")

    enriched_query = f"{topic}: {query}"

    try:
        embed_fn = _get_embed_fn()
        query_embedding = embed_fn(enriched_query)
    except Exception as e:
        return _make_fallback(topic, reason=f"embedding_failed: {e}")

    # Query ChromaDB
    n_results = min(top_k * 2, collection.count())
    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as e:
        return _make_fallback(topic, reason=f"query_failed: {e}")

    # Tahap 1: threshold strict (0.75)
    filtered = _filter_by_threshold(results, THRESHOLD_STRICT)
    if filtered:
        return {
            "chunks": filtered[:top_k],
            "source": "chromadb",
            "threshold_used": THRESHOLD_STRICT,
            "query_used": enriched_query,
        }

    # Tahap 2: relax threshold (0.60)
    filtered_relaxed = _filter_by_threshold(results, THRESHOLD_RELAXED)
    if filtered_relaxed:
        return {
            "chunks": filtered_relaxed[:top_k],
            "source": "chromadb_relaxed",
            "threshold_used": THRESHOLD_RELAXED,
            "query_used": enriched_query,
        }

    # Tahap 3: fallback
    return _make_fallback(topic, reason="below_threshold")


def _filter_by_threshold(results: dict, threshold: float) -> list[dict]:
    """Filter hasil ChromaDB berdasarkan similarity threshold."""
    if not results or not results.get("documents") or not results["documents"][0]:
        return []

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        similarity = _distance_to_similarity(dist)
        if similarity >= threshold:
            chunks.append(
                {
                    "text": doc,
                    "metadata": meta,
                    "similarity": round(similarity, 4),
                }
            )

    return sorted(chunks, key=lambda x: x["similarity"], reverse=True)


def _make_fallback(topic: str, reason: str = "unknown") -> dict:
    """Fallback context ketika retrieval gagal atau hasilnya kosong."""
    return {
        "chunks": [
            {
                "text": f"Grammar topic: {topic}",
                "metadata": {"topic": topic, "source": "fallback"},
                "similarity": 0.0,
            }
        ],
        "source": "fallback",
        "fallback_reason": reason,
        "query_used": topic,
    }


def format_context_for_prompt(retrieval_result: dict) -> str:
    """
    Format hasil retrieval menjadi string siap diinjeksi ke prompt LLM.

    Penggunaan:
        result = retrieve("conditional if clause", "Conditional Clauses Type 2")
        context_str = format_context_for_prompt(result)
        # Inject context_str ke dalam prompt generator/corrector
    """
    chunks = retrieval_result.get("chunks", [])
    source = retrieval_result.get("source", "unknown")

    if source == "fallback":
        topic = chunks[0]["metadata"].get("topic", "grammar topic")
        return f"[Reference Topic: {topic}]"

    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        meta = chunk.get("metadata", {})
        topic = meta.get("topic", "")
        section = meta.get("section_title", "")
        similarity = chunk.get("similarity", 0)
        header = (
            f"[Chunk {i} | Topic: {topic} | "
            f"Section: {section} | Relevance: {similarity:.2f}]"
        )
        context_parts.append(f"{header}\n{chunk['text']}")

    return "\n\n---\n\n".join(context_parts)
