"""
scripts/index_knowledge_base.py
--------------------------------
Script runner untuk indexing knowledge base grammar ke ChromaDB.

Penggunaan:
    python scripts/index_knowledge_base.py           # Index normal
    python scripts/index_knowledge_base.py --reset   # Hapus dan index ulang

Jalankan script ini:
- Pertama kali setup project
- Setiap kali ada perubahan atau penambahan file di knowledge_base/grammar/
"""

import sys
import os
import time

# Tambahkan root project ke Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.rag.indexer import index_knowledge_base, GRAMMAR_KB_PATH


def main():
    reset = "--reset" in sys.argv

    print("=" * 55)
    print("  📖 Knowledge Base Indexer")
    print("=" * 55)
    print(f"  Source : {GRAMMAR_KB_PATH}")
    print(f"  Mode   : {'RESET + Re-index' if reset else 'Incremental (upsert)'}")
    print("=" * 55)
    print()

    start_time = time.time()

    try:
        result = index_knowledge_base(reset=reset)
    except Exception as e:
        print(f"\n❌ Indexing gagal: {e}")
        print("\nPastikan:")
        print("  1. OPENAI_API_KEY sudah diset di file .env")
        print("  2. Koneksi internet tersedia")
        print("  3. Folder knowledge_base/grammar/ berisi file .md")
        sys.exit(1)

    elapsed = time.time() - start_time

    print()
    print("=" * 55)
    print("  ✅ Indexing Selesai!")
    print("=" * 55)
    print(f"  Files indexed : {result['total_files']}")
    print(f"  Total chunks  : {result['total_chunks']}")
    print(f"  In ChromaDB   : {result.get('chromadb_count', 'N/A')}")
    print(f"  Time elapsed  : {elapsed:.1f}s")
    print("=" * 55)
    print()
    print("Langkah selanjutnya:")
    print("  Jalankan test retriever untuk verifikasi hasil indexing:")
    print("  python -c \"from modules.rag.retriever import retrieve; print(retrieve('conditional clause', 'Conditional Clauses Type 2 and 3'))\"")
    print()


if __name__ == "__main__":
    main()