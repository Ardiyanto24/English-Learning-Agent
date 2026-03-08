"""
database/connection.py
----------------------
Modul koneksi SQLite dengan konfigurasi WAL mode.

WAL (Write-Ahead Logging) memungkinkan pembacaan database
berjalan bersamaan dengan penulisan — penting untuk Streamlit
yang bisa memiliki beberapa operasi simultan.
"""

import sqlite3
import os
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

# Path database diambil dari .env, default ke root project
DATABASE_PATH = os.getenv("DATABASE_PATH", "./english_agent.db")


def get_connection() -> sqlite3.Connection:
    """
    Buat dan return koneksi SQLite dengan konfigurasi optimal.

    Konfigurasi:
    - WAL mode: read dan write bisa berjalan bersamaan
    - row_factory: hasil query dikembalikan sebagai dict, bukan tuple
    - timeout: tunggu 30 detik jika DB sedang di-lock sebelum error
    """
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)

    # row_factory membuat kolom bisa diakses by name: row["session_id"]
    # bukan by index: row[0]
    conn.row_factory = sqlite3.Row

    # Aktifkan WAL mode
    conn.execute("PRAGMA journal_mode=WAL")

    # Foreign key enforcement — SQLite tidak enforce FK secara default
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


@contextmanager
def get_db():
    """
    Context manager untuk koneksi database.

    Penggunaan:
        with get_db() as conn:
            conn.execute("SELECT ...")

    Otomatis commit jika tidak ada error, rollback jika ada exception,
    dan selalu close koneksi setelah selesai.
    """
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """
    Inisialisasi database — buat semua tabel jika belum ada.
    Dipanggil saat aplikasi pertama kali dijalankan.
    """
    from database.models import CREATE_ALL_TABLES, CREATE_ALL_INDEXES

    with get_db() as conn:
        for statement in CREATE_ALL_TABLES:
            conn.execute(statement)
        for statement in CREATE_ALL_INDEXES:
            conn.execute(statement)