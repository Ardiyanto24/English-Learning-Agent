"""
tests/integration/test_toefl_flow.py
--------------------------------------
Integration test untuk full flow TOEFL Simulator mode 50%.

Yang diverifikasi:
  - Tabel `sessions`       : session_id ada, status = 'completed'
  - Tabel `toefl_sessions` : semua raw/extrapolated/scaled score tersimpan,
                             estimated_score dalam range 310–677,
                             score_status = 'completed'
  - Tabel `toefl_questions`: semua soal 3 section tersimpan, semua dijawab
  - Pause/Resume flow      : status 'paused' + expires_at tersimpan,
                             resume valid → state dikembalikan,
                             resume expired → session abandoned
  - Estimasi skor          : verifikasi formula ITP end-to-end

Mode 50% distribusi: L=25, S=20, R=25 soal.

Cara jalankan:
    pytest tests/integration/test_toefl_flow.py -v
"""

import json
from datetime import datetime, timedelta

# ===================================================
# Konstanta mode 50%
# ===================================================
MODE = "50%"
L_TOTAL = 25  # Listening
S_TOTAL = 20  # Structure
R_TOTAL = 25  # Reading


# ===================================================
# Helper
# ===================================================
def _make_questions(
    session_id: str, section: str, part: str, count: int, correct_answer: str = "A"
) -> list[dict]:
    """Buat list soal TOEFL untuk satu section."""
    return [
        {
            "session_id": session_id,
            "section": section,
            "part": part,
            "question_number": i + 1,
            "question_text": f"{section.title()} question {i + 1}.",
            "options": json.dumps(["A. opt A", "B. opt B", "C. opt C", "D. opt D"]),
            "correct_answer": correct_answer,
            "difficulty": "easy",
        }
        for i in range(count)
    ]


def _save_section_questions(session_id: str, section: str, part: str, count: int) -> list[int]:
    """Simpan soal ke DB, return list q_ids."""
    from database.repositories.toefl_repository import save_toefl_question

    q_ids = []
    questions = _make_questions(session_id, section, part, count)
    for q in questions:
        qid = save_toefl_question(
            session_id=session_id,
            section=q["section"],
            part=q["part"],
            question_number=q["question_number"],
            question_text=q["question_text"],
            options=q["options"],
            correct_answer=q["correct_answer"],
            difficulty=q["difficulty"],
        )
        q_ids.append(qid)
    return q_ids


def _answer_all_correct(q_ids: list, correct_answer: str = "A") -> int:
    """Simulasi user jawab semua soal dengan benar. Return correct_count."""
    from database.repositories.toefl_repository import update_toefl_answer

    for qid in q_ids:
        update_toefl_answer(qid, user_answer=correct_answer, is_correct=True)
    return len(q_ids)


def _answer_partial(q_ids: list, correct_count: int, correct_answer: str = "A") -> int:
    """
    Simulasi user jawab sebagian benar.
    correct_count soal pertama dijawab benar, sisanya salah.
    """
    from database.repositories.toefl_repository import update_toefl_answer

    for i, qid in enumerate(q_ids):
        if i < correct_count:
            update_toefl_answer(qid, user_answer=correct_answer, is_correct=True)
        else:
            update_toefl_answer(qid, user_answer="B", is_correct=False)
    return correct_count


# ===================================================
# Integration Test: Full TOEFL Session Flow
# ===================================================
class TestToeflFullFlow:

    def test_session_created_in_db(self, tmp_db):
        """
        Setelah sesi dimulai, tabel `sessions` harus punya
        record dengan mode='toefl' dan status='active'.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.toefl_repository import save_toefl_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        with get_db() as conn:
            session = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (sid,)).fetchone()
            toefl = conn.execute(
                "SELECT * FROM toefl_sessions WHERE session_id = ?", (sid,)
            ).fetchone()

        assert session is not None
        assert session["mode"] == "toefl"
        assert session["status"] == "active"
        assert toefl is not None
        assert toefl["mode"] == MODE
        assert toefl["score_status"] == "pending"
        assert toefl["current_section"] == 1

    def test_all_three_sections_questions_saved(self, tmp_db):
        """
        Mode 50%: total soal yang disimpan harus L=25, S=20, R=25.
        Semua soal harus ada sebelum user mulai menjawab.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.toefl_repository import save_toefl_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        l_ids = _save_section_questions(sid, "listening", "A", L_TOTAL)
        s_ids = _save_section_questions(sid, "structure", "A", S_TOTAL)
        r_ids = _save_section_questions(sid, "reading", "A", R_TOTAL)

        # Verifikasi semua soal berhasil disimpan (ID tidak None)
        assert all(
            i is not None for i in l_ids
        ), f"Listening: {l_ids.count(None)} soal gagal disimpan ke DB"
        assert all(
            i is not None for i in s_ids
        ), f"Structure: {s_ids.count(None)} soal gagal disimpan ke DB"
        assert all(
            i is not None for i in r_ids
        ), f"Reading: {r_ids.count(None)} soal gagal disimpan ke DB"

        with get_db() as conn:
            counts = conn.execute(
                """
                SELECT section, COUNT(*) as cnt
                FROM toefl_questions WHERE session_id = ?
                GROUP BY section
                """,
                (sid,),
            ).fetchall()

        count_map = {row["section"]: row["cnt"] for row in counts}

        assert count_map.get("listening") == L_TOTAL
        assert count_map.get("structure") == S_TOTAL
        assert count_map.get("reading") == R_TOTAL

    def test_answers_saved_incrementally(self, tmp_db):
        """
        Jawaban user harus tersimpan secara incremental per soal —
        bukan batch di akhir. Ini ketahanan data saat koneksi terputus.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.toefl_repository import (
            save_toefl_session,
            update_toefl_answer,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        q_ids = _save_section_questions(sid, "listening", "A", 3)

        # Jawab hanya 2 soal pertama
        update_toefl_answer(q_ids[0], "A", is_correct=True)
        update_toefl_answer(q_ids[1], "B", is_correct=False)
        # q_ids[2] sengaja belum dijawab

        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, user_answer, is_correct FROM toefl_questions "
                "WHERE session_id = ? ORDER BY question_number ASC",
                (sid,),
            ).fetchall()

        assert rows[0]["user_answer"] == "A"
        assert rows[0]["is_correct"] == 1
        assert rows[1]["user_answer"] == "B"
        assert rows[1]["is_correct"] == 0
        assert rows[2]["user_answer"] is None  # belum dijawab

    def test_score_calculated_and_saved(self, tmp_db):
        """
        Setelah semua section selesai, process_full_score harus
        menghitung estimated_score yang valid (310–677) dan
        semua intermediate values harus tersimpan ke tabel toefl_sessions.
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from database.repositories.toefl_repository import (
            save_toefl_session,
            update_toefl_scores,
        )
        from modules.scoring.toefl_converter import process_full_score
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        # Simulasi: jawab benar 18 dari 25 listening,
        #           15 dari 20 structure, 20 dari 25 reading
        l_raw, s_raw, r_raw = 18, 15, 20

        scores = process_full_score(
            listening_raw=l_raw,
            structure_raw=s_raw,
            reading_raw=r_raw,
            listening_total_mode=L_TOTAL,
            structure_total_mode=S_TOTAL,
            reading_total_mode=R_TOTAL,
        )

        update_toefl_scores(
            session_id=sid,
            listening_raw=scores["listening_raw"],
            structure_raw=scores["structure_raw"],
            reading_raw=scores["reading_raw"],
            listening_extrapolated=scores["listening_extrapolated"],
            structure_extrapolated=scores["structure_extrapolated"],
            reading_extrapolated=scores["reading_extrapolated"],
            listening_scaled=scores["listening_scaled"],
            structure_scaled=scores["structure_scaled"],
            reading_scaled=scores["reading_scaled"],
            estimated_score=scores["estimated_score"],
        )
        update_session_status(sid, status="completed")

        with get_db() as conn:
            toefl = conn.execute(
                "SELECT * FROM toefl_sessions WHERE session_id = ?", (sid,)
            ).fetchone()

        # Semua intermediate values tersimpan
        assert toefl["listening_raw"] == l_raw
        assert toefl["structure_raw"] == s_raw
        assert toefl["reading_raw"] == r_raw
        assert toefl["listening_extrapolated"] == scores["listening_extrapolated"]
        assert toefl["structure_extrapolated"] == scores["structure_extrapolated"]
        assert toefl["reading_extrapolated"] == scores["reading_extrapolated"]
        assert toefl["listening_scaled"] == scores["listening_scaled"]
        assert toefl["structure_scaled"] == scores["structure_scaled"]
        assert toefl["reading_scaled"] == scores["reading_scaled"]
        assert toefl["estimated_score"] == scores["estimated_score"]
        assert toefl["score_status"] == "completed"

        # estimated_score dalam range valid
        assert 310 <= toefl["estimated_score"] <= 677

    def test_estimated_score_range_various_scenarios(self, tmp_db):
        """
        estimated_score harus selalu dalam range 310–677,
        untuk berbagai kombinasi jawaban benar.
        """
        from database.repositories.session_repository import create_session
        from database.repositories.toefl_repository import (
            save_toefl_session,
            update_toefl_scores,
        )
        from modules.scoring.toefl_converter import process_full_score
        from utils.helpers import generate_session_id

        scenarios = [
            (0, 0, 0),  # semua salah → skor minimum
            (25, 20, 25),  # semua benar → skor maksimum
            (18, 15, 20),  # tipikal
            (10, 8, 12),  # di bawah rata-rata
        ]

        for l_raw, s_raw, r_raw in scenarios:
            sid = generate_session_id()
            create_session(sid, mode="toefl")
            save_toefl_session(sid, mode=MODE)

            scores = process_full_score(
                listening_raw=l_raw,
                structure_raw=s_raw,
                reading_raw=r_raw,
                listening_total_mode=L_TOTAL,
                structure_total_mode=S_TOTAL,
                reading_total_mode=R_TOTAL,
            )

            update_toefl_scores(
                sid,
                **{
                    k: scores[k]
                    for k in [
                        "listening_raw",
                        "structure_raw",
                        "reading_raw",
                        "listening_extrapolated",
                        "structure_extrapolated",
                        "reading_extrapolated",
                        "listening_scaled",
                        "structure_scaled",
                        "reading_scaled",
                        "estimated_score",
                    ]
                },
            )

            assert 310 <= scores["estimated_score"] <= 677, (
                f"Scenario L={l_raw} S={s_raw} R={r_raw}: "
                f"estimated={scores['estimated_score']} out of range"
            )

    def test_perfect_score_50pct_mode(self, tmp_db):
        """
        Jawab semua benar di mode 50% → estimated_score harus 677
        (skor maksimum TOEFL ITP).
        """
        from modules.scoring.toefl_converter import process_full_score

        scores = process_full_score(
            listening_raw=L_TOTAL,
            structure_raw=S_TOTAL,
            reading_raw=R_TOTAL,
            listening_total_mode=L_TOTAL,
            structure_total_mode=S_TOTAL,
            reading_total_mode=R_TOTAL,
        )

        assert scores["estimated_score"] == 677

    def test_zero_score_50pct_mode(self, tmp_db):
        """
        Jawab semua salah di mode 50% → estimated_score harus 310
        (skor minimum TOEFL ITP).
        """
        from modules.scoring.toefl_converter import process_full_score

        scores = process_full_score(
            listening_raw=0,
            structure_raw=0,
            reading_raw=0,
            listening_total_mode=L_TOTAL,
            structure_total_mode=S_TOTAL,
            reading_total_mode=R_TOTAL,
        )

        assert scores["estimated_score"] == 310

    def test_pause_session_after_listening(self, tmp_db):
        """
        User pause setelah menyelesaikan Listening (section 1):
        - sessions.status harus 'paused'
        - sessions.expires_at harus terisi (7 hari ke depan)
        - toefl_sessions.current_section harus 2 (lanjut Structure)
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            pause_toefl_session,
        )
        from database.repositories.toefl_repository import save_toefl_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        # Simpan dan jawab semua soal Listening dulu
        l_ids = _save_section_questions(sid, "listening", "A", L_TOTAL)
        _answer_all_correct(l_ids)

        # Pause setelah section 1
        now_dt = datetime.now()
        paused_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        expires_at = (now_dt + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

        pause_toefl_session(
            session_id=sid,
            current_section=2,  # next section setelah pause
            paused_at=paused_at,
            expires_at=expires_at,
        )

        with get_db() as conn:
            session = conn.execute(
                "SELECT status, expires_at FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
            toefl = conn.execute(
                "SELECT current_section FROM toefl_sessions WHERE session_id = ?", (sid,)
            ).fetchone()

        assert session["status"] == "paused"
        assert session["expires_at"] is not None
        assert toefl["current_section"] == 2  # akan lanjut dari Structure

    def test_resume_valid_session(self, tmp_db):
        """
        Resume sesi yang masih valid (belum expired):
        - check_and_resume_toefl_session harus return dict state
        - state berisi current_section dan answered_questions
        """
        from database.repositories.session_repository import (
            create_session,
            pause_toefl_session,
            check_and_resume_toefl_session,
        )
        from database.repositories.toefl_repository import save_toefl_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        l_ids = _save_section_questions(sid, "listening", "A", L_TOTAL)
        _answer_all_correct(l_ids)

        now_dt = datetime.now()
        expires_at = (now_dt + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

        pause_toefl_session(
            session_id=sid,
            current_section=2,
            paused_at=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
            expires_at=expires_at,
        )

        # Resume dengan "now" yang masih sebelum expires_at
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        state = check_and_resume_toefl_session(sid, now=now_str)

        assert state is not None
        assert state["session_id"] == sid
        assert state["current_section"] == 2
        assert state["mode"] == MODE
        assert len(state["answered_questions"]) == L_TOTAL

    def test_resume_expired_session_returns_none(self, tmp_db):
        """
        Resume sesi yang sudah expired (> 7 hari):
        - check_and_resume_toefl_session harus return None
        - sessions.status harus berubah ke 'abandoned'
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            pause_toefl_session,
            check_and_resume_toefl_session,
        )
        from database.repositories.toefl_repository import save_toefl_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        l_ids = _save_section_questions(sid, "listening", "A", 5)
        _answer_all_correct(l_ids)

        # Set expires_at ke masa lalu (sudah expired)
        past = datetime.now() - timedelta(days=8)
        expires_at = past.strftime("%Y-%m-%d %H:%M:%S")
        paused_at = (past - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

        pause_toefl_session(
            session_id=sid,
            current_section=2,
            paused_at=paused_at,
            expires_at=expires_at,
        )

        # "now" setelah expires_at → expired
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state = check_and_resume_toefl_session(sid, now=now_str)

        assert state is None

        # Status harus berubah ke 'abandoned'
        with get_db() as conn:
            session = conn.execute(
                "SELECT status FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()

        assert session["status"] == "abandoned"

    def test_full_flow_mode_50pct_end_to_end(self, tmp_db):
        """
        TEST UTAMA — simulasi full flow TOEFL mode 50%:

        1. Buat session
        2. Simpan soal 3 section (L=25, S=20, R=25)
        3. Simulasi user jawab semua soal (benar semua)
        4. Hitung skor dengan process_full_score
        5. Simpan semua skor ke DB
        6. Complete session

        Verifikasi akhir:
        - sessions        : status = 'completed'
        - toefl_sessions  : semua score fields terisi, score_status='completed',
                            estimated_score dalam range 310–677
        - toefl_questions : total soal = 70 (25+20+25), semua dijawab
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from database.repositories.toefl_repository import (
            save_toefl_session,
            update_toefl_scores,
        )
        from modules.scoring.toefl_converter import process_full_score
        from utils.helpers import generate_session_id

        # ── Step 1: Buat session ─────────────────────────
        sid = generate_session_id()
        create_session(sid, mode="toefl")
        save_toefl_session(sid, mode=MODE)

        # ── Step 2: Simpan soal semua section ───────────
        l_ids = _save_section_questions(sid, "listening", "A", L_TOTAL)
        s_ids = _save_section_questions(sid, "structure", "A", S_TOTAL)
        r_ids = _save_section_questions(sid, "reading", "A", R_TOTAL)

        # ── Step 3: User jawab semua soal ───────────────
        # Listening: 20 benar dari 25
        l_correct = _answer_partial(l_ids, correct_count=20)
        # Structure: 16 benar dari 20
        s_correct = _answer_partial(s_ids, correct_count=16)
        # Reading: 18 benar dari 25
        r_correct = _answer_partial(r_ids, correct_count=18)

        # ── Step 4: Hitung skor ──────────────────────────
        scores = process_full_score(
            listening_raw=l_correct,
            structure_raw=s_correct,
            reading_raw=r_correct,
            listening_total_mode=L_TOTAL,
            structure_total_mode=S_TOTAL,
            reading_total_mode=R_TOTAL,
        )

        # ── Step 5: Simpan skor ke DB ────────────────────
        update_toefl_scores(
            session_id=sid,
            listening_raw=scores["listening_raw"],
            structure_raw=scores["structure_raw"],
            reading_raw=scores["reading_raw"],
            listening_extrapolated=scores["listening_extrapolated"],
            structure_extrapolated=scores["structure_extrapolated"],
            reading_extrapolated=scores["reading_extrapolated"],
            listening_scaled=scores["listening_scaled"],
            structure_scaled=scores["structure_scaled"],
            reading_scaled=scores["reading_scaled"],
            estimated_score=scores["estimated_score"],
        )

        # ── Step 6: Complete session ─────────────────────
        update_session_status(sid, status="completed")

        # ════════════════════════════════════════════════
        # VERIFIKASI AKHIR
        # ════════════════════════════════════════════════
        with get_db() as conn:

            # 1. sessions: status completed
            session = conn.execute(
                "SELECT status, completed_at FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
            assert session["status"] == "completed"
            assert session["completed_at"] is not None

            # 2. toefl_sessions: semua skor tersimpan
            toefl = conn.execute(
                "SELECT * FROM toefl_sessions WHERE session_id = ?", (sid,)
            ).fetchone()

            assert toefl["listening_raw"] == l_correct
            assert toefl["structure_raw"] == s_correct
            assert toefl["reading_raw"] == r_correct
            assert toefl["listening_extrapolated"] is not None
            assert toefl["listening_scaled"] is not None
            assert toefl["score_status"] == "completed"
            assert 310 <= toefl["estimated_score"] <= 677

            # 3. toefl_questions: total soal 70, semua dijawab
            q_stats = conn.execute(
                """
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN user_answer IS NOT NULL THEN 1 ELSE 0 END) as answered
                FROM toefl_questions WHERE session_id = ?
                """,
                (sid,),
            ).fetchone()

            assert q_stats["total"] == L_TOTAL + S_TOTAL + R_TOTAL  # 70
            assert q_stats["answered"] == L_TOTAL + S_TOTAL + R_TOTAL  # semua dijawab

    def test_section_score_consistency(self, tmp_db):
        """
        Verifikasi konsistensi antar layer konversi:
        extrapolated → scaled harus meningkat seiring raw score yang lebih tinggi.
        """
        from modules.scoring.toefl_converter import (
            extrapolate_score,
            convert_to_scaled,
        )

        # Skenario: skor rendah vs skor tinggi
        low_extrap = extrapolate_score(10, L_TOTAL, 50)
        high_extrap = extrapolate_score(23, L_TOTAL, 50)

        low_scaled = convert_to_scaled(low_extrap, "listening")
        high_scaled = convert_to_scaled(high_extrap, "listening")

        # Skor lebih tinggi → scaled lebih tinggi
        assert high_scaled > low_scaled

    def test_toefl_history_excludes_incomplete(self, tmp_db):
        """
        get_toefl_history() hanya mengembalikan sesi yang
        score_status = 'completed'. Sesi pending/abandoned tidak muncul.
        """
        from database.repositories.session_repository import create_session
        from database.repositories.toefl_repository import (
            save_toefl_session,
            update_toefl_scores,
            get_toefl_history,
        )
        from modules.scoring.toefl_converter import process_full_score
        from utils.helpers import generate_session_id

        # Sesi 1: completed
        sid1 = generate_session_id()
        create_session(sid1, mode="toefl")
        save_toefl_session(sid1, mode=MODE)
        scores = process_full_score(18, 15, 20, L_TOTAL, S_TOTAL, R_TOTAL)
        update_toefl_scores(
            sid1,
            **{
                k: scores[k]
                for k in [
                    "listening_raw",
                    "structure_raw",
                    "reading_raw",
                    "listening_extrapolated",
                    "structure_extrapolated",
                    "reading_extrapolated",
                    "listening_scaled",
                    "structure_scaled",
                    "reading_scaled",
                    "estimated_score",
                ]
            },
        )

        # Sesi 2: pending (tidak pernah di-update scores)
        sid2 = generate_session_id()
        create_session(sid2, mode="toefl")
        save_toefl_session(sid2, mode=MODE)

        history = get_toefl_history()

        session_ids_in_history = [h["session_id"] for h in history]

        assert sid1 in session_ids_in_history  # completed → muncul
        assert sid2 not in session_ids_in_history  # pending → tidak muncul
