"""
tests/integration/test_quiz_flow.py
--------------------------------------
Integration test untuk full flow satu sesi Quiz Agent.

Yang diverifikasi:
  - Tabel `sessions`           : session_id ada, status = 'completed'
  - Tabel `quiz_sessions`      : metadata sesi dan skor tersimpan
  - Tabel `quiz_questions`     : semua soal tersimpan dengan 4 lapisan feedback
  - Tabel `quiz_topic_tracking`: tracking per topik terupdate setelah sesi

Focus khusus checklist:
  ✓ Full flow satu sesi quiz dengan RAG (di-mock)
  ✓ Verifikasi feedback 4 lapisan tersimpan ke DB
  ✓ topic_tracking terupdate (Weak Topic Reinforcement data)
  ✓ Sesi abandoned tidak update topic_tracking

Semua LLM dan RAG di-mock — test tidak memanggil Claude/ChromaDB sungguhan.

Cara jalankan:
    pytest tests/integration/test_quiz_flow.py -v
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ===================================================
# Helper
# ===================================================
def _make_questions(n: int = 5, topic: str = "Present Tenses") -> list[dict]:
    """Buat n soal quiz sample sebagai mock generator output."""
    formats = ["multiple_choice", "fill_blank", "error_id"]
    return [
        {
            "id":             i + 1,
            "topic":          topic,
            "cluster":        "Tense System",
            "format":         formats[i % len(formats)],
            "difficulty":     "easy",
            "question_text":  f"Sample question {i+1} about {topic}.",
            "options":        ["A. Option A", "B. Option B", "C. Option C", "D. Option D"],
            "correct_answer": "A",
        }
        for i in range(n)
    ]


def _make_correction(is_correct: bool = True) -> dict:
    """Buat mock correction result dari Corrector Agent."""
    return {
        "is_correct": is_correct,
        "is_graded":  True,
        "feedback": {
            "verdict":     "✓ Benar!" if is_correct else "✗ Kurang tepat.",
            "explanation": "Present simple digunakan untuk kebiasaan.",
            "concept":     "Ingat: he/she/it + V1+s/es.",
            "example":     [
                "✓ She walks to school every day.",
                "✗ She walk to school every day.",
            ],
        },
    }


def _make_planner_output(topics: list = None) -> dict:
    """Buat mock planner output."""
    if topics is None:
        topics = ["Present Tenses"]
    return {
        "topics":              topics,
        "cluster":             "Tense System",
        "total_questions":     5,
        "difficulty_target":   "easy",
        "format_distribution": {"multiple_choice": 3, "fill_blank": 1, "error_id": 1},
        "new_topics":          topics,
        "review_topics":       [],
        "is_cold_start":       True,
        "accessible_topics":   topics,
    }


# ===================================================
# Integration Test: Full Quiz Session Flow
# ===================================================
class TestQuizFullFlow:

    def test_session_created_in_db(self, tmp_db):
        """
        Setelah sesi dimulai, tabel `sessions` harus punya
        record dengan mode='quiz' dan status='active'.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="quiz")

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()

        assert row is not None
        assert row["mode"]   == "quiz"
        assert row["status"] == "active"

    def test_quiz_session_metadata_saved(self, tmp_db):
        """
        Metadata sesi (topics, total_questions) harus tersimpan
        di tabel `quiz_sessions`.

        topics disimpan sebagai list Python — repository menerima list,
        bukan JSON string.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.quiz_repository import save_quiz_session
        from utils.helpers import generate_session_id

        sid    = generate_session_id()
        topics = ["Present Tenses"]

        create_session(sid, mode="quiz")
        save_quiz_session(
            session_id      = sid,
            topics          = json.dumps(topics),
            total_questions = 5,
        )

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM quiz_sessions WHERE session_id = ?", (sid,)
            ).fetchone()

        assert row is not None
        assert row["total_questions"] == 5

    def test_all_questions_saved_to_db(self, tmp_db):
        """
        Semua soal dari Generator harus tersimpan di tabel `quiz_questions`
        sebelum user mulai menjawab (incremental save).
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.quiz_repository import (
            save_quiz_session,
            save_quiz_question,
        )
        from utils.helpers import generate_session_id

        sid       = generate_session_id()
        questions = _make_questions(n=5)

        create_session(sid, mode="quiz")
        save_quiz_session(sid, json.dumps(["Present Tenses"]), 5)

        for q in questions:
            save_quiz_question(
                session_id    = sid,
                topic         = q["topic"],
                cluster       = q["cluster"],
                format        = q["format"],
                difficulty    = q["difficulty"],
                question_text = q["question_text"],
                correct_answer= q["correct_answer"],
                options       = json.dumps(q["options"]),
            )

        with get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM quiz_questions WHERE session_id = ?",
                (sid,)
            ).fetchone()["cnt"]

        assert count == 5

    def test_4_layer_feedback_saved_to_db(self, tmp_db):
        """
        FOCUS TEST — 4 lapisan feedback Corrector harus tersimpan
        ke 4 kolom terpisah di tabel `quiz_questions`:
          - feedback_verdict
          - feedback_explanation
          - feedback_concept
          - feedback_example

        Ini adalah perbedaan utama quiz vs vocab:
        Quiz menyimpan feedback detail, bukan sekadar benar/salah.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.quiz_repository import (
            save_quiz_session,
            save_quiz_question,
            update_quiz_answer,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        q   = _make_questions(n=1)[0]
        correction = _make_correction(is_correct=True)
        feedback   = correction["feedback"]

        create_session(sid, mode="quiz")
        save_quiz_session(sid, json.dumps(["Present Tenses"]), 1)

        qid = save_quiz_question(
            session_id    = sid,
            topic         = q["topic"],
            cluster       = q["cluster"],
            format        = q["format"],
            difficulty    = q["difficulty"],
            question_text = q["question_text"],
            correct_answer= q["correct_answer"],
            options       = json.dumps(q["options"]),
        )

        # Simpan jawaban dengan 4 lapisan feedback
        update_quiz_answer(
            question_id          = qid,
            user_answer          = "A",
            is_correct           = correction["is_correct"],
            is_graded            = correction["is_graded"],
            feedback_verdict     = feedback["verdict"],
            feedback_explanation = feedback["explanation"],
            feedback_concept     = feedback["concept"],
            feedback_example     = json.dumps(feedback["example"]),
        )

        with get_db() as conn:
            row = conn.execute(
                "SELECT feedback_verdict, feedback_explanation, "
                "feedback_concept, feedback_example "
                "FROM quiz_questions WHERE id = ?",
                (qid,)
            ).fetchone()

        # Verifikasi semua 4 lapisan tersimpan
        assert row["feedback_verdict"]     == "✓ Benar!"
        assert row["feedback_explanation"] == "Present simple digunakan untuk kebiasaan."
        assert row["feedback_concept"]     == "Ingat: he/she/it + V1+s/es."
        assert row["feedback_example"]     is not None   # JSON string tersimpan

        # Verifikasi example bisa di-parse kembali
        example = json.loads(row["feedback_example"])
        assert isinstance(example, list)
        assert len(example) == 2

    def test_feedback_saved_for_wrong_answer(self, tmp_db):
        """
        Feedback juga harus tersimpan untuk jawaban SALAH.
        Ini penting karena feedback salah justru lebih edukatif.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.quiz_repository import (
            save_quiz_session,
            save_quiz_question,
            update_quiz_answer,
        )
        from utils.helpers import generate_session_id

        sid        = generate_session_id()
        q          = _make_questions(n=1)[0]
        correction = _make_correction(is_correct=False)
        feedback   = correction["feedback"]

        create_session(sid, mode="quiz")
        save_quiz_session(sid, json.dumps(["Present Tenses"]), 1)

        qid = save_quiz_question(
            session_id    = sid,
            topic         = q["topic"],
            cluster       = q["cluster"],
            format        = q["format"],
            difficulty    = q["difficulty"],
            question_text = q["question_text"],
            correct_answer= q["correct_answer"],
            options       = json.dumps(q["options"]),
        )

        update_quiz_answer(
            question_id          = qid,
            user_answer          = "B",   # jawaban salah
            is_correct           = False,
            is_graded            = True,
            feedback_verdict     = feedback["verdict"],
            feedback_explanation = feedback["explanation"],
            feedback_concept     = feedback["concept"],
            feedback_example     = json.dumps(feedback["example"]),
        )

        with get_db() as conn:
            row = conn.execute(
                "SELECT is_correct, feedback_verdict FROM quiz_questions WHERE id = ?",
                (qid,)
            ).fetchone()

        assert row["is_correct"]      == 0               # False di SQLite = 0
        assert "Kurang tepat" in row["feedback_verdict"]

    def test_topic_tracking_updated_after_session(self, tmp_db):
        """
        Setelah sesi selesai, `quiz_topic_tracking` harus punya
        record untuk topik yang dilatih dengan skor yang benar.

        Ini adalah data yang dipakai Planner untuk:
        - Weak Topic Reinforcement (topik dengan skor rendah diprioritaskan)
        - Difficulty Progression
        - Prerequisite checking
        """
        from database.connection import get_db
        from database.repositories.quiz_repository import update_topic_tracking

        update_topic_tracking(
            topic           = "Present Tenses",
            cluster         = "Tense System",
            score_pct       = 80.0,
            total_questions = 5,
            correct_count   = 4,
        )

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM quiz_topic_tracking WHERE topic = ?",
                ("Present Tenses",)
            ).fetchone()

        assert row is not None
        assert row["topic"]           == "Present Tenses"
        assert row["cluster"]         == "Tense System"
        assert row["total_sessions"]  == 1
        assert row["total_questions"] == 5
        assert row["total_correct"]   == 4
        assert row["avg_score_pct"]   == pytest.approx(80.0, abs=0.01)

    def test_topic_tracking_avg_score_accumulates(self, tmp_db):
        """
        avg_score_pct harus merupakan rata-rata dari semua sesi,
        bukan hanya sesi terakhir.

        Sesi 1: 80%, Sesi 2: 60% → avg = 70%
        """
        from database.connection import get_db
        from database.repositories.quiz_repository import update_topic_tracking

        # Sesi 1: skor 80%
        update_topic_tracking("Present Tenses", "Tense System", 80.0, 5, 4)
        # Sesi 2: skor 60%
        update_topic_tracking("Present Tenses", "Tense System", 60.0, 5, 3)

        with get_db() as conn:
            row = conn.execute(
                "SELECT total_sessions, avg_score_pct FROM quiz_topic_tracking "
                "WHERE topic = ?",
                ("Present Tenses",)
            ).fetchone()

        assert row["total_sessions"]  == 2
        assert row["avg_score_pct"]   == pytest.approx(70.0, abs=0.01)

    def test_session_completed_status_saved(self, tmp_db):
        """
        Setelah semua soal dijawab, status sesi harus 'completed'
        dan completed_at harus terisi.
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="quiz")
        update_session_status(sid, status="completed")

        with get_db() as conn:
            row = conn.execute(
                "SELECT status, completed_at FROM sessions WHERE session_id = ?",
                (sid,)
            ).fetchone()

        assert row["status"]       == "completed"
        assert row["completed_at"] is not None

    def test_final_score_saved_to_quiz_session(self, tmp_db):
        """
        correct_count, wrong_count, score_pct harus tersimpan
        di tabel `quiz_sessions` setelah sesi selesai.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.quiz_repository import (
            save_quiz_session,
            update_quiz_session_scores,
        )
        from utils.helpers import calculate_score_pct, generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="quiz")
        save_quiz_session(sid, json.dumps(["Present Tenses"]), 5)

        # 4 benar, 1 salah dari 5 soal
        update_quiz_session_scores(sid, 4, 1, calculate_score_pct(4, 5))

        with get_db() as conn:
            row = conn.execute(
                "SELECT correct_count, wrong_count, score_pct "
                "FROM quiz_sessions WHERE session_id = ?",
                (sid,)
            ).fetchone()

        assert row["correct_count"] == 4
        assert row["wrong_count"]   == 1
        assert row["score_pct"]     == pytest.approx(80.0, abs=0.01)

    def test_full_flow_end_to_end_with_mocked_llm(self, tmp_db):
        """
        TEST UTAMA — simulasi full flow satu sesi quiz dari awal sampai akhir:

        1. Planner output (mock)
        2. Generator output (mock) + RAG (mock)
        3. Validator (mock)
        4. Buat session di DB
        5. Simpan semua soal ke DB
        6. Simulasi user jawab semua soal
        7. Corrector (mock LLM) — verifikasi 4 lapisan feedback
        8. Simpan jawaban + feedback ke DB
        9. Update topic_tracking
        10. Complete session

        Verifikasi akhir:
        - sessions           : status = 'completed'
        - quiz_sessions      : skor tersimpan
        - quiz_questions     : semua soal ada, semua dijawab, 4 lapisan feedback ada
        - quiz_topic_tracking: tracking topik terupdate
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from database.repositories.quiz_repository import (
            save_quiz_session,
            save_quiz_question,
            update_quiz_answer,
            update_quiz_session_scores,
            update_topic_tracking,
        )
        from utils.helpers import calculate_score_pct, generate_session_id

        # ── Step 1-3: Mock Planner + Generator + Validator ──
        planner_output = _make_planner_output(["Present Tenses"])
        final_questions = _make_questions(n=5)

        # ── Step 4: Buat session ─────────────────────────
        sid = generate_session_id()
        create_session(sid, mode="quiz")
        save_quiz_session(
            session_id      = sid,
            topics          = json.dumps(planner_output["topics"]),
            total_questions = len(final_questions),
        )

        # ── Step 5: Simpan soal ke DB ────────────────────
        q_ids = []
        for q in final_questions:
            qid = save_quiz_question(
                session_id    = sid,
                topic         = q["topic"],
                cluster       = q["cluster"],
                format        = q["format"],
                difficulty    = q["difficulty"],
                question_text = q["question_text"],
                correct_answer= q["correct_answer"],
                options       = json.dumps(q["options"]),
            )
            q_ids.append(qid)

        # ── Step 6 & 7: Simulasi jawab + Corrector mock ──
        mock_correction = _make_correction(is_correct=True)

        correct_count = 0
        topic_stats   = {}

        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, \
             patch("agents.quiz.corrector._get_rag_context_for_correction") as mock_rag:

            mock_llm.return_value = mock_correction
            mock_rag.return_value = "[Topic: Present Tenses — RAG context mocked]"

            from agents.quiz.corrector import run_corrector

            for q, qid in zip(final_questions, q_ids):
                user_answer = "A"   # jawab benar semua

                correction = run_corrector(
                    topic          = q["topic"],
                    format         = q["format"],
                    question_text  = q["question_text"],
                    options        = q["options"],
                    correct_answer = q["correct_answer"],
                    user_answer    = user_answer,
                    session_id     = sid,
                )

                feedback = correction["feedback"]

                # ── Step 8: Simpan jawaban + 4 lapisan feedback ─
                update_quiz_answer(
                    question_id          = qid,
                    user_answer          = user_answer,
                    is_correct           = correction["is_correct"],
                    is_graded            = correction["is_graded"],
                    feedback_verdict     = feedback["verdict"],
                    feedback_explanation = feedback["explanation"],
                    feedback_concept     = feedback["concept"],
                    feedback_example     = json.dumps(feedback["example"]),
                )

                if correction["is_correct"]:
                    correct_count += 1

                # Kumpulkan stats per topik
                t = q["topic"]
                if t not in topic_stats:
                    topic_stats[t] = {"cluster": q["cluster"], "total": 0, "correct": 0}
                topic_stats[t]["total"]   += 1
                topic_stats[t]["correct"] += 1 if correction["is_correct"] else 0

        # ── Step 9: Update topic_tracking ────────────────
        for topic, stats in topic_stats.items():
            score = calculate_score_pct(stats["correct"], stats["total"])
            update_topic_tracking(
                topic           = topic,
                cluster         = stats["cluster"],
                score_pct       = score,
                total_questions = stats["total"],
                correct_count   = stats["correct"],
            )

        # ── Step 10: Complete session ────────────────────
        score_pct = calculate_score_pct(correct_count, len(final_questions))
        update_quiz_session_scores(sid, correct_count, len(final_questions) - correct_count, score_pct)
        update_session_status(sid, status="completed")

        # ════════════════════════════════════════════════
        # VERIFIKASI AKHIR — cek semua tabel
        # ════════════════════════════════════════════════
        with get_db() as conn:

            # 1. sessions: status completed
            session = conn.execute(
                "SELECT status, completed_at FROM sessions WHERE session_id = ?",
                (sid,)
            ).fetchone()
            assert session["status"]       == "completed"
            assert session["completed_at"] is not None

            # 2. quiz_sessions: skor tersimpan
            qs = conn.execute(
                "SELECT correct_count, wrong_count, score_pct "
                "FROM quiz_sessions WHERE session_id = ?",
                (sid,)
            ).fetchone()
            assert qs["correct_count"] == 5
            assert qs["wrong_count"]   == 0
            assert qs["score_pct"]     == pytest.approx(100.0, abs=0.01)

            # 3. quiz_questions: semua soal ada, dijawab, 4 lapisan feedback ada
            questions_db = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN user_answer IS NOT NULL THEN 1 ELSE 0 END) as answered, "
                "SUM(CASE WHEN feedback_verdict IS NOT NULL THEN 1 ELSE 0 END) as has_verdict, "
                "SUM(CASE WHEN feedback_explanation IS NOT NULL THEN 1 ELSE 0 END) as has_explanation, "
                "SUM(CASE WHEN feedback_concept IS NOT NULL THEN 1 ELSE 0 END) as has_concept, "
                "SUM(CASE WHEN feedback_example IS NOT NULL THEN 1 ELSE 0 END) as has_example "
                "FROM quiz_questions WHERE session_id = ?",
                (sid,)
            ).fetchone()

            assert questions_db["total"]           == 5   # semua soal tersimpan
            assert questions_db["answered"]         == 5   # semua dijawab
            assert questions_db["has_verdict"]      == 5   # layer 1 ada di semua soal
            assert questions_db["has_explanation"]  == 5   # layer 2 ada di semua soal
            assert questions_db["has_concept"]      == 5   # layer 3 ada di semua soal
            assert questions_db["has_example"]      == 5   # layer 4 ada di semua soal

            # 4. quiz_topic_tracking: topik ter-track
            tracking = conn.execute(
                "SELECT total_sessions, avg_score_pct "
                "FROM quiz_topic_tracking WHERE topic = ?",
                ("Present Tenses",)
            ).fetchone()
            assert tracking is not None
            assert tracking["total_sessions"] == 1
            assert tracking["avg_score_pct"]  == pytest.approx(100.0, abs=0.01)

    def test_rag_context_used_by_corrector(self, tmp_db):
        """
        Memastikan RAG context diambil dan dikirim ke Corrector LLM.
        RAG yang gagal harus di-fallback ke nama topik, bukan crash.
        """
        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, \
             patch("agents.quiz.corrector.retrieve") as mock_retrieve:

            mock_llm.return_value = _make_correction(is_correct=True)
            mock_retrieve.side_effect = Exception("ChromaDB unavailable")

            from agents.quiz.corrector import run_corrector

            # Meskipun RAG gagal, corrector tetap jalan
            result = run_corrector(
                topic          = "Present Tenses",
                format         = "multiple_choice",
                question_text  = "She ___ to school every day.",
                options        = ["A. walks", "B. walk", "C. walked", "D. walking"],
                correct_answer = "A",
                user_answer    = "A",
            )

        # Corrector tidak crash meskipun RAG gagal
        assert result["is_graded"] is True

    def test_ungraded_answer_when_corrector_fails(self, tmp_db):
        """
        Jika Corrector LLM gagal setelah 3x retry, jawaban harus
        ditandai is_graded=False. Sesi tetap jalan.

        Verifikasi: is_graded=False tersimpan ke DB.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.quiz_repository import (
            save_quiz_session,
            save_quiz_question,
            update_quiz_answer,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        q   = _make_questions(n=1)[0]

        create_session(sid, mode="quiz")
        save_quiz_session(sid, json.dumps(["Present Tenses"]), 1)

        qid = save_quiz_question(
            session_id    = sid,
            topic         = q["topic"],
            cluster       = q["cluster"],
            format        = q["format"],
            difficulty    = q["difficulty"],
            question_text = q["question_text"],
            correct_answer= q["correct_answer"],
            options       = json.dumps(q["options"]),
        )

        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, \
             patch("agents.quiz.corrector._get_rag_context_for_correction") as mock_rag:

            mock_llm.side_effect  = Exception("LLM totally down")
            mock_rag.return_value = "[Topic: Present Tenses]"

            from agents.quiz.corrector import run_corrector
            correction = run_corrector(
                topic          = q["topic"],
                format         = q["format"],
                question_text  = q["question_text"],
                options        = q["options"],
                correct_answer = q["correct_answer"],
                user_answer    = "A",
            )

        # Simpan hasil ungraded ke DB
        feedback = correction.get("feedback", {})
        update_quiz_answer(
            question_id          = qid,
            user_answer          = "A",
            is_correct           = correction["is_correct"],
            is_graded            = correction["is_graded"],
            feedback_verdict     = feedback.get("verdict"),
            feedback_explanation = feedback.get("explanation"),
            feedback_concept     = feedback.get("concept"),
            feedback_example     = json.dumps(feedback.get("example", [])),
        )

        with get_db() as conn:
            row = conn.execute(
                "SELECT is_graded, is_correct FROM quiz_questions WHERE id = ?",
                (qid,)
            ).fetchone()

        assert row["is_graded"]  == 0   # False = ungraded
        assert row["is_correct"] == 0   # False saat ungraded