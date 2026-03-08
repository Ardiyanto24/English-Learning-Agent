"""
database/repositories/session_repository.py
--------------------------------------------
Repository untuk tabel sessions.
Semua operasi CRUD tabel sessions ada di sini.
Agent tidak boleh menulis SQL langsung — gunakan fungsi-fungsi ini.
"""

from typing import Optional
from database.connection import get_db


def create_session(session_id: str, mode: str) -> dict:
    """
    Buat sesi baru dengan status 'active'.

    Args:
        session_id: UUID unik sesi (generate dari helpers.py)
        mode: 'vocab' | 'quiz' | 'speaking' | 'toefl'

    Returns:
        dict data sesi yang baru dibuat
    """
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, mode, status)
            VALUES (?, ?, 'active')
            """,
            (session_id, mode),
        )
    return get_session(session_id)


def get_session(session_id: str) -> Optional[dict]:
    """
    Ambil data sesi berdasarkan session_id.

    Returns:
        dict data sesi, atau None jika tidak ditemukan
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def update_session_status(
    session_id: str,
    status: str,
    is_adjusted: bool = False,
    is_flagged: bool = False,
    flag_reason: Optional[str] = None,
) -> bool:
    """
    Update status sesi.

    Args:
        session_id: UUID sesi
        status: 'active' | 'paused' | 'completed' | 'incomplete' | 'abandoned'
        is_adjusted: True jika Validator melakukan adjustment
        is_flagged: True jika ada anomali
        flag_reason: Penjelasan flag jika ada

    Returns:
        True jika berhasil, False jika session_id tidak ditemukan
    """
    with get_db() as conn:
        # Set completed_at jika status terminal
        if status in ("completed", "abandoned", "incomplete"):
            conn.execute(
                """
                UPDATE sessions
                SET status = ?,
                    is_adjusted = ?,
                    is_flagged = ?,
                    flag_reason = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (status, is_adjusted, is_flagged, flag_reason, session_id),
            )
        else:
            conn.execute(
                """
                UPDATE sessions
                SET status = ?,
                    is_adjusted = ?,
                    is_flagged = ?,
                    flag_reason = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (status, is_adjusted, is_flagged, flag_reason, session_id),
            )
        return conn.execute(
            "SELECT changes()"
        ).fetchone()[0] > 0


def get_sessions_by_mode(mode: str, limit: int = 10) -> list[dict]:
    """
    Ambil daftar sesi berdasarkan mode, diurutkan dari terbaru.

    Args:
        mode: 'vocab' | 'quiz' | 'speaking' | 'toefl'
        limit: maksimal jumlah sesi yang dikembalikan

    Returns:
        List of dict sesi
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sessions
            WHERE mode = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (mode, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def set_session_expiry(session_id: str, expires_at: str) -> bool:
    """
    Set waktu expiry sesi (khusus TOEFL pause).

    Args:
        session_id: UUID sesi
        expires_at: Timestamp string format 'YYYY-MM-DD HH:MM:SS'
    """
    with get_db() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET expires_at = ?, status = 'paused', updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (expires_at, session_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0