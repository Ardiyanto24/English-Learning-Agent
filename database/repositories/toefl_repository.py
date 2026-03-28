"""
database/repositories/toefl_repository.py
------------------------------------------
Repository untuk tabel toefl_sessions dan toefl_questions.
"""

from typing import Optional
from database.connection import get_db


def save_toefl_session(session_id: str, mode: str) -> bool:
    """Simpan metadata sesi TOEFL Simulator baru."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO toefl_sessions (session_id, mode)
            VALUES (?, ?)
            """,
            (session_id, mode),
        )
    return True


def save_toefl_question(
    session_id: str,
    section: str,
    part: str,
    question_number: int,
    question_text: str,
    options: str,
    correct_answer: str,
    difficulty: str,
    passage_text: Optional[str] = None,
    audio_script: Optional[str] = None,
) -> int:
    """
    Simpan satu soal TOEFL (incremental save, sebelum user menjawab).

    Args:
        options: JSON string array 4 pilihan, contoh: '["A","B","C","D"]'

    Returns:
        id row soal yang baru dibuat
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO toefl_questions
                (session_id, section, part, question_number, question_text,
                 options, correct_answer, difficulty, passage_text, audio_script)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, section, part, question_number, question_text, options, correct_answer, difficulty, passage_text, audio_script),
        )
    return cursor.lastrowid


def update_toefl_answer(question_id: int, user_answer: str, is_correct: bool) -> bool:
    """Update jawaban user untuk sebuah soal TOEFL."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE toefl_questions
            SET user_answer = ?, is_correct = ?
            WHERE id = ?
            """,
            (user_answer, is_correct, question_id),
        )
    return True


def update_toefl_scores(
    session_id: str,
    listening_raw: int,
    structure_raw: int,
    reading_raw: int,
    listening_extrapolated: int,
    structure_extrapolated: int,
    reading_extrapolated: int,
    listening_scaled: int,
    structure_scaled: int,
    reading_scaled: int,
    estimated_score: int,
) -> bool:
    """Update semua skor setelah simulasi selesai."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE toefl_sessions
            SET listening_raw = ?, structure_raw = ?, reading_raw = ?,
                listening_extrapolated = ?, structure_extrapolated = ?,
                reading_extrapolated = ?,
                listening_scaled = ?, structure_scaled = ?,
                reading_scaled = ?,
                estimated_score = ?, score_status = 'completed',
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (
                listening_raw,
                structure_raw,
                reading_raw,
                listening_extrapolated,
                structure_extrapolated,
                reading_extrapolated,
                listening_scaled,
                structure_scaled,
                reading_scaled,
                estimated_score,
                session_id,
            ),
        )
    return True


def update_current_section(session_id: str, current_section: int) -> bool:
    """Update section yang sedang dikerjakan (untuk pause/resume)."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE toefl_sessions
            SET current_section = ?, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
            """,
            (current_section, session_id),
        )
    return True


def get_toefl_session(session_id: str) -> Optional[dict]:
    """Ambil data sesi TOEFL beserta semua soalnya."""
    with get_db() as conn:
        session = conn.execute(
            "SELECT * FROM toefl_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if not session:
            return None

        questions = conn.execute(
            """
            SELECT * FROM toefl_questions
            WHERE session_id = ?
            ORDER BY section ASC, question_number ASC
            """,
            (session_id,),
        ).fetchall()

    return {
        **dict(session),
        "questions": [dict(q) for q in questions],
    }


def get_toefl_history(limit: int = 10) -> list[dict]:
    """
    Ambil riwayat simulasi TOEFL yang sudah selesai.
    Digunakan oleh Analytics Agent dan Dashboard.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM toefl_sessions
            WHERE score_status = 'completed'
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
