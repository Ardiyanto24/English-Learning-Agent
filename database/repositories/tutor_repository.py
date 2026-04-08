"""
database/repositories/tutor_repository.py
------------------------------------------
Repository untuk tabel tutor_sessions, tutor_questions,
dan tutor_topic_tracking.

Semua operasi DB Grammar Tutor terpusat di file ini.
Agent dan UI tidak boleh menulis SQL langsung — gunakan
fungsi-fungsi di repository ini.
"""

from database.connection import get_db


def save_tutor_session(session_id: str, topics: str, total_questions: int) -> bool:
    """
    Simpan metadata sesi Grammar Tutor baru.

    Args:
        session_id     : ID sesi unik, foreign key ke sessions(session_id)
        topics         : JSON string array topik yang dipilih user,
                         contoh: '["Simple Past Tense", "Modal Verbs"]'
        total_questions: Jumlah soal yang akan digenerate dalam sesi ini

    Returns:
        True jika INSERT berhasil
    """
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO tutor_sessions (session_id, topics, total_questions)
            VALUES (?, ?, ?)
            """,
            (session_id, topics, total_questions),
        )
    return True


def update_tutor_session_scores(
    session_id: str,
    full_credit_count: int,
    partial_credit_count: int,
    no_credit_count: int,
    score_pct: float,
) -> bool:
    """
    Update skor akhir sesi Grammar Tutor.

    Dipanggil satu kali di akhir sesi setelah seluruh soal
    selesai dinilai oleh Corrector Agent.

    Args:
        session_id          : ID sesi yang akan diupdate
        full_credit_count   : Jumlah soal yang mendapat full_credit (score 1.0)
        partial_credit_count: Jumlah soal yang mendapat partial_credit (score 0.5)
        no_credit_count     : Jumlah soal yang mendapat no_credit (score 0.0)
        score_pct           : Skor akhir sesi dalam persen (0–100)

    Returns:
        True jika UPDATE berhasil
    """
    with get_db() as conn:
        conn.execute(
            """
            UPDATE tutor_sessions
            SET full_credit_count = ?, partial_credit_count = ?,
                no_credit_count = ?, score_pct = ?
            WHERE session_id = ?
            """,
            (full_credit_count, partial_credit_count, no_credit_count, score_pct, session_id),
        )
    return True
