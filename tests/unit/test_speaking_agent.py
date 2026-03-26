"""
tests/unit/test_speaking_agent.py
-----------------------------------
Unit test untuk Speaking Agent — berdasarkan kode asli di agents/speaking/.

Yang ditest:
  Assessor : sliding window max 5 entry, window mulai dari role AI,
             hard stop tanpa LLM, Phase 1 guard override stop → new_subtopic,
             fallback decision saat LLM gagal, output fields selalu ada
  Evaluator: weighted average prompted_response (50/50),
             weighted average oral_presentation (25/25/25/25),
             _calculate_final_score konsisten override LLM,
             is_graded=False saat LLM gagal,
             transcript kosong → ungraded langsung,
             oral_presentation punya extra fields

Semua test TIDAK memanggil LLM sungguhan — semua di-mock.
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


def _history(n_exchanges: int) -> list[dict]:
    """Buat n pasang AI-user exchange."""
    h = []
    for i in range(n_exchanges):
        h.append({"role": "ai",   "text": f"AI turn {i+1}"})
        h.append({"role": "user", "text": f"User response {i+1}"})
    return h


# ===================================================
# Test: Speaking Assessor
# ===================================================
class TestSpeakingAssessor:

    # ── Sliding Window ──────────────────────────────
    def test_short_history_returned_unchanged(self):
        """
        History <= WINDOW_SIZE dikembalikan utuh tanpa dipotong.
        Tidak ada informasi yang hilang pada history pendek.
        """
        from agents.speaking.assessor import WINDOW_SIZE, _build_sliding_window

        short = _history(2)   # 4 entries < WINDOW_SIZE (5)
        result = _build_sliding_window(short)

        assert len(result) == len(short)

    def test_long_history_trimmed_to_window_size(self):
        """
        History > WINDOW_SIZE harus dipotong.
        Hanya bagian akhir yang dikirim ke LLM untuk hemat token.
        """
        from agents.speaking.assessor import WINDOW_SIZE, _build_sliding_window

        long = _history(10)   # 20 entries >> WINDOW_SIZE
        result = _build_sliding_window(long)

        # Maksimal WINDOW_SIZE + 1 (buffer jika entry pertama di-trim)
        assert len(result) <= WINDOW_SIZE + 1

    def test_window_does_not_start_with_user_role(self):
        """
        Window tidak boleh dimulai dari role 'user'.
        Kalau entry pertama adalah user, harus di-trim 1 entry lagi
        agar context AI tetap ada sebagai pembuka.
        """
        from agents.speaking.assessor import _build_sliding_window

        # Buat history yang panjang agar pasti di-trim
        history = _history(8)   # 16 entries
        result  = _build_sliding_window(history)

        # Entry pertama harus role 'ai', bukan 'user'
        if len(result) > 0:
            assert result[0]["role"] == "ai"

    def test_window_preserves_latest_exchanges(self):
        """
        Window harus mengandung exchange TERBARU (akhir history),
        bukan exchange lama yang sudah tidak relevan.
        """
        from agents.speaking.assessor import _build_sliding_window

        history = _history(10)   # 20 entries
        result  = _build_sliding_window(history)

        # Entry terakhir window harus sama dengan entry terakhir history
        assert result[-1] == history[-1]

    # ── Hard Limits — tanpa LLM ────────────────────
    def test_prompted_response_hard_stop_at_max(self):
        """
        prompted_response: exchange_count >= PROMPTED_RESPONSE_MAX (3)
        → hard stop LANGSUNG tanpa memanggil LLM sama sekali.
        """
        from agents.speaking.assessor import (
            PROMPTED_RESPONSE_MAX,
            _check_hard_limits,
        )

        result = _check_hard_limits("prompted_response", PROMPTED_RESPONSE_MAX)

        assert result is not None
        assert result["decision"] == "stop"

    def test_conversation_practice_hard_stop_at_15(self):
        """
        conversation_practice: exchange_count >= CONVERSATION_HARD_STOP (15)
        → hard stop LANGSUNG tanpa memanggil LLM.
        """
        from agents.speaking.assessor import (
            CONVERSATION_HARD_STOP,
            _check_hard_limits,
        )

        result = _check_hard_limits("conversation_practice", CONVERSATION_HARD_STOP)

        assert result is not None
        assert result["decision"] == "stop"

    def test_hard_stop_returns_none_below_limit(self):
        """
        Di bawah batas: _check_hard_limits harus return None
        (bukan stop) agar LLM tetap dipanggil untuk assess.
        """
        from agents.speaking.assessor import _check_hard_limits

        # prompted_response belum di batas
        assert _check_hard_limits("prompted_response", 1)     is None
        assert _check_hard_limits("prompted_response", 2)     is None

        # conversation_practice belum di batas
        assert _check_hard_limits("conversation_practice", 5)  is None
        assert _check_hard_limits("conversation_practice", 14) is None

    def test_hard_stop_skips_llm_call(self):
        """
        Saat hard limit tercapai, run_assessor tidak boleh
        memanggil LLM sama sekali — langsung return stop.
        """
        from agents.speaking.assessor import PROMPTED_RESPONSE_MAX

        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            from agents.speaking.assessor import run_assessor
            result = run_assessor(
                sub_mode          = "prompted_response",
                exchange_count    = PROMPTED_RESPONSE_MAX,   # tepat di batas
                full_history      = _history(3),
                main_topic        = "Daily routines",
                latest_transcript = "I wake up at 7 AM.",
            )

        # LLM tidak boleh dipanggil
        mock_llm.assert_not_called()
        assert result["decision"] == "stop"

    # ── Phase 1 Guard ──────────────────────────────
    def test_phase1_stop_overridden_to_new_subtopic(self):
        """
        conversation_practice dengan exchange_count < 10 (Fase 1):
        Jika LLM return 'stop', harus di-override ke 'new_subtopic'.

        Ini mencegah conversation berakhir terlalu cepat sebelum
        10 exchange minimum terpenuhi.
        """
        stop_resp = {
            "decision":           "stop",
            "reason":             "Conversation closed naturally.",
            "suggested_followup": None,
        }

        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            mock_llm.return_value = stop_resp

            from agents.speaking.assessor import (
                CONVERSATION_PHASE2_MIN,
                run_assessor,
            )
            result = run_assessor(
                sub_mode          = "conversation_practice",
                exchange_count    = CONVERSATION_PHASE2_MIN - 1,   # masih Fase 1
                full_history      = _history(CONVERSATION_PHASE2_MIN - 1),
                main_topic        = "Technology",
                latest_transcript = "I love smartphones.",
            )

        # Harus di-override, bukan stop
        assert result["decision"] == "new_subtopic"

    def test_phase2_allows_stop_decision(self):
        """
        conversation_practice dengan exchange_count >= 10 (Fase 2):
        Keputusan 'stop' dari LLM boleh diteruskan apa adanya.
        """
        stop_resp = {
            "decision":           "stop",
            "reason":             "Conversation naturally concluded.",
            "suggested_followup": None,
        }

        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            mock_llm.return_value = stop_resp

            from agents.speaking.assessor import (
                CONVERSATION_PHASE2_MIN,
                run_assessor,
            )
            result = run_assessor(
                sub_mode          = "conversation_practice",
                exchange_count    = CONVERSATION_PHASE2_MIN,   # tepat di Fase 2
                full_history      = _history(CONVERSATION_PHASE2_MIN),
                main_topic        = "Technology",
                latest_transcript = "That concludes my thoughts.",
            )

        # Stop boleh diteruskan di Fase 2
        assert result["decision"] == "stop"

    # ── Fallback saat LLM Gagal ────────────────────
    def test_llm_failure_prompted_response_fallback_continue(self):
        """
        prompted_response + LLM gagal + belum di hard limit
        → fallback: 'continue' (conversation tetap jalan).
        """
        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            mock_llm.side_effect = Exception("Network error")

            from agents.speaking.assessor import run_assessor
            result = run_assessor(
                sub_mode          = "prompted_response",
                exchange_count    = 1,
                full_history      = _history(1),
                main_topic        = "Health",
                latest_transcript = "Exercise is important.",
            )

        assert result["decision"] == "continue"

    def test_llm_failure_conversation_phase1_fallback_new_subtopic(self):
        """
        conversation_practice + Fase 1 + LLM gagal
        → fallback: 'new_subtopic' (bukan stop).
        """
        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            mock_llm.side_effect = Exception("API down")

            from agents.speaking.assessor import (
                CONVERSATION_PHASE2_MIN,
                run_assessor,
            )
            result = run_assessor(
                sub_mode          = "conversation_practice",
                exchange_count    = 5,   # Fase 1
                full_history      = _history(5),
                main_topic        = "Environment",
                latest_transcript = "We should recycle more.",
            )

        assert result["decision"] == "new_subtopic"

    def test_llm_failure_conversation_phase2_fallback_stop(self):
        """
        conversation_practice + Fase 2 + LLM gagal
        → fallback: 'stop' (aman untuk berhenti di Fase 2).
        """
        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            mock_llm.side_effect = Exception("Timeout")

            from agents.speaking.assessor import (
                CONVERSATION_PHASE2_MIN,
                run_assessor,
            )
            result = run_assessor(
                sub_mode          = "conversation_practice",
                exchange_count    = CONVERSATION_PHASE2_MIN + 1,   # Fase 2
                full_history      = _history(CONVERSATION_PHASE2_MIN + 1),
                main_topic        = "Environment",
                latest_transcript = "That's my final point.",
            )

        assert result["decision"] == "stop"

    # ── Output Structure ───────────────────────────
    def test_output_always_has_required_fields(self):
        """
        Output run_assessor selalu punya decision, reason, suggested_followup
        — baik saat LLM sukses maupun gagal.
        """
        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            mock_llm.return_value = {
                "decision":           "continue",
                "reason":             "Answer needs elaboration.",
                "suggested_followup": "Can you tell me more?",
            }

            from agents.speaking.assessor import run_assessor
            result = run_assessor(
                sub_mode          = "prompted_response",
                exchange_count    = 1,
                full_history      = _history(1),
                main_topic        = "Daily life",
                latest_transcript = "I usually cook at home.",
            )

        assert "decision"           in result
        assert "reason"             in result
        assert "suggested_followup" in result

    def test_valid_decisions_only(self):
        """
        decision harus salah satu dari:
        'continue' | 'stop' | 'new_subtopic'
        """
        with patch("agents.speaking.assessor._call_assessor_llm") as mock_llm:
            mock_llm.return_value = {
                "decision":           "continue",
                "reason":             "Keep going.",
                "suggested_followup": None,
            }

            from agents.speaking.assessor import run_assessor
            result = run_assessor(
                sub_mode          = "prompted_response",
                exchange_count    = 1,
                full_history      = _history(1),
                main_topic        = "Sports",
                latest_transcript = "I play badminton.",
            )

        valid = {"continue", "stop", "new_subtopic"}
        assert result["decision"] in valid


# ===================================================
# Test: Speaking Evaluator
# ===================================================
class TestSpeakingEvaluator:

    # Transcript minimal yang valid
    TRANSCRIPT = [
        {"role": "ai",   "text": "Tell me about your daily routine."},
        {"role": "user", "text": "I wake up at 7 AM and exercise."},
    ]

    # ── _calculate_final_score (pure function, no mock needed) ──
    def test_weighted_average_prompted_response_50_50(self):
        """
        prompted_response: grammar 50% + relevance 50%.
        Fungsi ini tidak bergantung LLM — murni aritmatika Python.
        """
        from agents.speaking.evaluator import _calculate_final_score

        parsed = {"grammar_score": 8.0, "relevance_score": 6.0}
        score  = _calculate_final_score(parsed, "prompted_response")

        assert score == pytest.approx(7.0, abs=0.01)

    def test_weighted_average_conversation_practice_50_50(self):
        """
        conversation_practice: bobot sama dengan prompted_response (50/50).
        """
        from agents.speaking.evaluator import _calculate_final_score

        parsed = {"grammar_score": 5.0, "relevance_score": 9.0}
        score  = _calculate_final_score(parsed, "conversation_practice")

        assert score == pytest.approx(7.0, abs=0.01)

    def test_weighted_average_oral_presentation_25_each(self):
        """
        oral_presentation: grammar + relevance + vocabulary + structure
        masing-masing 25% (4 kriteria equal weight).
        """
        from agents.speaking.evaluator import _calculate_final_score

        parsed = {
            "grammar_score":    8.0,
            "relevance_score":  6.0,
            "vocabulary_score": 7.0,
            "structure_score":  9.0,
        }
        score    = _calculate_final_score(parsed, "oral_presentation")
        expected = (8.0 + 6.0 + 7.0 + 9.0) / 4   # = 7.5

        assert score == pytest.approx(expected, abs=0.01)

    def test_final_score_clamped_to_1_10(self):
        """
        final_score harus selalu dalam range 1.0–10.0.
        Bahkan jika skor input ekstrem, hasil harus di-clamp.
        """
        from agents.speaking.evaluator import _calculate_final_score

        # Skor sangat rendah
        parsed_low = {"grammar_score": 1.0, "relevance_score": 1.0}
        score_low  = _calculate_final_score(parsed_low, "prompted_response")
        assert score_low >= 1.0

        # Skor sangat tinggi
        parsed_high = {"grammar_score": 10.0, "relevance_score": 10.0}
        score_high  = _calculate_final_score(parsed_high, "prompted_response")
        assert score_high <= 10.0

    def test_calculate_overrides_llm_final_score(self):
        """
        _calculate_final_score selalu menimpa nilai final_score dari LLM.
        Ini memastikan konsistensi — LLM tidak bisa memberikan skor
        yang tidak konsisten dengan komponen-komponennya.

        Contoh: grammar=8, relevance=6 → final HARUS 7.0,
        bukan 9.0 meskipun LLM bilang 9.0.
        """
        from agents.speaking.evaluator import _calculate_final_score

        # LLM "bilang" final_score=9.0, tapi komponennya 8+6=14/2=7.0
        parsed = {
            "grammar_score":   8.0,
            "relevance_score": 6.0,
            "final_score":     9.0,   # nilai dari LLM yang "salah"
        }
        corrected = _calculate_final_score(parsed, "prompted_response")

        # Harus 7.0, bukan 9.0
        assert corrected == pytest.approx(7.0, abs=0.01)

    # ── run_evaluator ──────────────────────────────
    def test_successful_evaluation_is_graded_true(self):
        """LLM sukses → is_graded=True dan final_score ada."""
        mock_result = {
            "grammar_score":   8.0,
            "relevance_score": 7.0,
            "final_score":     7.5,
            "is_graded":       True,
            "feedback": {
                "grammar":   "Good grammar.",
                "relevance": "Stayed on topic.",
                "overall":   "Well done!",
            },
        }

        with patch("agents.speaking.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = mock_result

            from agents.speaking.evaluator import run_evaluator
            result = run_evaluator(
                sub_mode        = "prompted_response",
                main_topic      = "Daily routines",
                prompt_text     = "Tell me about your morning routine.",
                full_transcript = self.TRANSCRIPT,
            )

        assert result["is_graded"]  is True
        assert result["final_score"] is not None

    def test_llm_failure_returns_is_graded_false(self):
        """
        LLM gagal setelah 3x retry → is_graded=False.
        Sesi TETAP TERSIMPAN — user tidak kehilangan data.
        """
        with patch("agents.speaking.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.side_effect = Exception("API unavailable")

            from agents.speaking.evaluator import run_evaluator
            result = run_evaluator(
                sub_mode        = "prompted_response",
                main_topic      = "Health",
                prompt_text     = "What do you do to stay healthy?",
                full_transcript = self.TRANSCRIPT,
            )

        assert result["is_graded"]   is False
        assert result["final_score"] is None

    def test_empty_transcript_returns_ungraded(self):
        """
        Transcript tanpa giliran user sama sekali → langsung ungraded.
        Ini edge case: sesi dibuat tapi user tidak pernah bicara.
        """
        with patch("agents.speaking.evaluator._call_evaluator_llm") as mock_llm:
            from agents.speaking.evaluator import run_evaluator
            result = run_evaluator(
                sub_mode        = "prompted_response",
                main_topic      = "Travel",
                prompt_text     = "Tell me about your dream destination.",
                full_transcript = [],   # kosong
            )

        # LLM tidak boleh dipanggil untuk transcript kosong
        mock_llm.assert_not_called()
        assert result["is_graded"] is False

    def test_oral_presentation_has_extra_score_fields(self):
        """
        oral_presentation harus punya vocabulary_score dan structure_score
        sebagai tambahan dari grammar dan relevance.
        """
        mock_result = {
            "grammar_score":    7.0,
            "relevance_score":  8.0,
            "vocabulary_score": 6.0,
            "structure_score":  9.0,
            "final_score":      7.5,
            "is_graded":        True,
            "feedback": {
                "grammar":    "Good grammar.",
                "relevance":  "On topic.",
                "vocabulary": "Rich vocabulary.",
                "structure":  "Well structured.",
                "overall":    "Excellent presentation!",
            },
        }

        with patch("agents.speaking.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = mock_result

            from agents.speaking.evaluator import run_evaluator
            result = run_evaluator(
                sub_mode        = "oral_presentation",
                main_topic      = "Climate change",
                prompt_text     = "Give a 3-minute presentation.",
                full_transcript = self.TRANSCRIPT,
            )

        assert "vocabulary_score" in result
        assert "structure_score"  in result

    def test_oral_presentation_ungraded_has_extra_none_fields(self):
        """
        oral_presentation yang ungraded (LLM gagal) harus punya
        vocabulary_score=None dan structure_score=None
        — bukan KeyError saat diakses UI.
        """
        with patch("agents.speaking.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.side_effect = Exception("LLM down")

            from agents.speaking.evaluator import run_evaluator
            result = run_evaluator(
                sub_mode        = "oral_presentation",
                main_topic      = "Education",
                prompt_text     = "Discuss the importance of education.",
                full_transcript = self.TRANSCRIPT,
            )

        assert result["is_graded"]        is False
        assert result["vocabulary_score"] is None
        assert result["structure_score"]  is None

    def test_feedback_fields_match_sub_mode(self):
        """
        prompted_response: feedback punya grammar, relevance, overall.
        oral_presentation: feedback tambah vocabulary, structure.
        """
        # Prompted response
        mock_prompted = {
            "grammar_score":   7.0,
            "relevance_score": 8.0,
            "final_score":     7.5,
            "is_graded":       True,
            "feedback": {
                "grammar":   "Good.",
                "relevance": "On topic.",
                "overall":   "Well done.",
            },
        }

        with patch("agents.speaking.evaluator._call_evaluator_llm") as mock_llm:
            mock_llm.return_value = mock_prompted

            from agents.speaking.evaluator import run_evaluator
            result = run_evaluator(
                sub_mode        = "prompted_response",
                main_topic      = "Food",
                prompt_text     = "Describe your favorite meal.",
                full_transcript = self.TRANSCRIPT,
            )

        fb = result["feedback"]
        assert "grammar"   in fb
        assert "relevance" in fb
        assert "overall"   in fb
        # oral_presentation fields tidak boleh ada di prompted_response
        assert "vocabulary" not in fb
        assert "structure"  not in fb