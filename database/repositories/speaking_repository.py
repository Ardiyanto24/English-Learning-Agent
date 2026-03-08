"""
database/repositories/speaking_repository.py
---------------------------------------------
Repository untuk tabel speaking_sessions dan speaking_exchanges.
"""

from typing import Optional
from database.connection import get_db


def save_speaking_session(session_id: str, sub_mode: str, topic: str,
                          category: str) -> bool:
    """Simpan metadata sesi speaking baru."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO speaking_sessions
                (session_id, sub_mode, topic, category)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, sub_mode, topic, category),
        )
    return True


def save_exchange(session_id: str, exchange_number: int, agent_prompt: str,
                  user_transcript: Optional[str] = None,
                  is_followup: bool = False,
                  assessor_decision: Optional[str] = None) -> int:
    """
    Simpan satu exchange dalam percakapan (incremental save).
    Dipanggil setiap kali ada satu putaran tanya-jawab.

    Returns:
        id row exchange yang baru dibuat
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO speaking_exchanges
                (session_id, exchange_number, agent_prompt,
                 user_transcript, is_followup, assessor_decision)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, exchange_number, agent_prompt,
             user_transcript, is_followup, assessor_decision),
        )
    return cursor.lastrowid


def update_exchange_transcript(exchange_id: int, user_transcript: str,
                                assessor_decision: Optional[str] = None) -> bool:
    """Update transkrip user dan keputusan assessor setelah user menjawab."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE speaking_exchanges
            SET user_transcript = ?, assessor_decision = ?
            WHERE id = ?
            """,
            (user_transcript, assessor_decision, exchange_id),
        )
    return True


def update_speaking_scores(session_id: str, total_exchanges: int,
                           full_transcript: str, grammar_score: float,
                           relevance_score: float, final_score: float,
                           vocabulary_score: Optional[float] = None,
                           structure_score: Optional[float] = None,
                           duration_seconds: Optional[int] = None,
                           is_graded: bool = True) -> bool:
    """
    Update skor akhir sesi speaking setelah evaluasi selesai.
    vocabulary_score dan structure_score hanya untuk Oral Presentation.
    """
    with get_db() as conn:
        conn.execute(
            """
            UPDATE speaking_sessions
            SET total_exchanges = ?, full_transcript = ?,
                grammar_score = ?, relevance_score = ?,
                vocabulary_score = ?, structure_score = ?,
                final_score = ?, duration_seconds = ?,
                is_graded = ?
            WHERE session_id = ?
            """,
            (total_exchanges, full_transcript, grammar_score, relevance_score,
             vocabulary_score, structure_score, final_score,
             duration_seconds, is_graded, session_id),
        )
    return True


def get_speaking_session(session_id: str) -> Optional[dict]:
    """Ambil data sesi speaking beserta semua exchange-nya."""
    with get_db() as conn:
        session = conn.execute(
            "SELECT * FROM speaking_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if not session:
            return None

        exchanges = conn.execute(
            """
            SELECT * FROM speaking_exchanges
            WHERE session_id = ?
            ORDER BY exchange_number ASC
            """,
            (session_id,),
        ).fetchall()

    return {
        **dict(session),
        "exchanges": [dict(e) for e in exchanges],
    }


def get_recent_speaking_sessions(sub_mode: Optional[str] = None,
                                 limit: int = 10) -> list[dict]:
    """Ambil sesi speaking terbaru, opsional filter by sub_mode."""
    with get_db() as conn:
        if sub_mode:
            rows = conn.execute(
                """
                SELECT * FROM speaking_sessions
                WHERE sub_mode = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (sub_mode, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM speaking_sessions
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]