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
