"""
database/repositories/quiz_repository.py
-----------------------------------------
Repository untuk tabel quiz_sessions, quiz_questions,
dan quiz_topic_tracking.
"""

from typing import Optional
from database.connection import get_db


def save_quiz_session(session_id: str, topics: str, total_questions: int) -> bool:
    """
    Simpan metadata sesi quiz baru.

    Args:
        topics: JSON string array topik, contoh: '["Subject-Verb Agreement"]'
    """
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO quiz_sessions (session_id, topics, total_questions)
            VALUES (?, ?, ?)
            """,
            (session_id, topics, total_questions),
        )
    return True


def update_quiz_session_scores(
    session_id: str, correct_count: int, wrong_count: int, score_pct: float
) -> bool:
    """Update skor akhir sesi quiz."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE quiz_sessions
            SET correct_count = ?, wrong_count = ?, score_pct = ?
            WHERE session_id = ?
            """,
            (correct_count, wrong_count, score_pct, session_id),
        )
    return True


def save_quiz_question(
    session_id: str,
    topic: str,
    cluster: str,
    format: str,
    difficulty: str,
    question_text: str,
    correct_answer: str,
    options: Optional[str] = None,
) -> int:
    """
    Simpan soal quiz (incremental save, sebelum user menjawab).

    Returns:
        id row yang baru dibuat
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO quiz_questions
                (session_id, topic, cluster, format, difficulty,
                 question_text, correct_answer, options)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                topic,
                cluster,
                format,
                difficulty,
                question_text,
                correct_answer,
                options,
            ),
        )
    return cursor.lastrowid


def update_quiz_answer(
    question_id: int,
    user_answer: str,
    is_correct: bool,
    is_graded: bool = True,
    feedback_verdict: str = None,
    feedback_explanation: str = None,
    feedback_concept: str = None,
    feedback_example: str = None,
) -> bool:
    """Update jawaban dan 4 lapisan feedback setelah soal dijawab."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE quiz_questions
            SET user_answer = ?, is_correct = ?, is_graded = ?,
                feedback_verdict = ?, feedback_explanation = ?,
                feedback_concept = ?, feedback_example = ?
            WHERE id = ?
            """,
            (
                user_answer,
                is_correct,
                is_graded,
                feedback_verdict,
                feedback_explanation,
                feedback_concept,
                feedback_example,
                question_id,
            ),
        )
    return True


def get_topic_tracking(topic: str) -> Optional[dict]:
    """
    Ambil data tracking sebuah topik grammar.
    Digunakan Planner untuk cek prerequisite dan difficulty progression.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM quiz_topic_tracking WHERE topic = ?",
            (topic,),
        ).fetchone()
    return dict(row) if row else None


def update_topic_tracking(
    topic: str, cluster: str, score_pct: float, total_questions: int, correct_count: int
) -> bool:
    """
    Update atau buat tracking topik setelah sesi selesai.
    avg_score_pct dihitung sebagai rata-rata semua sesi.
    """
    with get_db() as conn:
        existing = get_topic_tracking(topic)

        if existing:
            new_total_sessions = existing["total_sessions"] + 1
            new_total_questions = existing["total_questions"] + total_questions
            new_total_correct = existing["total_correct"] + correct_count
            new_total_wrong = existing["total_wrong"] + (total_questions - correct_count)
            new_avg = (
                existing["avg_score_pct"] * existing["total_sessions"] + score_pct
            ) / new_total_sessions

            conn.execute(
                """
                UPDATE quiz_topic_tracking
                SET total_sessions = ?, total_questions = ?,
                    total_correct = ?, total_wrong = ?,
                    avg_score_pct = ?, last_score_pct = ?,
                    last_practiced_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE topic = ?
                """,
                (
                    new_total_sessions,
                    new_total_questions,
                    new_total_correct,
                    new_total_wrong,
                    new_avg,
                    score_pct,
                    topic,
                ),
            )
        else:
            wrong_count = total_questions - correct_count
            conn.execute(
                """
                INSERT INTO quiz_topic_tracking
                    (topic, cluster, total_sessions, total_questions,
                     total_correct, total_wrong, avg_score_pct,
                     last_score_pct, last_practiced_at)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (topic, cluster, total_questions, correct_count, wrong_count, score_pct, score_pct),
            )
    return True


def set_prerequisite_met(topic: str, is_met: bool = True) -> bool:
    """Update status prerequisite sebuah topik."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE quiz_topic_tracking
            SET is_prerequisite_met = ?, updated_at = CURRENT_TIMESTAMP
            WHERE topic = ?
            """,
            (is_met, topic),
        )
    return True


def get_weak_topics(threshold: float = 70.0, limit: int = 10) -> list[dict]:
    """
    Ambil topik dengan avg_score_pct di bawah threshold.
    Digunakan Planner untuk Weak Topic Reinforcement.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM quiz_topic_tracking
            WHERE avg_score_pct < ? AND total_sessions > 0
            ORDER BY avg_score_pct ASC
            LIMIT ?
            """,
            (threshold, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_all_topic_tracking() -> list[dict]:
    """Ambil semua data tracking topik — untuk Planner dan Analytics."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM quiz_topic_tracking ORDER BY topic ASC").fetchall()
    return [dict(row) for row in rows]
