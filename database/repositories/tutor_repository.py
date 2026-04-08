"""
database/repositories/tutor_repository.py
------------------------------------------
Repository untuk tabel tutor_sessions, tutor_questions,
dan tutor_topic_tracking.

Semua operasi DB Grammar Tutor terpusat di file ini.
Agent dan UI tidak boleh menulis SQL langsung — gunakan
fungsi-fungsi di repository ini.
"""

from typing import Optional
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


def save_tutor_question(
    session_id: str,
    topic: str,
    question_type: str,
    question_text: str,
    reference_answer: str,
) -> int:
    """
    Simpan soal Grammar Tutor (incremental save, sebelum user menjawab).

    Kolom user_answer, credit_level, score, dan feedback dibiarkan NULL
    karena user belum menjawab. Diisi kemudian oleh update_tutor_question_answer.

    Args:
        session_id      : ID sesi, foreign key ke sessions(session_id)
        topic           : Topik grammar soal ini, contoh: "Simple Past Tense"
        question_type   : Tipe soal, salah satu dari: type_1_recall, type_2_pattern,
                          type_3_classify, type_4_transform, type_5_error, type_6_reason
        question_text   : Teks pertanyaan yang ditampilkan ke user
        reference_answer: Jawaban acuan dari Generator, dipakai Corrector sebagai patokan

    Returns:
        lastrowid — ID row yang baru dibuat, dibutuhkan oleh update_tutor_question_answer
    """
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tutor_questions
                (session_id, topic, question_type, question_text, reference_answer)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, topic, question_type, question_text, reference_answer),
        )
    return cursor.lastrowid


def update_tutor_question_answer(
    question_id: int,
    user_answer: str,
    credit_level: str,
    score: float,
    is_graded: bool,
    feedback_verdict: Optional[str] = None,
    feedback_concept: Optional[str] = None,
    feedback_tip: Optional[str] = None,
) -> bool:
    """
    Update jawaban user dan hasil penilaian Corrector untuk satu soal.

    Dipanggil setelah Submit All — satu kali per soal setelah Corrector
    selesai menilai dan menghasilkan tiga lapisan feedback.

    Args:
        question_id     : ID row di tutor_questions yang akan diupdate
        user_answer     : Jawaban yang diinput user
        credit_level    : Tier penilaian: "full_credit", "partial_credit", atau "no_credit"
        score           : Nilai numerik tier: 1.0, 0.5, atau 0.0
        is_graded       : True jika Corrector berhasil menilai, False jika gagal setelah 3x retry
        feedback_verdict: Lapisan 1 — penjelasan tier yang diterima dan alasannya
        feedback_concept: Lapisan 2 — rule grammar yang seharusnya diaplikasikan
        feedback_tip    : Lapisan 3 — cara mudah mengingat rule (mnemonik/analogi)

    Returns:
        True jika UPDATE berhasil
    """
    with get_db() as conn:
        conn.execute(
            """
            UPDATE tutor_questions
            SET user_answer = ?, credit_level = ?, score = ?, is_graded = ?,
                feedback_verdict = ?, feedback_concept = ?, feedback_tip = ?
            WHERE id = ?
            """,
            (
                user_answer,
                credit_level,
                score,
                is_graded,
                feedback_verdict,
                feedback_concept,
                feedback_tip,
                question_id,
            ),
        )
    return True


def get_tutor_topic_tracking(topic: str) -> Optional[dict]:
    """
    Ambil data tracking sebuah topik Grammar Tutor.

    Digunakan Planner untuk dua keperluan:
    - Prerequisite check: apakah avg_score_pct >= 60?
    - Penentuan proficiency level: cold_start / familiar / advanced

    Args:
        topic: Nama topik grammar, contoh: "Simple Past Tense"

    Returns:
        Dict seluruh kolom tutor_topic_tracking jika record ditemukan,
        atau None jika topik belum pernah dilatih di Grammar Tutor (cold start)
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM tutor_topic_tracking WHERE topic = ?",
            (topic,),
        ).fetchone()
    return dict(row) if row else None


def upsert_tutor_topic_tracking(
    topic: str,
    session_score_pct: float,
    full_credit: int,
    partial_credit: int,
    no_credit: int,
    question_count: int,
) -> bool:
    """
    Insert atau update akumulasi performa user untuk satu topik Grammar Tutor.

    Dipanggil satu kali di akhir setiap sesi untuk setiap topik yang dilatih.
    avg_score_pct dihitung sebagai rata-rata tertimbang seluruh sesi historis
    berdasarkan jumlah soal per sesi — bukan simple average antar sesi.

    Args:
        topic            : Nama topik grammar yang baru selesai dilatih
        session_score_pct: Skor sesi yang baru selesai dalam persen (0–100)
        full_credit      : Jumlah soal full_credit di sesi ini
        partial_credit   : Jumlah soal partial_credit di sesi ini
        no_credit        : Jumlah soal no_credit di sesi ini
        question_count   : Total soal di sesi ini untuk topik ini

    Returns:
        True jika INSERT atau UPDATE berhasil
    """
    existing = get_tutor_topic_tracking(topic)

    with get_db() as conn:
        if existing is None:
            # Cabang 1: topik belum pernah dilatih — INSERT baseline
            conn.execute(
                """
                INSERT INTO tutor_topic_tracking (
                    topic, total_sessions, total_questions,
                    full_credit_count, partial_credit_count, no_credit_count,
                    avg_score_pct, last_score_pct, last_practiced_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    topic,
                    question_count,
                    full_credit,
                    partial_credit,
                    no_credit,
                    session_score_pct,
                    session_score_pct,
                ),
            )
        else:
            # Cabang 2: topik sudah ada — hitung nilai baru lalu UPDATE
            new_total_sessions = existing["total_sessions"] + 1
            new_total_questions = existing["total_questions"] + question_count
            new_full_credit = existing["full_credit_count"] + full_credit
            new_partial_credit = existing["partial_credit_count"] + partial_credit
            new_no_credit = existing["no_credit_count"] + no_credit

            # Rata-rata tertimbang: bobot proporsional terhadap jumlah soal per sesi
            new_avg_score_pct = (
                existing["total_questions"] * existing["avg_score_pct"]
                + question_count * session_score_pct
            ) / new_total_questions

            conn.execute(
                """
                UPDATE tutor_topic_tracking
                SET total_sessions = ?, total_questions = ?,
                    full_credit_count = ?, partial_credit_count = ?, no_credit_count = ?,
                    avg_score_pct = ?, last_score_pct = ?,
                    last_practiced_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE topic = ?
                """,
                (
                    new_total_sessions,
                    new_total_questions,
                    new_full_credit,
                    new_partial_credit,
                    new_no_credit,
                    new_avg_score_pct,
                    session_score_pct,
                    topic,
                ),
            )
    return True
