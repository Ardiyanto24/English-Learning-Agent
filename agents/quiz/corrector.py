"""
agents/quiz/corrector.py
-------------------------
Quiz Corrector Agent.

Agent paling bernilai pedagogis di seluruh aplikasi.
Bukan sekadar menilai benar/salah — tapi mengajarkan KENAPA.

Menggunakan Claude Sonnet karena 4 lapisan feedback membutuhkan
reasoning grammar yang mendalam — Haiku tidak cukup andal.

4 Lapisan Feedback:
  1. Verdict      : Benar atau salah, langsung
  2. Explanation  : Kenapa — fokus konsep, bukan sekadar kasih jawaban
  3. Concept      : Rule grammar yang relevan "Ingat rule X: ..."
  4. Example      : ✓ kalimat benar / ✗ kalimat salah

Error handling:
- @retry_llm: max 3x retry
- Setelah 3x gagal: is_graded=False, sesi tetap jalan
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.quiz.corrector_prompt import (
    QUIZ_CORRECTOR_SYSTEM_PROMPT,
    build_corrector_prompt,
)
from modules.rag.retriever import retrieve, format_context_for_prompt
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


def _get_rag_context_for_correction(topic: str) -> str:
    """
    Retrieve materi referensi untuk topik soal yang sedang dikoreksi.

    Berbeda dari Generator yang retrieve di awal sesi,
    Corrector retrieve ulang per soal agar konteks akurat.

    Jika RAG gagal: gunakan nama topik sebagai fallback.
    """
    try:
        result = retrieve(query=topic, topic=topic)
        chunks = format_context_for_prompt(result)
        if chunks:
            return chunks
        return f"[Topic: {topic}]"
    except Exception as e:
        logger.warning(
            f"[quiz_corrector] RAG retrieve failed for '{topic}': {e} "
            f"— using topic name as fallback"
        )
        return f"[Topic: {topic}]"


def _parse_corrector_response(raw: str) -> dict:
    """Parse dan validasi JSON response dari Corrector."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    # Validasi field wajib
    required_top = {"is_correct", "is_graded", "feedback"}
    missing = required_top - set(parsed.keys())
    if missing:
        raise ValueError(f"Corrector response missing top-level fields: {missing}")

    # Validasi 4 lapisan feedback
    feedback = parsed.get("feedback", {})
    required_feedback = {"verdict", "explanation", "concept", "example"}
    missing_fb = required_feedback - set(feedback.keys())
    if missing_fb:
        raise ValueError(f"Corrector feedback missing layers: {missing_fb}")

    # Validasi example adalah list dengan 2 item
    example = feedback.get("example", [])
    if not isinstance(example, list) or len(example) < 2:
        raise ValueError(
            f"'example' must be a list with 2 items, got: {example}"
        )

    return parsed


@retry_llm
def _call_corrector_llm(
    topic: str,
    format: str,
    question_text: str,
    options: list,
    correct_answer: str,
    user_answer: str,
    rag_context: str,
) -> dict:
    """Panggil Claude Sonnet untuk generate 4 lapisan feedback."""
    user_prompt = build_corrector_prompt(
        topic=topic,
        format=format,
        question_text=question_text,
        options=options,
        correct_answer=correct_answer,
        user_answer=user_answer,
        rag_context=rag_context,
    )

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=1024,
        system=QUIZ_CORRECTOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_corrector_response(raw)


def run_corrector(
    topic: str,
    format: str,
    question_text: str,
    options: list,
    correct_answer: str,
    user_answer: str,
    session_id: Optional[str] = None,
) -> dict:
    """
    Jalankan Quiz Corrector Agent.

    Args:
        topic          : Topik grammar soal ini
        format         : Format soal (multiple_choice/error_id/fill_blank)
        question_text  : Teks soal
        options        : List pilihan ["A. ...", "B. ...", ...]
        correct_answer : Jawaban benar ("A"/"B"/"C"/"D")
        user_answer    : Jawaban yang dipilih user
        session_id     : ID sesi untuk logging (opsional)

    Returns:
        dict: {
            "is_correct" : bool,
            "is_graded"  : bool,
            "feedback"   : {
                "verdict"     : str,
                "explanation" : str,
                "concept"     : str,
                "example"     : [str, str],
            }
        }
    """
    logger.info(
        f"[quiz_corrector] Correcting topic='{topic}' "
        f"format={format} user_answer='{user_answer}'"
    )

    # Retrieve RAG context untuk topik ini
    rag_context = _get_rag_context_for_correction(topic)

    try:
        result = _call_corrector_llm(
            topic=topic,
            format=format,
            question_text=question_text,
            options=options,
            correct_answer=correct_answer,
            user_answer=user_answer,
            rag_context=rag_context,
        )

        logger.info(
            f"[quiz_corrector] Done — "
            f"is_correct={result.get('is_correct')}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="quiz_corrector",
            session_id=session_id,
            context={
                "topic":  topic,
                "format": format,
                "error":  str(e),
            },
            fallback_used=True,
        )
        logger.warning(
            f"[quiz_corrector] Failed after 3 retries for topic='{topic}' "
            f"— marking as ungraded"
        )

        # Sesi tetap jalan — tandai sebagai ungraded
        return {
            "is_correct": False,
            "is_graded":  False,
            "feedback": {
                "verdict":     "Maaf, terjadi kendala teknis saat menilai jawabanmu.",
                "explanation": "Soal ini ditandai sebagai 'belum dinilai' dan tidak mempengaruhi skor akhirmu.",
                "concept":     "-",
                "example":     ["-", "-"],
            },
        }