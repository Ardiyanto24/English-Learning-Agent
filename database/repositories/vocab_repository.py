"""
database/repositories/vocab_repository.py
------------------------------------------
Repository untuk tabel vocab_sessions, vocab_questions,
dan vocab_word_tracking.
"""

from typing import Optional
from database.connection import get_db


def save_vocab_session(
    session_id: str, topic: str, total_words: int, new_words: int, review_words: int
) -> bool:
    """Simpan metadata sesi vocab baru."""
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO vocab_sessions
                (session_id, topic, total_words, new_words, review_words)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, topic, total_words, new_words, review_words),
        )
    return True


def update_vocab_session_scores(
    session_id: str, correct_count: int, wrong_count: int, score_pct: float
) -> bool:
    """Update skor akhir sesi vocab setelah selesai."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE vocab_sessions
            SET correct_count = ?, wrong_count = ?, score_pct = ?
            WHERE session_id = ?
            """,
            (correct_count, wrong_count, score_pct, session_id),
        )
    return True


def save_vocab_question(
    session_id: str,
    word: str,
    format: str,
    topic: str,
    difficulty: str,
    question_text: str,
    correct_answer: str,
    is_new_word: bool = True,
) -> int:
    """
    Simpan satu soal vocab ke database (incremental save).
    Dipanggil segera setelah soal di-generate, sebelum user menjawab.

    Returns:
        id row yang baru dibuat (dipakai untuk update jawaban nanti)
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO vocab_questions
                (session_id, word, format, topic, difficulty,
                 question_text, correct_answer, is_new_word)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                word,
                format,
                topic,
                difficulty,
                question_text,
                correct_answer,
                is_new_word,
            ),
        )
    return cursor.lastrowid


def update_vocab_answer(
    question_id: int, user_answer: str, is_correct: bool, is_graded: bool = True
) -> bool:
    """Update jawaban user setelah soal dijawab."""
    with get_db() as conn:
        conn.execute(
            """
            UPDATE vocab_questions
            SET user_answer = ?, is_correct = ?, is_graded = ?
            WHERE id = ?
            """,
            (user_answer, is_correct, is_graded, question_id),
        )
    return True


def get_word_tracking(word: str, topic: str) -> Optional[dict]:
    """
    Ambil data tracking sebuah kata.
    Digunakan Planner untuk spaced repetition decision.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM vocab_word_tracking WHERE word = ? AND topic = ?",
            (word, topic),
        ).fetchone()
    return dict(row) if row else None


def update_word_tracking(word: str, topic: str, difficulty: str, is_correct: bool) -> bool:
    """
    Update tracking kata setelah user menjawab soal.
    Menggunakan INSERT OR REPLACE untuk upsert (insert jika baru, update jika ada).
    mastery_score dihitung otomatis sebagai persentase jawaban benar.
    """
    with get_db() as conn:
        # Ambil data lama dulu
        existing = conn.execute(
            "SELECT * FROM vocab_word_tracking WHERE word = ? AND topic = ?",
            (word, topic),
        ).fetchone()

        if existing:
            total_seen = existing["total_seen"] + 1
            total_correct = existing["total_correct"] + (1 if is_correct else 0)
            total_wrong = existing["total_wrong"] + (0 if is_correct else 1)
            mastery_score = (total_correct / total_seen) * 100

            conn.execute(
                """
                UPDATE vocab_word_tracking
                SET total_seen = ?, total_correct = ?, total_wrong = ?,
                    mastery_score = ?, last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE word = ? AND topic = ?
                """,
                (total_seen, total_correct, total_wrong, mastery_score, word, topic),
            )
        else:
            # Kata baru — buat entry baru
            total_correct = 1 if is_correct else 0
            mastery_score = 100.0 if is_correct else 0.0
            conn.execute(
                """
                INSERT INTO vocab_word_tracking
                    (word, topic, difficulty, total_seen, total_correct,
                     total_wrong, mastery_score, last_seen_at)
                VALUES (?, ?, ?, 1, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (word, topic, difficulty, total_correct, 0 if is_correct else 1, mastery_score),
            )
    return True


def get_weak_words(topic: str, threshold: float = 60.0, limit: int = 20) -> list[dict]:
    """
    Ambil daftar kata lemah (mastery_score di bawah threshold).
    Digunakan Planner untuk prioritaskan kata yang perlu di-review.

    Args:
        topic: topik yang dicari
        threshold: batas mastery score (default 60%)
        limit: maksimal jumlah kata

    Returns:
        List kata diurutkan dari mastery_score terendah
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM vocab_word_tracking
            WHERE topic = ? AND mastery_score < ?
            ORDER BY mastery_score ASC, last_seen_at ASC
            LIMIT ?
            """,
            (topic, threshold, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_spaced_repetition_words(
    topic: str,
    threshold: float = 60.0,
    limit: int = 5,
) -> list[dict]:
    """
    Ambil kata untuk review via spaced repetition.
    Prioritas: kata yang paling LAMA tidak dilihat (last_seen_at ASC).
    Filter: hanya kata dengan mastery_score < threshold.

    Berbeda dari get_weak_words() yang prioritas mastery terendah —
    fungsi ini memastikan rotasi kata, bukan kata yang sama terus muncul.

    Args:
        topic    : Topik yang dicari
        threshold: Batas mastery score (default 60%)
        limit    : Jumlah kata yang diambil (sesuai review_words dari Planner)

    Returns:
        List dict kata, diurutkan dari yang terlama tidak dilihat
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT * FROM vocab_word_tracking
            WHERE topic = ? AND mastery_score < ?
            ORDER BY last_seen_at ASC
            LIMIT ?
            """,
            (topic, threshold, limit),
        ).fetchall()
    return [dict(row) for row in rows]
