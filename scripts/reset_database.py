"""
scripts/reset_database.py
--------------------------
Script untuk drop semua tabel dan recreate dari awal.
DEVELOPMENT ONLY — jangan jalankan di production!

Penggunaan:
    python scripts/reset_database.py
    python scripts/reset_database.py --confirm   # skip konfirmasi
"""

import sys
import os

# Tambahkan root project ke Python path agar import berjalan
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import get_db, init_database
from database.models import CREATE_ALL_TABLES


def drop_all_tables():
    """Drop semua tabel dalam urutan terbalik (child dulu, baru parent)."""
    # Urutan drop: child tables dulu agar tidak melanggar foreign key
    tables = [
        "error_logs",
        "analytics_snapshots",
        "toefl_questions",
        "toefl_sessions",
        "speaking_exchanges",
        "speaking_sessions",
        "quiz_topic_tracking",
        "quiz_questions",
        "quiz_sessions",
        "vocab_word_tracking",
        "vocab_questions",
        "vocab_sessions",
        "sessions",
        "users",
    ]

    with get_db() as conn:
        # Matikan FK sementara untuk memudahkan drop
        conn.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
            print(f"  ✓ Dropped: {table}")
        conn.execute("PRAGMA foreign_keys=ON")


def main():
    # Cek argumen --confirm untuk skip prompt
    skip_confirm = "--confirm" in sys.argv

    if not skip_confirm:
        print("⚠️  PERINGATAN: Script ini akan menghapus SEMUA data di database!")
        print(f"   Database: {os.getenv('DATABASE_PATH', './english_agent.db')}")
        confirm = input("\nKetik 'yes' untuk konfirmasi: ").strip().lower()

        if confirm != "yes":
            print("❌ Dibatalkan.")
            sys.exit(0)

    print("\n🗑️  Menghapus semua tabel...")
    drop_all_tables()

    print("\n🔨 Membuat ulang semua tabel dan index...")
    init_database()

    print("\n✅ Database berhasil di-reset!")
    print("   Semua tabel sudah dibuat ulang dari awal.\n")


if __name__ == "__main__":
    main()
