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
        return conn.execute("SELECT changes()").fetchone()[0] > 0


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


def pause_toefl_session(
    session_id: str, current_section: int, paused_at: str, expires_at: str
) -> bool:
    """
    Simpan state pause sesi TOEFL.

    Dipanggil oleh toefl_session_manager setelah validasi bahwa
    pause terjadi antar section (bukan di tengah section).

    Args:
        session_id     : UUID sesi
        current_section: Nomor section yang baru selesai (1=Listening, 2=Structure)
                         Section berikutnya akan dilanjutkan saat resume
        paused_at      : Timestamp saat pause (format 'YYYY-MM-DD HH:MM:SS')
        expires_at     : paused_at + 7 hari (format 'YYYY-MM-DD HH:MM:SS')

    Returns:
        True jika berhasil, False jika session_id tidak ditemukan
    """
    with get_db() as conn:
        conn.execute(
            """
            UPDATE sessions
            SET status = 'paused',
                expires_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (expires_at, session_id),
        )
        conn.execute(
            """
            UPDATE toefl_sessions
            SET current_section = ?,
                paused_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (current_section, paused_at, session_id),
        )
        return conn.execute("SELECT changes()").fetchone()[0] > 0


def check_and_resume_toefl_session(session_id: str, now: str) -> "Optional[dict]":
    """
    Cek validitas sesi TOEFL yang di-pause, lalu resume jika masih valid.

    Flow:
    1. Ambil data sesi dari DB
    2. Jika status bukan 'paused' -> return None
    3. Jika expires_at < now -> mark 'abandoned', return None
    4. Jika masih valid -> return dict state lengkap untuk dilanjutkan

    Args:
        session_id: UUID sesi yang ingin di-resume
        now       : Timestamp sekarang (format 'YYYY-MM-DD HH:MM:SS')
                    Dioper sebagai parameter agar mudah di-test dengan nilai palsu

    Returns:
        dict state sesi jika valid untuk di-resume, None jika tidak bisa
    """
    with get_db() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if not session:
            return None

        session = dict(session)

        if session.get("status") != "paused":
            return None

        expires_at = session.get("expires_at")

        if expires_at and now > expires_at:
            conn.execute(
                """
                UPDATE sessions
                SET status = 'abandoned',
                    completed_at = CURRENT_TIMESTAMP,
                    flag_reason = 'session_expired',
                    is_flagged = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = ?
                """,
                (session_id,),
            )
            return None

        toefl_state = conn.execute(
            "SELECT * FROM toefl_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if not toefl_state:
            return None

        toefl_state = dict(toefl_state)

        answered_questions = conn.execute(
            """
            SELECT * FROM toefl_questions
            WHERE session_id = ? AND user_answer IS NOT NULL
            ORDER BY section ASC, question_number ASC
            """,
            (session_id,),
        ).fetchall()

    return {
        "session_id": session_id,
        "mode": toefl_state.get("mode"),
        "current_section": toefl_state.get("current_section"),
        "expires_at": expires_at,
        "answered_questions": [dict(q) for q in answered_questions],
        "listening_raw": toefl_state.get("listening_raw"),
        "structure_raw": toefl_state.get("structure_raw"),
        "reading_raw": toefl_state.get("reading_raw"),
    }


def get_abandoned_sessions(mode: str = "toefl") -> list:
    """
    Ambil semua sesi yang di-mark abandoned.
    Digunakan Analytics Agent untuk eksklusi dari kalkulasi.

    Args:
        mode: Filter berdasarkan mode (default 'toefl')

    Returns:
        List of dict sesi yang abandoned
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM sessions
            WHERE mode = ? AND status = 'abandoned'
            ORDER BY updated_at DESC
            """,
            (mode,),
        ).fetchall()
    return [dict(row) for row in rows]
