"""
tests/unit/test_vocab_agent.py
-------------------------------
Unit test untuk Vocab Agent — berdasarkan kode asli di agents/vocab/.

Yang ditest:
  Planner  : cold start default, topic override, LLM fallback, output format,
             format_distribution sum, difficulty logic
  Validator: match_score >= 0.8 langsung pass, < 0.8 trigger regenerate,
             is_adjusted=True setelah max retry, final_words selalu ada
  Evaluator: struktur output, is_graded=False saat LLM gagal,
             jawaban benar, jawaban salah

Semua test TIDAK memanggil LLM sungguhan — semua di-mock.
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ===================================================
# Helper: buat mock LLM response
# ===================================================
def _resp(text: str) -> MagicMock:
    """Buat mock object yang meniru struktur anthropic.Message."""
    m = MagicMock()
    m.content = [MagicMock(text=text)]
    return m


# ===================================================
# Test: Vocab Planner
# ===================================================
class TestVocabPlanner:

    def test_cold_start_returns_default_config(self):
        """
        Ketika DB kosong (cold start), Planner harus:
        - SKIP LLM call (hemat token)
        - Return DEFAULT_PLANNER_CONFIG persis
        - difficulty_target harus 'easy'
        - total_words=10, new_words=5, review_words=5
        """
        # Mock _build_history_summary agar return cold start
        with patch("agents.vocab.planner._build_history_summary") as mock_hist:
            mock_hist.return_value = {"is_cold_start": True}

            from agents.vocab.planner import run_planner

            result = run_planner(topic="sehari_hari")

        assert result["topic"] == "sehari_hari"
        assert result["difficulty_target"] == "easy"
        assert result["total_words"] == 10
        assert result["new_words"] == 5
        assert result["review_words"] == 5
        assert "format_distribution" in result

    def test_cold_start_topic_override(self):
        """
        Topic dari parameter harus menggantikan topic default
        di config, bahkan saat cold start.
        """
        with patch("agents.vocab.planner._build_history_summary") as mock_hist:
            mock_hist.return_value = {"is_cold_start": True}

            from agents.vocab.planner import run_planner

            result = run_planner(topic="di_kampus")

        assert result["topic"] == "di_kampus"

    def test_cold_start_skips_llm_call(self):
        """
        Saat cold start, _call_planner_llm tidak boleh dipanggil sama sekali.
        Ini penting untuk hemat token.
        """
        with patch("agents.vocab.planner._build_history_summary") as mock_hist, patch("agents.vocab.planner._call_planner_llm") as mock_llm:

            mock_hist.return_value = {"is_cold_start": True}

            from agents.vocab.planner import run_planner

            run_planner(topic="sehari_hari")

        # LLM tidak boleh dipanggil saat cold start
        mock_llm.assert_not_called()

    def test_llm_failure_fallback_to_default(self):
        """
        Jika LLM gagal setelah 3x retry, Planner harus fallback
        ke default config — tidak boleh raise exception ke UI.
        """
        with patch("agents.vocab.planner._build_history_summary") as mock_hist, patch("agents.vocab.planner._call_planner_llm") as mock_llm:

            mock_hist.return_value = {
                "is_cold_start": False,
                "current_difficulty": "easy",
                "avg_mastery_easy": 50.0,
                "avg_mastery_medium": -1,
                "avg_mastery_hard": -1,
                "weak_words_count": 3,
                "total_sessions": 2,
            }
            mock_llm.side_effect = Exception("LLM timeout")

            from agents.vocab.planner import run_planner

            result = run_planner(topic="perkenalan")

        # Fallback ke default — tidak raise exception
        assert result["difficulty_target"] == "easy"
        assert result["topic"] == "perkenalan"
        assert result["total_words"] == 10

    def test_output_has_all_required_fields(self):
        """
        Output planner harus punya semua field yang dibutuhkan
        oleh Generator dan Validator.
        """
        with patch("agents.vocab.planner._build_history_summary") as mock_hist:
            mock_hist.return_value = {"is_cold_start": True}

            from agents.vocab.planner import run_planner

            result = run_planner()

        required = {
            "topic",
            "total_words",
            "new_words",
            "review_words",
            "difficulty_target",
            "format_distribution",
        }
        assert required.issubset(set(result.keys()))

    def test_format_distribution_sums_to_total_words(self):
        """
        Jumlah semua nilai di format_distribution harus sama
        dengan total_words. Ini memastikan tidak ada soal yang
        kurang atau berlebih.
        """
        with patch("agents.vocab.planner._build_history_summary") as mock_hist:
            mock_hist.return_value = {"is_cold_start": True}

            from agents.vocab.planner import run_planner

            result = run_planner()

        dist_sum = sum(result["format_distribution"].values())
        assert dist_sum == result["total_words"]

    def test_difficulty_upgrade_at_80_pct(self):
        """
        Jika avg mastery easy >= 80%, difficulty harus naik ke 'medium'.
        """
        from agents.vocab.planner import _determine_current_difficulty

        avg_mastery = {"easy": 85.0, "medium": -1.0, "hard": -1.0}
        result = _determine_current_difficulty(avg_mastery)

        assert result == "medium"

    def test_difficulty_downgrade_below_40_pct(self):
        """
        Jika avg mastery medium < 40%, difficulty harus turun ke 'easy'.
        """
        from agents.vocab.planner import _determine_current_difficulty

        avg_mastery = {"easy": -1.0, "medium": 35.0, "hard": -1.0}
        result = _determine_current_difficulty(avg_mastery)

        assert result == "easy"

    def test_difficulty_stays_between_40_and_80(self):
        """
        Jika avg mastery 40%-79%, difficulty harus tetap di level saat ini.
        """
        from agents.vocab.planner import _determine_current_difficulty

        avg_mastery = {"easy": -1.0, "medium": 60.0, "hard": -1.0}
        result = _determine_current_difficulty(avg_mastery)

        assert result == "medium"


# ===================================================
# Test: Vocab Validator
# ===================================================
class TestVocabValidator:

    # Data sample untuk dipakai semua test Validator
    PLANNER = {
        "topic": "sehari_hari",
        "total_words": 2,
        "new_words": 1,
        "review_words": 1,
        "difficulty_target": "easy",
        "format_distribution": {"tebak_arti": 1, "tebak_inggris": 1},
    }

    GENERATOR = {
        "words": [
            {
                "word": "breakfast",
                "difficulty": "easy",
                "format": "tebak_arti",
                "question_text": "Apa arti 'breakfast'?",
                "correct_answer": "sarapan",
                "is_new": True,
            },
            {
                "word": "tidur",
                "difficulty": "easy",
                "format": "tebak_inggris",
                "question_text": "Bahasa Inggris dari 'tidur'?",
                "correct_answer": "sleep",
                "is_new": False,
            },
        ]
    }

    def test_valid_score_passes_immediately(self):
        """
        Jika match_score >= 0.8, Validator langsung return is_valid=True
        tanpa trigger regenerate. LLM hanya dipanggil 1x.
        """
        valid_resp = json.dumps(
            {
                "is_valid": True,
                "match_score": 0.95,
                "issues": [],
                "adjusted_words": [],
            }
        )

        with patch("agents.vocab.validator._call_validator_llm") as mock_llm:
            mock_llm.return_value = json.loads(valid_resp)

            from agents.vocab.validator import run_validator

            result = run_validator(self.PLANNER, self.GENERATOR)

        assert result["is_valid"] is True
        assert result["is_adjusted"] is False
        assert result["match_score"] >= 0.8
        # LLM hanya dipanggil 1x — tidak ada retry
        assert mock_llm.call_count == 1

    def test_invalid_score_triggers_regenerate(self):
        """
        Jika match_score < 0.8, Validator harus trigger run_generator
        untuk mendapatkan soal baru. Ini memastikan kualitas soal terjaga.
        """
        bad_resp = {
            "is_valid": False,
            "match_score": 0.5,
            "issues": ["format mismatch"],
            "adjusted_words": [],
        }

        with patch("agents.vocab.validator._call_validator_llm") as mock_llm, patch("agents.vocab.validator.run_generator") as mock_gen:

            mock_llm.return_value = bad_resp
            mock_gen.return_value = self.GENERATOR

            from agents.vocab.validator import run_validator

            run_validator(self.PLANNER, self.GENERATOR)

        # Generator harus dipanggil setelah validasi gagal
        assert mock_gen.call_count >= 1

    def test_max_retry_sets_is_adjusted_true(self):
        """
        Setelah MAX_REGENERATE_ATTEMPTS (3x) semua gagal,
        is_adjusted harus True — menandakan soal tidak sempurna
        tapi sesi tetap lanjut.
        """
        bad_resp = {
            "is_valid": False,
            "match_score": 0.3,
            "issues": ["bad quality"],
            "adjusted_words": [],
        }

        with patch("agents.vocab.validator._call_validator_llm") as mock_llm, patch("agents.vocab.validator.run_generator") as mock_gen:

            mock_llm.return_value = bad_resp
            mock_gen.return_value = self.GENERATOR

            from agents.vocab.validator import run_validator

            result = run_validator(self.PLANNER, self.GENERATOR)

        assert result["is_adjusted"] is True

    def test_final_words_always_present(self):
        """
        Output Validator selalu punya 'final_words' — bahkan saat
        validasi gagal total. Ini memastikan UI tidak crash.
        """
        with patch("agents.vocab.validator._call_validator_llm") as mock_llm, patch("agents.vocab.validator.run_generator") as mock_gen:

            mock_llm.side_effect = Exception("LLM down")
            mock_gen.return_value = self.GENERATOR

            from agents.vocab.validator import run_validator

            result = run_validator(self.PLANNER, self.GENERATOR)

        assert "final_words" in result
        assert isinstance(result["final_words"], list)

    def test_generator_failure_handled_gracefully(self):
        """
        Jika Generator raise RuntimeError saat retry,
        Validator tidak crash — fallback ke soal yang ada.
        """
        bad_resp = {
            "is_valid": False,
            "match_score": 0.5,
            "issues": [],
            "adjusted_words": [],
        }

        with patch("agents.vocab.validator._call_validator_llm") as mock_llm, patch("agents.vocab.validator.run_generator") as mock_gen:

            mock_llm.return_value = bad_resp
            mock_gen.side_effect = RuntimeError("Generator totally failed")

            from agents.vocab.validator import run_validator

            result = run_validator(self.PLANNER, self.GENERATOR)

        # Tidak crash, final_words tetap ada
        assert "final_words" in result
        assert len(result["final_words"]) > 0


# ===================================================
# Test: Vocab Evaluator
# ===================================================
class TestVocabEvaluator:

    def test_output_has_required_fields(self):
        """
        Output Evaluator harus selalu punya 3 field wajib:
        is_correct, is_graded, feedback.
        """
        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_correct": True,
                "is_graded": True,
                "feedback": "Benar! Sarapan adalah terjemahan yang tepat.",
            }

            from agents.vocab.evaluator import run_evaluator

            result = run_evaluator(
                word="breakfast",
                format="tebak_arti",
                question_text="Apa arti 'breakfast'?",
                correct_answer="sarapan",
                user_answer="sarapan",
            )

        assert "is_correct" in result
        assert "is_graded" in result
        assert "feedback" in result

    def test_llm_failure_returns_is_graded_false(self):
        """
        Jika LLM gagal setelah 3x retry, is_graded harus False
        dan is_correct False. Sesi TETAP JALAN — tidak raise exception.

        Ini adalah edge case paling kritis: user tidak boleh stuck
        hanya karena LLM evaluator down.
        """
        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.side_effect = Exception("LLM timeout")

            from agents.vocab.evaluator import run_evaluator

            result = run_evaluator(
                word="breakfast",
                format="tebak_arti",
                question_text="Apa arti 'breakfast'?",
                correct_answer="sarapan",
                user_answer="sarapan",
            )

        assert result["is_graded"] is False
        assert result["is_correct"] is False
        # Feedback harus ada dan mengandung kata "belum dinilai"
        assert "belum dinilai" in result["feedback"]

    def test_correct_answer_returns_is_correct_true(self):
        """Jawaban benar → is_correct=True, is_graded=True."""
        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_correct": True,
                "is_graded": True,
                "feedback": "Benar!",
            }

            from agents.vocab.evaluator import run_evaluator

            result = run_evaluator(
                word="sleep",
                format="tebak_arti",
                question_text="Apa arti 'sleep'?",
                correct_answer="tidur",
                user_answer="tidur",
            )

        assert result["is_correct"] is True
        assert result["is_graded"] is True

    def test_wrong_answer_returns_is_correct_false(self):
        """Jawaban salah → is_correct=False, is_graded=True."""
        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_correct": False,
                "is_graded": True,
                "feedback": "Kurang tepat. Sleep artinya tidur.",
            }

            from agents.vocab.evaluator import run_evaluator

            result = run_evaluator(
                word="sleep",
                format="tebak_arti",
                question_text="Apa arti 'sleep'?",
                correct_answer="tidur",
                user_answer="makan",
            )

        assert result["is_correct"] is False
        assert result["is_graded"] is True

    def test_feedback_is_string(self):
        """Feedback harus berupa string, bukan None atau tipe lain."""
        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_correct": True,
                "is_graded": True,
                "feedback": "Benar sekali!",
            }

            from agents.vocab.evaluator import run_evaluator

            result = run_evaluator(
                word="eat",
                format="tebak_arti",
                question_text="Apa arti 'eat'?",
                correct_answer="makan",
                user_answer="makan",
            )

        assert isinstance(result["feedback"], str)
        assert len(result["feedback"]) > 0
