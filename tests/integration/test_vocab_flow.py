"""
tests/integration/test_vocab_flow.py
--------------------------------------
Integration test untuk full flow satu sesi Vocab Agent.

Berbeda dari unit test yang isolasi satu fungsi,
integration test ini mensimulasikan SELURUH ALUR:

    Planner → Generator → Validator → DB Session
    → User Jawab Soal (loop) → Evaluator → DB Save
    → Complete Session → Verifikasi semua tabel

Yang diverifikasi:
  - Tabel `sessions`       : session_id ada, status = 'completed'
  - Tabel `vocab_sessions` : metadata sesi tersimpan, skor terisi
  - Tabel `vocab_questions`: semua soal tersimpan, jawaban user tersimpan
  - Tabel `vocab_word_tracking`: tracking kata terupdate setelah sesi

Semua LLM call di-mock — test ini tidak memanggil Claude sungguhan.
DB yang dipakai adalah DB temp pytest (tmp_db fixture dari conftest.py).

Cara jalankan:
    pytest tests/integration/test_vocab_flow.py -v
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ===================================================
# Helper
# ===================================================
def _resp(text: str) -> MagicMock:
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    return m


def _make_words(n: int = 5, topic: str = "sehari_hari") -> list[dict]:
    """Buat n kata vocab sample untuk dipakai sebagai mock generator output."""
    formats = ["tebak_arti", "sinonim_antonim", "tebak_inggris"]
    words = [
        "accomplish",
        "adequate",
        "ambiguous",
        "analyze",
        "apparent",
        "appropriate",
        "approximate",
        "arbitrary",
        "assess",
        "assume",
    ]
    return [
        {
            "word": words[i % len(words)],
            "difficulty": "easy",
            "format": formats[i % len(formats)],
            "topic": topic,
            "question_text": f"What does '{words[i % len(words)]}' mean?",
            "correct_answer": f"answer_{words[i % len(words)]}",
            "is_new": True,
        }
        for i in range(n)
    ]


# ===================================================
# Integration Test: Full Vocab Session Flow
# ===================================================
class TestVocabFullFlow:

    def test_session_created_in_db(self, tmp_db):
        """
        Setelah sesi dimulai, tabel `sessions` harus punya
        satu record dengan session_id yang valid.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="vocab")

        with get_db() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row is not None
        assert row["session_id"] == sid
        assert row["mode"] == "vocab"
        assert row["status"] == "active"

    def test_vocab_session_metadata_saved(self, tmp_db):
        """
        Setelah Planner selesai, metadata sesi harus tersimpan
        di tabel `vocab_sessions` dengan nilai yang benar.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.vocab_repository import save_vocab_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="vocab")
        save_vocab_session(
            session_id=sid,
            topic="sehari_hari",
            total_words=10,
            new_words=5,
            review_words=5,
        )

        with get_db() as conn:
            row = conn.execute("SELECT * FROM vocab_sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row is not None
        assert row["topic"] == "sehari_hari"
        assert row["total_words"] == 10
        assert row["new_words"] == 5
        assert row["review_words"] == 5

    def test_all_questions_saved_to_db(self, tmp_db):
        """
        Setelah Generator + Validator selesai, semua soal harus
        tersimpan di tabel `vocab_questions` dengan jumlah yang benar.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.vocab_repository import (
            save_vocab_session,
            save_vocab_question,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        words = _make_words(n=5)

        create_session(sid, mode="vocab")
        save_vocab_session(sid, "sehari_hari", 5, 5, 0)

        for w in words:
            save_vocab_question(
                session_id=sid,
                word=w["word"],
                format=w["format"],
                topic=w["topic"],
                difficulty=w["difficulty"],
                question_text=w["question_text"],
                correct_answer=w["correct_answer"],
                is_new_word=w["is_new"],
            )

        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) as cnt FROM vocab_questions WHERE session_id = ?", (sid,)).fetchone()["cnt"]

        assert count == 5

    def test_user_answers_saved_incrementally(self, tmp_db):
        """
        Setiap kali user menjawab soal, jawaban harus langsung
        tersimpan ke DB (incremental save — bukan batch di akhir).

        Ini penting untuk ketahanan data saat browser ditutup tiba-tiba.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.vocab_repository import (
            save_vocab_session,
            save_vocab_question,
            update_vocab_answer,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        words = _make_words(n=3)

        create_session(sid, mode="vocab")
        save_vocab_session(sid, "sehari_hari", 3, 3, 0)

        # Simpan soal dan simulasikan jawaban user satu per satu
        q_ids = []
        for w in words:
            qid = save_vocab_question(
                session_id=sid,
                word=w["word"],
                format=w["format"],
                topic=w["topic"],
                difficulty=w["difficulty"],
                question_text=w["question_text"],
                correct_answer=w["correct_answer"],
                is_new_word=w["is_new"],
            )
            q_ids.append(qid)

        # Simulasi user jawab soal 1 dan 2, soal 3 belum dijawab
        update_vocab_answer(q_ids[0], "answer_accomplish", is_correct=True, is_graded=True)
        update_vocab_answer(q_ids[1], "wrong_answer", is_correct=False, is_graded=True)
        # q_ids[2] sengaja tidak dijawab

        with get_db() as conn:
            rows = conn.execute("SELECT id, user_answer, is_correct FROM vocab_questions " "WHERE session_id = ? ORDER BY id ASC", (sid,)).fetchall()

        # Soal 1 dan 2 sudah ada jawaban
        assert rows[0]["user_answer"] == "answer_accomplish"
        assert rows[0]["is_correct"] == 1

        assert rows[1]["user_answer"] == "wrong_answer"
        assert rows[1]["is_correct"] == 0

        # Soal 3 belum dijawab — user_answer masih None
        assert rows[2]["user_answer"] is None

    def test_session_status_completed_after_all_answered(self, tmp_db):
        """
        Setelah semua soal dijawab dan _complete_session() dipanggil,
        status di tabel `sessions` harus berubah menjadi 'completed'.
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="vocab")

        # Simulasi complete session
        update_session_status(sid, status="completed")

        with get_db() as conn:
            row = conn.execute("SELECT status, completed_at FROM sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["status"] == "completed"
        assert row["completed_at"] is not None  # timestamp terisi

    def test_final_score_saved_to_vocab_session(self, tmp_db):
        """
        Setelah sesi selesai, skor akhir (correct_count, wrong_count,
        score_pct) harus tersimpan di tabel `vocab_sessions`.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.vocab_repository import (
            save_vocab_session,
            update_vocab_session_scores,
        )
        from utils.helpers import calculate_score_pct, generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="vocab")
        save_vocab_session(sid, "sehari_hari", 5, 5, 0)

        # Simulasi: 4 benar, 1 salah dari 5 soal
        correct = 4
        wrong = 1
        score = calculate_score_pct(correct, correct + wrong)  # = 80.0
        update_vocab_session_scores(sid, correct, wrong, score)

        with get_db() as conn:
            row = conn.execute("SELECT correct_count, wrong_count, score_pct " "FROM vocab_sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["correct_count"] == 4
        assert row["wrong_count"] == 1
        assert row["score_pct"] == pytest.approx(80.0, abs=0.01)

    def test_word_tracking_updated_after_session(self, tmp_db):
        """
        Setelah sesi selesai, setiap kata yang dijawab harus
        punya record di tabel `vocab_word_tracking`.

        Ini adalah data untuk spaced repetition di sesi berikutnya.
        """
        from database.connection import get_db
        from database.repositories.vocab_repository import update_word_tracking
        from utils.helpers import generate_session_id

        words = _make_words(n=3)

        # Simulasi update tracking setelah sesi selesai
        for w in words:
            update_word_tracking(
                word=w["word"],
                topic=w["topic"],
                difficulty=w["difficulty"],
                is_correct=True,
            )

        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) as cnt FROM vocab_word_tracking").fetchone()["cnt"]

        assert count == 3

    def test_word_tracking_mastery_score_calculated(self, tmp_db):
        """
        mastery_score di vocab_word_tracking harus dihitung otomatis
        berdasarkan total_correct / total_seen * 100.
        """
        from database.connection import get_db
        from database.repositories.vocab_repository import update_word_tracking

        # Jawab benar 1x
        update_word_tracking("accomplish", "sehari_hari", "easy", is_correct=True)

        with get_db() as conn:
            row = conn.execute(
                "SELECT mastery_score, total_seen, total_correct " "FROM vocab_word_tracking WHERE word = ? AND topic = ?",
                ("accomplish", "sehari_hari"),
            ).fetchone()

        assert row["total_seen"] == 1
        assert row["total_correct"] == 1
        assert row["mastery_score"] == pytest.approx(100.0, abs=0.01)

    def test_word_tracking_mastery_decreases_on_wrong(self, tmp_db):
        """
        Jika user menjawab salah, mastery_score harus turun.
        Ini memastikan spaced repetition bekerja dengan benar.
        """
        from database.connection import get_db
        from database.repositories.vocab_repository import update_word_tracking

        # Jawab benar 1x dulu
        update_word_tracking("adequate", "sehari_hari", "easy", is_correct=True)
        # Kemudian jawab salah 1x
        update_word_tracking("adequate", "sehari_hari", "easy", is_correct=False)

        with get_db() as conn:
            row = conn.execute(
                "SELECT mastery_score, total_seen, total_correct " "FROM vocab_word_tracking WHERE word = ? AND topic = ?",
                ("adequate", "sehari_hari"),
            ).fetchone()

        assert row["total_seen"] == 2
        assert row["total_correct"] == 1
        assert row["mastery_score"] == pytest.approx(50.0, abs=0.01)  # 1/2 * 100

    def test_full_flow_end_to_end_with_mocked_llm(self, tmp_db):
        """
        TEST UTAMA — simulasi full flow satu sesi vocab dari awal sampai akhir:

        1. Planner (mock cold start)
        2. Generator (mock LLM)
        3. Validator (mock LLM)
        4. Buat session di DB
        5. Simpan semua soal ke DB
        6. Simulasi user jawab semua soal
        7. Evaluator (mock LLM)
        8. Simpan semua jawaban ke DB
        9. Update word tracking
        10. Complete session

        Verifikasi akhir:
        - sessions: status = 'completed'
        - vocab_sessions: skor tersimpan
        - vocab_questions: semua soal ada, semua dijawab
        - vocab_word_tracking: semua kata ter-track
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from database.repositories.vocab_repository import (
            save_vocab_session,
            save_vocab_question,
            update_vocab_answer,
            update_vocab_session_scores,
            update_word_tracking,
        )
        from utils.helpers import calculate_score_pct, generate_session_id

        # ── Step 1: Mock Planner cold start ──────────────
        planner_output = {
            "topic": "sehari_hari",
            "total_words": 5,
            "new_words": 5,
            "review_words": 0,
            "difficulty_target": "easy",
            "format_distribution": {"tebak_arti": 2, "sinonim_antonim": 2, "tebak_inggris": 1},
        }

        # ── Step 2 & 3: Mock Generator + Validator output ─
        final_words = _make_words(n=5)

        # ── Step 4: Buat session di DB ───────────────────
        sid = generate_session_id()
        create_session(sid, mode="vocab")
        save_vocab_session(
            session_id=sid,
            topic=planner_output["topic"],
            total_words=len(final_words),
            new_words=planner_output["new_words"],
            review_words=planner_output["review_words"],
        )

        # ── Step 5: Simpan soal ke DB ────────────────────
        q_ids = []
        for w in final_words:
            qid = save_vocab_question(
                session_id=sid,
                word=w["word"],
                format=w["format"],
                topic=w["topic"],
                difficulty=w["difficulty"],
                question_text=w["question_text"],
                correct_answer=w["correct_answer"],
                is_new_word=w["is_new"],
            )
            q_ids.append(qid)

        # ── Step 6 & 7: Simulasi user jawab + Evaluator ──
        correct_count = 0
        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_correct": True,
                "is_graded": True,
                "feedback": "Benar!",
            }

            from agents.vocab.evaluator import run_evaluator

            for i, (w, qid) in enumerate(zip(final_words, q_ids)):
                # Simulasi user menjawab
                user_answer = w["correct_answer"]  # jawab benar semua

                eval_result = run_evaluator(
                    word=w["word"],
                    format=w["format"],
                    question_text=w["question_text"],
                    correct_answer=w["correct_answer"],
                    user_answer=user_answer,
                    session_id=sid,
                )

                # ── Step 8: Simpan jawaban ke DB ─────────
                update_vocab_answer(
                    question_id=qid,
                    user_answer=user_answer,
                    is_correct=eval_result["is_correct"],
                    is_graded=eval_result["is_graded"],
                )

                if eval_result["is_correct"]:
                    correct_count += 1

                # ── Step 9: Update word tracking ─────────
                update_word_tracking(
                    word=w["word"],
                    topic=w["topic"],
                    difficulty=w["difficulty"],
                    is_correct=eval_result["is_correct"],
                )

        # ── Step 10: Complete session ────────────────────
        score_pct = calculate_score_pct(correct_count, len(final_words))
        update_vocab_session_scores(sid, correct_count, len(final_words) - correct_count, score_pct)
        update_session_status(sid, status="completed")

        # ════════════════════════════════════════════════
        # VERIFIKASI AKHIR — cek semua tabel
        # ════════════════════════════════════════════════
        with get_db() as conn:

            # 1. sessions: status harus completed
            session = conn.execute("SELECT status, completed_at FROM sessions WHERE session_id = ?", (sid,)).fetchone()
            assert session["status"] == "completed"
            assert session["completed_at"] is not None

            # 2. vocab_sessions: skor tersimpan
            vs = conn.execute("SELECT correct_count, wrong_count, score_pct " "FROM vocab_sessions WHERE session_id = ?", (sid,)).fetchone()
            assert vs["correct_count"] == 5  # jawab benar semua
            assert vs["wrong_count"] == 0
            assert vs["score_pct"] == pytest.approx(100.0, abs=0.01)

            # 3. vocab_questions: semua soal ada dan semua dijawab
            qs = conn.execute("SELECT COUNT(*) as total, " "SUM(CASE WHEN user_answer IS NOT NULL THEN 1 ELSE 0 END) as answered " "FROM vocab_questions WHERE session_id = ?", (sid,)).fetchone()
            assert qs["total"] == 5
            assert qs["answered"] == 5  # semua soal sudah dijawab

            # 4. vocab_word_tracking: semua kata ter-track
            wt = conn.execute("SELECT COUNT(*) as cnt FROM vocab_word_tracking").fetchone()
            assert wt["cnt"] == 5  # 5 kata unik ter-track

    def test_abandoned_session_status_is_abandoned(self, tmp_db):
        """
        Jika user keluar di tengah sesi (klik 'Keluar'),
        status harus berubah ke 'abandoned' — bukan 'incomplete'.

        Sesi abandoned tidak dihitung dalam analytics.
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="vocab")

        # User keluar di tengah jalan
        update_session_status(sid, status="abandoned")

        with get_db() as conn:
            row = conn.execute("SELECT status FROM sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["status"] == "abandoned"

    def test_is_adjusted_flag_saved_when_validator_adjusts(self, tmp_db):
        """
        Jika Validator melakukan adjustment (is_adjusted=True),
        flag ini harus tersimpan di tabel `sessions`.

        Berguna untuk audit: tahu mana sesi yang soalnya tidak sempurna.
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="vocab")

        # Sesi selesai tapi soalnya di-adjust
        update_session_status(sid, status="completed", is_adjusted=True)

        with get_db() as conn:
            row = conn.execute("SELECT is_adjusted FROM sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["is_adjusted"] == 1  # True di SQLite disimpan sebagai 1
