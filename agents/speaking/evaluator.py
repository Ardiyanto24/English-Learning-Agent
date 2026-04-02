"""
agents/speaking/evaluator.py
------------------------------
Speaking Evaluator Agent.

Menilai KESELURUHAN performa user setelah sesi selesai.
Berbeda dari Assessor yang pakai sliding window — Evaluator
selalu menerima FULL transcript dari session state.

Kriteria dan bobot per sub-mode:
  prompted_response & conversation_practice:
    Grammar   : 50%
    Relevance : 50%

  oral_presentation:
    Grammar    : 25%
    Relevance  : 25%
    Vocabulary : 25%
    Structure  : 25%

Error handling:
  - @retry_llm max 3x
  - Setelah gagal semua: is_graded=False, sesi tetap tersimpan
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.speaking.evaluator_prompt import (
    SPEAKING_EVALUATOR_SYSTEM_PROMPT,
    build_evaluator_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL

load_dotenv()

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_evaluator_response(raw: str, sub_mode: str) -> dict:
    """
    Parse dan validasi JSON response dari Evaluator.
    Validasi field berbeda tergantung sub_mode.
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    # Field wajib untuk semua sub-mode
    required = {"grammar_score", "relevance_score", "final_score", "is_graded", "feedback"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Evaluator response missing fields: {missing}")

    # Field tambahan untuk oral_presentation
    if sub_mode == "oral_presentation":
        extra = {"vocabulary_score", "structure_score"}
        missing_extra = extra - set(parsed.keys())
        if missing_extra:
            raise ValueError(f"oral_presentation response missing fields: {missing_extra}")

    # Validasi skor dalam range 1-10
    score_fields = ["grammar_score", "relevance_score", "final_score"]
    if sub_mode == "oral_presentation":
        score_fields += ["vocabulary_score", "structure_score"]

    for field in score_fields:
        val = parsed.get(field, 0)
        if not isinstance(val, (int, float)) or not (1 <= val <= 10):
            raise ValueError(f"Score '{field}' must be between 1-10, got: {val}")

    # Validasi feedback punya field yang benar
    feedback = parsed.get("feedback", {})
    required_fb = {"grammar", "relevance", "overall"}
    if sub_mode == "oral_presentation":
        required_fb |= {"vocabulary", "structure"}
    missing_fb = required_fb - set(feedback.keys())
    if missing_fb:
        raise ValueError(f"Feedback missing fields: {missing_fb}")

    return parsed


def _calculate_final_score(parsed: dict, sub_mode: str) -> float:
    """
    Hitung ulang final_score sebagai weighted average.
    Ini memastikan konsistensi meskipun LLM memberi nilai
    final_score yang tidak tepat.
    """
    if sub_mode == "oral_presentation":
        weights = {
            "grammar_score": 0.25,
            "relevance_score": 0.25,
            "vocabulary_score": 0.25,
            "structure_score": 0.25,
        }
    else:
        weights = {
            "grammar_score": 0.50,
            "relevance_score": 0.50,
        }

    total = sum(parsed.get(field, 0) * weight for field, weight in weights.items())
    return round(max(1.0, min(total, 10.0)), 2)


@retry_llm
def _call_evaluator_llm(
    sub_mode: str,
    main_topic: str,
    prompt_text: str,
    full_transcript: list[dict],
) -> dict:
    """Panggil Claude Sonnet untuk evaluate full transcript."""
    user_prompt = build_evaluator_prompt(
        sub_mode=sub_mode,
        main_topic=main_topic,
        prompt_text=prompt_text,
        full_transcript=full_transcript,
    )

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=1024,
        system=SPEAKING_EVALUATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    parsed = _parse_evaluator_response(raw, sub_mode)

    # Override final_score dengan perhitungan kita sendiri
    # untuk memastikan weighted average yang konsisten
    parsed["final_score"] = _calculate_final_score(parsed, sub_mode)

    return parsed


def _ungraded_result(sub_mode: str) -> dict:
    """
    Hasil fallback ketika evaluasi gagal setelah semua retry.
    Sesi tetap tersimpan, hanya ditandai ungraded.
    """
    base = {
        "grammar_score": None,
        "relevance_score": None,
        "final_score": None,
        "is_graded": False,
        "feedback": {
            "grammar": "-",
            "relevance": "-",
            "overall": "Maaf, terjadi kendala teknis saat menilai sesi ini. Sesi tetap tersimpan.",
        },
    }
    if sub_mode == "oral_presentation":
        base["vocabulary_score"] = None
        base["structure_score"] = None
        base["feedback"]["vocabulary"] = "-"
        base["feedback"]["structure"] = "-"
    return base


def run_evaluator(
    sub_mode: str,
    main_topic: str,
    prompt_text: str,
    full_transcript: list[dict],
    session_id: Optional[str] = None,
) -> dict:
    """
    Jalankan Speaking Evaluator Agent.

    Args:
        sub_mode        : "prompted_response" | "conversation_practice" | "oral_presentation"
        main_topic      : Topik utama sesi
        prompt_text     : Prompt pembuka yang diberikan ke user
        full_transcript : Seluruh history dari session state:
                          [{"role": "ai"|"user", "text": str}, ...]
        session_id      : ID sesi untuk logging (opsional)

    Returns:
        dict untuk prompted_response / conversation_practice:
        {
            "grammar_score"  : float (1-10),
            "relevance_score": float (1-10),
            "final_score"    : float (1-10),
            "is_graded"      : bool,
            "feedback": {
                "grammar"  : str,
                "relevance": str,
                "overall"  : str,
            }
        }

        dict tambahan untuk oral_presentation:
        + "vocabulary_score": float (1-10),
        + "structure_score" : float (1-10),
        + feedback["vocabulary"]: str,
        + feedback["structure"] : str,
    """
    exchange_count = len([t for t in full_transcript if t.get("role") == "user"])

    logger.info(
        f"[speaking_evaluator] Evaluating — "
        f"sub_mode={sub_mode} exchanges={exchange_count} "
        f"topic='{main_topic}'"
    )

    # Minimal 1 exchange user untuk bisa dinilai
    user_turns = [t for t in full_transcript if t.get("role") == "user"]
    if not user_turns:
        logger.warning("[speaking_evaluator] No user turns found in transcript")
        return _ungraded_result(sub_mode)

    try:
        result = _call_evaluator_llm(
            sub_mode=sub_mode,
            main_topic=main_topic,
            prompt_text=prompt_text,
            full_transcript=full_transcript,
        )

        logger.info(
            f"[speaking_evaluator] Done — "
            f"final_score={result.get('final_score')} "
            f"grammar={result.get('grammar_score')} "
            f"relevance={result.get('relevance_score')}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="speaking_evaluator",
            session_id=session_id,
            context={
                "sub_mode": sub_mode,
                "exchange_count": exchange_count,
                "error": str(e),
            },
            fallback_used=True,
        )
        logger.warning("[speaking_evaluator] Failed after 3 retries — marking session as ungraded")
        return _ungraded_result(sub_mode)
