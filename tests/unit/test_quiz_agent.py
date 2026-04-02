"""
tests/unit/test_quiz_agent.py
------------------------------
Unit test untuk Quiz Agent — berdasarkan kode asli di agents/quiz/.

Yang ditest:
  Planner  : cold start, prerequisite blocking, 5 logic hierarki,
             output format, format_distribution, difficulty progression
  Validator: match_score >= 0.8 pass, < 0.8 trigger regenerate,
             is_adjusted=True setelah max retry, final_questions selalu ada,
             generator failure graceful
  Corrector: 4 lapisan feedback ada, is_graded=False saat LLM gagal,
             jawaban benar/salah, example harus list 2 item

Semua test TIDAK memanggil LLM atau DB sungguhan — semua di-mock.
"""

from unittest.mock import MagicMock, patch

import pytest


# ===================================================
# Helper
# ===================================================
def _resp(text: str) -> MagicMock:
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    return m


# ===================================================
# Sample data yang dipakai berulang
# ===================================================
PLANNER_OUTPUT = {
    "topics": ["Present Tenses"],
    "cluster": "Tense System",
    "total_questions": 10,
    "difficulty_target": "easy",
    "format_distribution": {
        "multiple_choice": 7,
        "error_id": 1,
        "fill_blank": 2,
    },
    "new_topics": ["Present Tenses"],
    "review_topics": [],
    "is_cold_start": True,
    "accessible_topics": ["Present Tenses"],
}

GENERATOR_OUTPUT = {
    "questions": [
        {
            "id": i + 1,
            "format": "multiple_choice" if i < 7 else ("error_id" if i == 7 else "fill_blank"),
            "topic": "Present Tenses",
            "difficulty": "easy",
            "question_text": f"Sample question {i + 1}",
            "options": ["A. opt A", "B. opt B", "C. opt C", "D. opt D"],
            "correct_answer": "A",
        }
        for i in range(10)
    ]
}


# ===================================================
# Test: Quiz Planner
# ===================================================
class TestQuizPlanner:

    def test_cold_start_is_detected(self):
        """
        Saat DB kosong (tidak ada topik yang pernah dilatih),
        is_cold_start harus True.
        """
        with patch("agents.quiz.planner._get_all_topic_tracking") as mock_track, patch("agents.quiz.planner._get_practiced_topics_this_session_pool") as mock_prac:

            mock_track.return_value = {}
            mock_prac.return_value = set()  # belum ada topik dilatih

            from agents.quiz.planner import run_planner

            result = run_planner()

        assert result["is_cold_start"] is True

    def test_cold_start_difficulty_is_easy(self):
        """Cold start selalu return difficulty_target = 'easy'."""
        with patch("agents.quiz.planner._get_all_topic_tracking") as mock_track, patch("agents.quiz.planner._get_practiced_topics_this_session_pool") as mock_prac:

            mock_track.return_value = {}
            mock_prac.return_value = set()

            from agents.quiz.planner import run_planner

            result = run_planner()

        assert result["difficulty_target"] == "easy"

    def test_output_has_all_required_keys(self):
        """Output planner harus punya semua key yang dibutuhkan Generator."""
        with patch("agents.quiz.planner._get_all_topic_tracking") as mock_track, patch("agents.quiz.planner._get_practiced_topics_this_session_pool") as mock_prac:

            mock_track.return_value = {}
            mock_prac.return_value = set()

            from agents.quiz.planner import run_planner

            result = run_planner()

        required = {
            "topics",
            "cluster",
            "total_questions",
            "difficulty_target",
            "format_distribution",
            "new_topics",
            "review_topics",
            "is_cold_start",
            "accessible_topics",
        }
        assert required.issubset(set(result.keys()))

    def test_format_distribution_sums_to_total_questions(self):
        """Sum format_distribution harus == total_questions."""
        with patch("agents.quiz.planner._get_all_topic_tracking") as mock_track, patch("agents.quiz.planner._get_practiced_topics_this_session_pool") as mock_prac:

            mock_track.return_value = {}
            mock_prac.return_value = set()

            from agents.quiz.planner import run_planner

            result = run_planner(total_questions=10)

        dist_sum = sum(result["format_distribution"].values())
        assert dist_sum == result["total_questions"]

    def test_prerequisite_blocks_advanced_topic(self):
        """
        Topik dengan prerequisite yang belum dikuasai TIDAK boleh
        masuk accessible_topics.

        Contoh: 'Past Tenses' butuh 'Present Tenses' sudah dikuasai.
        Jika 'Present Tenses' belum dilatih → 'Past Tenses' diblokir.
        """
        from agents.quiz.planner import _filter_by_prerequisite, PREREQUISITE_RULES

        if PREREQUISITE_RULES is None:
            pytest.skip("PREREQUISITE_RULES tidak tersedia")

        # Simulasi: Present Tenses belum dilatih (tidak ada di tracking)
        topic_tracking = {}  # DB kosong

        all_topics = ["Present Tenses", "Past Tenses", "Future Tenses"]
        accessible = _filter_by_prerequisite(all_topics, topic_tracking)

        # Present Tenses tidak punya prerequisite → lolos
        assert "Present Tenses" in accessible

        # Past Tenses butuh Present Tenses (belum dilatih) → diblokir
        assert "Past Tenses" not in accessible

        # Future Tenses butuh Present+Past (keduanya belum) → diblokir
        assert "Future Tenses" not in accessible

    def test_topic_without_prerequisite_always_accessible(self):
        """
        Topik yang tidak punya prerequisite harus selalu lolos filter,
        bahkan saat DB kosong.
        """
        from agents.quiz.planner import _filter_by_prerequisite, PREREQUISITE_RULES

        if PREREQUISITE_RULES is None:
            pytest.skip("PREREQUISITE_RULES tidak tersedia")

        topic_tracking = {}
        accessible = _filter_by_prerequisite(["Present Tenses"], topic_tracking)

        assert "Present Tenses" in accessible

    def test_prerequisite_unlocked_when_score_above_threshold(self):
        """
        Topik advance harus BISA DIAKSES jika semua prerequisite-nya
        sudah dikuasai (avg_score_pct >= MASTERY_THRESHOLD * 100).
        """
        from agents.quiz.planner import (
            _filter_by_prerequisite,
            PREREQUISITE_RULES,
        )

        if PREREQUISITE_RULES is None:
            pytest.skip("PREREQUISITE_RULES tidak tersedia")

        # Simulasi: Present Tenses sudah dikuasai (skor 80%)
        topic_tracking = {
            "Present Tenses": {
                "avg_score_pct": 80.0,
                "total_sessions": 5,
            }
        }

        accessible = _filter_by_prerequisite(["Present Tenses", "Past Tenses"], topic_tracking)

        # Past Tenses hanya butuh Present Tenses — sudah terpenuhi
        assert "Past Tenses" in accessible

    def test_cognitive_load_max_1_new_topic(self):
        """
        Logic 2 Cognitive Load: maksimal 1 topik BARU per sesi.
        Meskipun ada 5 topik baru tersedia, hanya 1 yang dipilih.
        """
        from agents.quiz.planner import _apply_cognitive_load

        accessible = ["Present Tenses", "Subject-Verb Agreement", "Articles", "Prepositions", "Pronouns"]
        practiced = set()  # Semua masih baru

        new_topics, review_topics = _apply_cognitive_load(accessible, practiced)

        assert len(new_topics) <= 1

    def test_difficulty_upgrade_at_80_pct(self):
        """
        Logic 3 Difficulty: jika avg skor review topics >= 80%
        → difficulty harus 'hard'.
        """
        from agents.quiz.planner import _determine_difficulty

        topic_tracking = {
            "Present Tenses": {"avg_score_pct": 85.0, "total_sessions": 5},
        }

        difficulty = _determine_difficulty(["Present Tenses"], topic_tracking)
        assert difficulty == "hard"

    def test_difficulty_downgrade_below_40_pct(self):
        """
        Logic 3 Difficulty: jika avg skor review topics < 40%
        → difficulty harus 'easy'.
        """
        from agents.quiz.planner import _determine_difficulty

        topic_tracking = {
            "Present Tenses": {"avg_score_pct": 30.0, "total_sessions": 3},
        }

        difficulty = _determine_difficulty(["Present Tenses"], topic_tracking)
        assert difficulty == "easy"

    def test_weak_topic_prioritized_first(self):
        """
        Logic 4 Weak Topic Reinforcement: topik dengan skor paling
        rendah harus muncul pertama dalam list.
        """
        from agents.quiz.planner import _prioritize_weak_topics

        topic_tracking = {
            "Present Tenses": {"avg_score_pct": 70.0},
            "Past Tenses": {"avg_score_pct": 30.0},  # ← lebih lemah
            "Future Tenses": {"avg_score_pct": 50.0},
        }

        review = ["Present Tenses", "Past Tenses", "Future Tenses"]
        result = _prioritize_weak_topics(review, topic_tracking, max_topics=2)

        # Past Tenses (30%) harus jadi prioritas pertama
        assert result[0] == "Past Tenses"


# ===================================================
# Test: Quiz Validator
# ===================================================
class TestQuizValidator:

    def test_valid_score_passes_immediately(self):
        """
        match_score >= 0.8 → langsung return is_valid=True.
        LLM hanya dipanggil 1x — tidak ada retry.
        """
        valid_resp = {
            "is_valid": True,
            "match_score": 0.9,
            "issues": [],
            "adjusted_questions": [],
        }

        with patch("agents.quiz.validator._call_validator_llm") as mock_llm:
            mock_llm.return_value = valid_resp

            from agents.quiz.validator import run_validator

            result = run_validator(PLANNER_OUTPUT, GENERATOR_OUTPUT)

        assert result["is_valid"] is True
        assert result["is_adjusted"] is False
        assert result["match_score"] >= 0.8
        assert mock_llm.call_count == 1  # tidak ada retry

    def test_invalid_score_triggers_regeneration(self):
        """
        match_score < 0.8 → run_generator dipanggil ulang.
        Ini memastikan kualitas soal selalu dijaga.
        """
        bad_resp = {
            "is_valid": False,
            "match_score": 0.5,
            "issues": ["format mismatch"],
            "adjusted_questions": [],
        }

        with patch("agents.quiz.validator._call_validator_llm") as mock_llm, patch("agents.quiz.validator.run_generator") as mock_gen:

            mock_llm.return_value = bad_resp
            mock_gen.return_value = GENERATOR_OUTPUT

            from agents.quiz.validator import run_validator

            run_validator(PLANNER_OUTPUT, GENERATOR_OUTPUT)

        # Generator harus dipanggil setidaknya 1x setelah validasi gagal
        assert mock_gen.call_count >= 1

    def test_max_retry_sets_is_adjusted_true(self):
        """
        Setelah MAX_REGENERATE_ATTEMPTS (3x) semua gagal,
        is_adjusted=True — soal tidak sempurna tapi sesi tetap lanjut.
        """
        bad_resp = {
            "is_valid": False,
            "match_score": 0.3,
            "issues": ["consistently bad"],
            "adjusted_questions": [],
        }

        with patch("agents.quiz.validator._call_validator_llm") as mock_llm, patch("agents.quiz.validator.run_generator") as mock_gen:

            mock_llm.return_value = bad_resp
            mock_gen.return_value = GENERATOR_OUTPUT

            from agents.quiz.validator import run_validator

            result = run_validator(PLANNER_OUTPUT, GENERATOR_OUTPUT)

        assert result["is_adjusted"] is True

    def test_final_questions_always_present(self):
        """
        Output Validator selalu punya 'final_questions' — bahkan saat
        LLM gagal total. Ini memastikan UI tidak crash.
        """
        with patch("agents.quiz.validator._call_validator_llm") as mock_llm, patch("agents.quiz.validator.run_generator") as mock_gen:

            mock_llm.side_effect = Exception("LLM down")
            mock_gen.return_value = GENERATOR_OUTPUT

            from agents.quiz.validator import run_validator

            result = run_validator(PLANNER_OUTPUT, GENERATOR_OUTPUT)

        assert "final_questions" in result
        assert isinstance(result["final_questions"], list)

    def test_generator_runtime_error_handled_gracefully(self):
        """
        Jika Generator raise RuntimeError saat retry,
        Validator tidak crash — fallback ke soal yang ada.
        """
        bad_resp = {
            "is_valid": False,
            "match_score": 0.5,
            "issues": [],
            "adjusted_questions": [],
        }

        with patch("agents.quiz.validator._call_validator_llm") as mock_llm, patch("agents.quiz.validator.run_generator") as mock_gen:

            mock_llm.return_value = bad_resp
            mock_gen.side_effect = RuntimeError("Generator totally failed")

            from agents.quiz.validator import run_validator

            result = run_validator(PLANNER_OUTPUT, GENERATOR_OUTPUT)

        # Tidak crash, final_questions tetap ada
        assert "final_questions" in result
        assert len(result["final_questions"]) > 0

    def test_validator_unavailable_flag_when_llm_always_fails(self):
        """
        Jika LLM validator tidak pernah berhasil (selalu exception),
        is_validator_unavailable harus True.
        """
        with patch("agents.quiz.validator._call_validator_llm") as mock_llm, patch("agents.quiz.validator.run_generator") as mock_gen:

            mock_llm.side_effect = Exception("API down")
            mock_gen.return_value = GENERATOR_OUTPUT

            from agents.quiz.validator import run_validator

            result = run_validator(PLANNER_OUTPUT, GENERATOR_OUTPUT)

        assert result.get("is_validator_unavailable") is True


# ===================================================
# Test: Quiz Corrector
# ===================================================
class TestQuizCorrector:

    # Data sample untuk semua test Corrector
    BASE_ARGS = dict(
        topic="Present Tenses",
        format="multiple_choice",
        question_text="She ___ to school every day.",
        options=["A. walk", "B. walks", "C. walked", "D. walking"],
        correct_answer="B",
        user_answer="B",
    )

    def test_correct_answer_has_4_feedback_layers(self):
        """
        Jawaban benar harus return 4 lapisan feedback:
        verdict, explanation, concept, example.

        Ini adalah inti dari nilai pedagogis agent ini.
        """
        mock_result = {
            "is_correct": True,
            "is_graded": True,
            "feedback": {
                "verdict": "✓ Benar!",
                "explanation": "She walks menggunakan present simple untuk kebiasaan.",
                "concept": "Ingat: he/she/it + V1+s/es untuk present simple.",
                "example": [
                    "✓ She walks to school every day.",
                    "✗ She walk to school every day.",
                ],
            },
        }

        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, patch("agents.quiz.corrector._get_rag_context_for_correction") as mock_rag:

            mock_llm.return_value = mock_result
            mock_rag.return_value = "[Topic: Present Tenses]"

            from agents.quiz.corrector import run_corrector

            result = run_corrector(**self.BASE_ARGS)

        # Cek semua 4 lapisan ada
        feedback = result["feedback"]
        assert "verdict" in feedback
        assert "explanation" in feedback
        assert "concept" in feedback
        assert "example" in feedback

    def test_example_is_list_with_2_items(self):
        """
        Field 'example' harus berupa list dengan tepat 2 item:
        1 kalimat benar (✓) dan 1 kalimat salah (✗).
        """
        mock_result = {
            "is_correct": True,
            "is_graded": True,
            "feedback": {
                "verdict": "✓ Benar!",
                "explanation": "Correct usage of present simple.",
                "concept": "Subject + V1+s/es for he/she/it.",
                "example": [
                    "✓ She walks to school.",
                    "✗ She walk to school.",
                ],
            },
        }

        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, patch("agents.quiz.corrector._get_rag_context_for_correction") as mock_rag:

            mock_llm.return_value = mock_result
            mock_rag.return_value = "[Topic: Present Tenses]"

            from agents.quiz.corrector import run_corrector

            result = run_corrector(**self.BASE_ARGS)

        example = result["feedback"]["example"]
        assert isinstance(example, list)
        assert len(example) == 2

    def test_wrong_answer_returns_is_correct_false(self):
        """Jawaban salah → is_correct=False, feedback tetap 4 lapisan."""
        mock_result = {
            "is_correct": False,
            "is_graded": True,
            "feedback": {
                "verdict": "✗ Kurang tepat.",
                "explanation": "Seharusnya 'walks' untuk she/he/it.",
                "concept": "Ingat: he/she/it + V1+s/es.",
                "example": [
                    "✓ She walks to school.",
                    "✗ She walk to school.",
                ],
            },
        }

        wrong_args = {**self.BASE_ARGS, "user_answer": "A"}  # jawaban salah

        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, patch("agents.quiz.corrector._get_rag_context_for_correction") as mock_rag:

            mock_llm.return_value = mock_result
            mock_rag.return_value = "[Topic: Present Tenses]"

            from agents.quiz.corrector import run_corrector

            result = run_corrector(**wrong_args)

        assert result["is_correct"] is False
        assert result["is_graded"] is True

    def test_llm_failure_returns_is_graded_false(self):
        """
        LLM gagal setelah 3x retry → is_graded=False.
        Sesi TETAP JALAN — feedback fallback tetap punya 4 lapisan.
        """
        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, patch("agents.quiz.corrector._get_rag_context_for_correction") as mock_rag:

            mock_llm.side_effect = Exception("LLM timeout")
            mock_rag.return_value = "[Topic: Present Tenses]"

            from agents.quiz.corrector import run_corrector

            result = run_corrector(**self.BASE_ARGS)

        assert result["is_graded"] is False
        assert result["is_correct"] is False

        # Feedback fallback tetap punya 4 lapisan
        feedback = result["feedback"]
        assert "verdict" in feedback
        assert "explanation" in feedback
        assert "concept" in feedback
        assert "example" in feedback

    def test_rag_failure_does_not_crash_corrector(self):
        """
        Jika RAG retrieval gagal, Corrector tetap jalan
        menggunakan nama topik sebagai fallback context.
        """
        mock_result = {
            "is_correct": True,
            "is_graded": True,
            "feedback": {
                "verdict": "✓ Benar!",
                "explanation": "Good.",
                "concept": "Rule X.",
                "example": ["✓ Good.", "✗ Bad."],
            },
        }

        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, patch("agents.quiz.corrector.retrieve") as mock_retrieve:

            mock_llm.return_value = mock_result
            mock_retrieve.side_effect = Exception("ChromaDB down")

            from agents.quiz.corrector import run_corrector

            result = run_corrector(**self.BASE_ARGS)

        # Tidak crash meskipun RAG gagal
        assert result["is_graded"] is True

    def test_output_has_top_level_required_fields(self):
        """Output Corrector harus punya is_correct, is_graded, feedback."""
        mock_result = {
            "is_correct": True,
            "is_graded": True,
            "feedback": {
                "verdict": "✓ Benar!",
                "explanation": "Correct.",
                "concept": "Rule.",
                "example": ["✓ Good.", "✗ Bad."],
            },
        }

        with patch("agents.quiz.corrector._call_corrector_llm") as mock_llm, patch("agents.quiz.corrector._get_rag_context_for_correction") as mock_rag:

            mock_llm.return_value = mock_result
            mock_rag.return_value = "[Topic: Present Tenses]"

            from agents.quiz.corrector import run_corrector

            result = run_corrector(**self.BASE_ARGS)

        assert "is_correct" in result
        assert "is_graded" in result
        assert "feedback" in result
