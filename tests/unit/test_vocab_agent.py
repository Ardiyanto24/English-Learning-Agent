"""
tests/test_vocab_agents.py
---------------------------
Test struktur dan logika non-LLM dari 4 Vocab Agent.

Semua test di sini TIDAK memanggil LLM (mock/patch).
Test end-to-end dengan LLM dilakukan manual setelah Phase 2 selesai.

Jalankan: pytest tests/test_vocab_agents.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
from unittest.mock import MagicMock, patch

import pytest


# ===================================================
# Test: Planner — logika non-LLM
# ===================================================
class TestVocabPlanner:
    def test_cold_start_returns_default_config(self):
        """Cold start harus return default config tanpa LLM call."""
        from agents.vocab.planner import run_planner

        # Mock DB mengembalikan data kosong (cold start)
        with patch("agents.vocab.planner._build_history_summary") as mock_history:
            mock_history.return_value = {"is_cold_start": True}

            result = run_planner(topic="sehari_hari")

        assert result["topic"] == "sehari_hari"
        assert result["difficulty_target"] == "easy"
        assert result["total_words"] == 10
        assert result["new_words"] == 5
        assert result["review_words"] == 5
        assert "format_distribution" in result

    def test_cold_start_topic_override(self):
        """Topic dari input harus override default config topic."""
        from agents.vocab.planner import run_planner

        with patch("agents.vocab.planner._build_history_summary") as mock_history:
            mock_history.return_value = {"is_cold_start": True}
            result = run_planner(topic="di_kampus")

        assert result["topic"] == "di_kampus"

    def test_llm_failure_fallback_to_default(self):
        """Jika LLM gagal setelah retry, harus fallback ke default config."""
        from agents.vocab.planner import run_planner

        with (
            patch("agents.vocab.planner._build_history_summary") as mock_history,
            patch("agents.vocab.planner._call_planner_llm") as mock_llm,
        ):
            mock_history.return_value = {
                "is_cold_start": False,
                "current_difficulty": "easy",
                "avg_mastery_easy": 50.0,
                "avg_mastery_medium": -1,
                "avg_mastery_hard": -1,
                "weak_words_count": 3,
                "total_sessions": 2,
            }
            mock_llm.side_effect = Exception("LLM timeout")

            result = run_planner(topic="perkenalan")

        # Fallback ke default
        assert result["difficulty_target"] == "easy"
        assert result["topic"] == "perkenalan"

    def test_output_has_required_fields(self):
        """Output planner harus punya semua field yang dibutuhkan Generator."""
        from agents.vocab.planner import run_planner

        with patch("agents.vocab.planner._build_history_summary") as mock_history:
            mock_history.return_value = {"is_cold_start": True}
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

    def test_format_distribution_sums_to_total(self):
        """Sum format_distribution harus sama dengan total_words."""
        from agents.vocab.planner import run_planner

        with patch("agents.vocab.planner._build_history_summary") as mock_history:
            mock_history.return_value = {"is_cold_start": True}
            result = run_planner()

        dist_sum = sum(result["format_distribution"].values())
        assert dist_sum == result["total_words"]


# ===================================================
# Test: Generator — logika parsing
# ===================================================
class TestVocabGenerator:
    MOCK_PLANNER_OUTPUT = {
        "topic": "sehari_hari",
        "total_words": 2,
        "new_words": 1,
        "review_words": 1,
        "difficulty_target": "easy",
        "format_distribution": {"tebak_arti": 1, "tebak_inggris": 1},
    }

    MOCK_LLM_RESPONSE = json.dumps(
        {
            "words": [
                {
                    "word": "breakfast",
                    "difficulty": "easy",
                    "format": "tebak_arti",
                    "question_text": "Apa arti kata 'breakfast'?",
                    "correct_answer": "sarapan",
                    "is_new": True,
                },
                {
                    "word": "tidur",
                    "difficulty": "easy",
                    "format": "tebak_inggris",
                    "question_text": "Apa kata bahasa Inggris dari 'tidur'?",
                    "correct_answer": "sleep",
                    "is_new": False,
                },
            ]
        }
    )

    def test_generator_returns_words(self):
        """Generator harus return dict dengan key 'words'."""
        from agents.vocab.generator import run_generator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=self.MOCK_LLM_RESPONSE)]

        with patch("agents.vocab.generator._get_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_response
            result = run_generator(self.MOCK_PLANNER_OUTPUT)

        assert "words" in result
        assert len(result["words"]) == 2

    def test_generator_word_has_required_fields(self):
        """Setiap word harus punya semua field yang diperlukan."""
        from agents.vocab.generator import run_generator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text=self.MOCK_LLM_RESPONSE)]

        with patch("agents.vocab.generator._get_client") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_response
            result = run_generator(self.MOCK_PLANNER_OUTPUT)

        required = {
            "word",
            "difficulty",
            "format",
            "question_text",
            "correct_answer",
            "is_new",
        }
        for word in result["words"]:
            assert required.issubset(set(word.keys()))

    def test_generator_raises_on_persistent_failure(self):
        """Setelah 3x retry gagal, harus raise RuntimeError."""
        from agents.vocab.generator import run_generator

        with patch("agents.vocab.generator._call_generator_llm") as mock_llm:
            mock_llm.side_effect = Exception("API Error")

            with pytest.raises(RuntimeError, match="Vocab Generator gagal"):
                run_generator(self.MOCK_PLANNER_OUTPUT)

    def test_parse_handles_markdown_wrapper(self):
        """Parser harus bisa handle response yang dibungkus markdown."""
        from agents.vocab.generator import _parse_generator_response

        wrapped = f"```json\n{self.MOCK_LLM_RESPONSE}\n```"
        result = _parse_generator_response(wrapped)
        assert "words" in result


# ===================================================
# Test: Validator — logika validasi
# ===================================================
class TestVocabValidator:
    MOCK_PLANNER = {
        "topic": "sehari_hari",
        "total_words": 2,
        "new_words": 1,
        "review_words": 1,
        "difficulty_target": "easy",
        "format_distribution": {"tebak_arti": 1, "tebak_inggris": 1},
    }

    MOCK_GENERATOR = {
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
                "question_text": "Bahasa Inggris 'tidur'?",
                "correct_answer": "sleep",
                "is_new": False,
            },
        ]
    }

    def test_valid_output_returns_is_adjusted_false(self):
        """Jika valid, is_adjusted harus False."""
        from agents.vocab.validator import run_validator

        mock_validation = {
            "is_valid": True,
            "match_score": 1.0,
            "issues": [],
            "adjusted_words": [],
        }

        with patch("agents.vocab.validator._call_validator_llm") as mock_llm:
            mock_llm.return_value = mock_validation
            result = run_validator(self.MOCK_PLANNER, self.MOCK_GENERATOR)

        assert result["is_valid"] is True
        assert result["is_adjusted"] is False
        assert len(result["final_words"]) == 2

    def test_invalid_triggers_regenerate(self):
        """Jika match_score < 0.8, harus trigger regenerate."""
        from agents.vocab.validator import run_validator

        call_count = {"n": 0}

        def mock_validator(*args, **kwargs):
            call_count["n"] += 1
            return {
                "is_valid": False,
                "match_score": 0.5,
                "issues": ["format mismatch"],
                "adjusted_words": [],
            }

        with (
            patch(
                "agents.vocab.validator._call_validator_llm", side_effect=mock_validator
            ),
            patch("agents.vocab.validator.run_generator") as mock_gen,
        ):
            mock_gen.return_value = self.MOCK_GENERATOR
            result = run_validator(self.MOCK_PLANNER, self.MOCK_GENERATOR)

        # Harus attempt 3x total
        assert call_count["n"] == 3
        # Setelah 3x gagal, is_adjusted=True
        assert result["is_adjusted"] is True

    def test_validator_output_has_final_words(self):
        """Output validator selalu punya 'final_words'."""
        from agents.vocab.validator import run_validator

        with patch("agents.vocab.validator._call_validator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_valid": True,
                "match_score": 0.9,
                "issues": [],
                "adjusted_words": [],
            }
            result = run_validator(self.MOCK_PLANNER, self.MOCK_GENERATOR)

        assert "final_words" in result
        assert isinstance(result["final_words"], list)


# ===================================================
# Test: Evaluator — logika penilaian
# ===================================================
class TestVocabEvaluator:
    def test_evaluator_returns_correct_structure(self):
        """Evaluator harus return dict dengan 3 field wajib."""
        from agents.vocab.evaluator import run_evaluator

        mock_result = {
            "is_correct": True,
            "is_graded": True,
            "feedback": "Benar! Sarapan adalah terjemahan yang tepat.",
        }

        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = mock_result
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

    def test_evaluator_failure_returns_ungraded(self):
        """Jika LLM gagal setelah 3x, return is_graded=False."""
        from agents.vocab.evaluator import run_evaluator

        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.side_effect = Exception("LLM timeout")

            result = run_evaluator(
                word="breakfast",
                format="tebak_arti",
                question_text="Apa arti 'breakfast'?",
                correct_answer="sarapan",
                user_answer="sarapan",
            )

        assert result["is_graded"] is False
        assert result["is_correct"] is False
        assert "belum dinilai" in result["feedback"]

    def test_evaluator_correct_answer(self):
        """Test case jawaban benar."""
        from agents.vocab.evaluator import run_evaluator

        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_correct": True,
                "is_graded": True,
                "feedback": "Benar!",
            }
            result = run_evaluator(
                "sleep", "tebak_arti", "Apa arti sleep?", "tidur", "tidur"
            )

        assert result["is_correct"] is True
        assert result["is_graded"] is True

    def test_evaluator_wrong_answer(self):
        """Test case jawaban salah."""
        from agents.vocab.evaluator import run_evaluator

        with patch("agents.vocab.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = {
                "is_correct": False,
                "is_graded": True,
                "feedback": "Kurang tepat. Sleep artinya tidur.",
            }
            result = run_evaluator(
                "sleep", "tebak_arti", "Apa arti sleep?", "tidur", "makan"
            )

        assert result["is_correct"] is False
        assert result["is_graded"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])