"""
agents/vocab/evaluator.py
--------------------------
Vocab Evaluator Agent.

Tugas: Nilai jawaban user secara kontekstual (bukan exact matching).
LLM sebagai juri — sinonim dan terjemahan ekuivalen dianggap benar.

Error handling:
- @retry_llm: max 3x retry
- Setelah 3x gagal: is_graded=False, log error, sesi tetap jalan

Input  : word, format, question_text, correct_answer, user_answer
Output : {"is_correct": bool, "is_graded": bool, "feedback": str}
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.vocab.evaluator_prompt import (
    EVALUATOR_SYSTEM_PROMPT,
    build_evaluator_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_evaluator_response(raw: str) -> dict:
    """Parse dan validasi JSON response dari LLM Evaluator."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    required = {"is_correct", "is_graded", "feedback"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Evaluator response missing fields: {missing}")

    return parsed


@retry_llm
def _call_evaluator_llm(
    word: str,
    format: str,
    question_text: str,
    correct_answer: str,
    user_answer: str,
) -> dict:
    """Panggil Claude Haiku sebagai contextual judge. Di-wrap @retry_llm."""
    user_prompt = build_evaluator_prompt(
        word=word,
        format=format,
        question_text=question_text,
        correct_answer=correct_answer,
        user_answer=user_answer,
    )

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=EVALUATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_evaluator_response(raw)


def run_evaluator(
    word: str,
    format: str,
    question_text: str,
    correct_answer: str,
    user_answer: str,
    session_id: Optional[str] = None,
) -> dict:
    """
    Jalankan Vocab Evaluator Agent.

    Args:
        word          : Kata yang dinilai. Contoh: "breakfast"
        format        : Format soal — "tebak_arti"|"sinonim_antonim"|"tebak_inggris"
        question_text : Teks soal yang ditampilkan ke user
        correct_answer: Jawaban benar dari Generator
        user_answer   : Jawaban yang diberikan user
        session_id    : ID sesi untuk logging (opsional)

    Returns:
        dict: {
            "is_correct": bool,
            "is_graded" : bool,  ← False jika LLM gagal setelah 3x
            "feedback"  : str,   ← Feedback dalam Bahasa Indonesia
        }
    """
    logger.info(
        f"[vocab_evaluator] Evaluating word='{word}' "
        f"format={format} answer='{user_answer}'"
    )

    try:
        result = _call_evaluator_llm(
            word=word,
            format=format,
            question_text=question_text,
            correct_answer=correct_answer,
            user_answer=user_answer,
        )
        logger.info(
            f"[vocab_evaluator] Result: is_correct={result['is_correct']}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="vocab_evaluator",
            session_id=session_id,
            context={
                "word": word,
                "format": format,
                "error": str(e),
            },
            fallback_used=True,
        )
        logger.warning(
            f"[vocab_evaluator] Failed after 3 retries for word='{word}' "
            f"— marking as ungraded"
        )
        # Sesi tetap jalan — tandai jawaban sebagai ungraded
        return {
            "is_correct": False,
            "is_graded": False,
            "feedback": (
                "Maaf, terjadi kendala teknis saat menilai jawabanmu. "
                "Jawaban ini ditandai sebagai 'belum dinilai' dan tidak "
                "mempengaruhi skor akhirmu."
            ),
        }