"""
agents/quiz_tutor/corrector.py
-------------------------------
Grammar Tutor Corrector Agent.

Model     : claude-sonnet (SONNET_MODEL) — reasoning mendalam dibutuhkan
            untuk menilai jawaban isian secara konseptual, bukan string matching.

Perbedaan utama dari TOEFL Quiz Corrector:
- Penilaian 3-tier (bukan binary benar/salah):
    full_credit    → score 1.0  — konsep benar, terminologi tepat, lengkap
    partial_credit → score 0.5  — konsep benar tapi kurang lengkap ATAU
                                  terminologi berbeda namun maknanya setara
    no_credit      → score 0.0  — konsep salah atau jawaban tidak relevan
- Output menggunakan `credit_level` dan `score`, bukan `is_correct`
- Feedback 3 layer: verdict, concept_rule, memory_tip
  (bukan 4 layer seperti TOEFL Quiz yang menyertakan example sentences)

Error handling:
- @retry_llm     : max 3x retry dengan exponential backoff (2s → 4s → 8s)
- RAG failure    : fallback ke nama topik, sesi tetap jalan
- Semua retry habis : is_graded=False, sesi tetap jalan, log error
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import SONNET_MODEL
from modules.rag.retriever import format_context_for_prompt, retrieve
from prompts.quiz_tutor.corrector_prompt import (
    TUTOR_CORRECTOR_SYSTEM_PROMPT,
    build_tutor_corrector_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm

load_dotenv()

_client: Optional[anthropic.Anthropic] = None
_rag_cache: dict[str, str] = {}  # Cache RAG: satu retrieve per topik per sesi


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _get_rag_context_for_correction(topic: str) -> str:
    """
    Retrieve materi referensi untuk topik soal yang sedang dikoreksi.

    Hasil di-cache per topik — jika topik yang sama muncul di beberapa
    soal dalam satu sesi, retrieve ke ChromaDB hanya terjadi sekali.

    Args:
        topic: Nama topik grammar soal yang akan dikoreksi.

    Returns:
        String context materi grammar, atau nama topik jika RAG gagal.
    """
    if topic in _rag_cache:
        logger.debug(f"[tutor_corrector] RAG cache hit for '{topic}'")
        return _rag_cache[topic]

    try:
        result = retrieve(query=topic, topic=topic)
        chunks = format_context_for_prompt(result)
        context = chunks if chunks else f"[Topic: {topic}]"
    except Exception as e:
        logger.warning(
            f"[tutor_corrector] RAG retrieve failed for '{topic}': {e} "
            f"— using topic name as fallback"
        )
        context = f"[Topic: {topic}]"

    _rag_cache[topic] = context
    return context


def _parse_corrector_response(raw: str) -> dict:
    """
    Parse dan validasi JSON response dari Tutor Corrector.

    Berbeda dari Quiz Corrector: validasi field top-level menggunakan
    credit_level dan score (bukan is_correct), dan feedback hanya
    3 layer: verdict, concept_rule, memory_tip.

    Args:
        raw: String response mentah dari LLM.

    Returns:
        Dict terstruktur hasil penilaian Corrector.

    Raises:
        ValueError jika struktur tidak valid atau field wajib hilang.
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    # Validasi field top-level
    required_top = {"credit_level", "score", "is_graded", "feedback"}
    missing = required_top - set(parsed.keys())
    if missing:
        raise ValueError(
            f"Corrector response missing top-level fields: {missing}"
        )

    # Validasi 3 layer feedback
    feedback = parsed.get("feedback", {})
    required_feedback = {"verdict", "concept_rule", "memory_tip"}
    missing_fb = required_feedback - set(feedback.keys())
    if missing_fb:
        raise ValueError(
            f"Corrector feedback missing layers: {missing_fb}"
        )

    # Validasi nilai credit_level
    valid_credit_levels = {"full_credit", "partial_credit", "no_credit"}
    credit_level = parsed.get("credit_level")
    if credit_level not in valid_credit_levels:
        raise ValueError(
            f"Invalid credit_level '{credit_level}'. "
            f"Must be one of: {valid_credit_levels}"
        )

    return parsed


@retry_llm
def _call_corrector_llm(
    topic: str,
    question_type: str,
    question_text: str,
    reference_answer: str,
    user_answer: str,
    rag_context: str,
) -> dict:
    """
    Panggil Claude Sonnet untuk menilai jawaban user dengan sistem 3-tier.
    Di-wrap @retry_llm: max 3x retry, exponential backoff.

    Args:
        topic           : Topik grammar soal yang dinilai.
        question_type   : Tipe soal (type_1_recall s/d type_6_reason).
        question_text   : Teks pertanyaan yang ditampilkan ke user.
        reference_answer: Jawaban acuan dari Generator sebagai patokan.
        user_answer     : Jawaban yang diinput user.
        rag_context     : Materi referensi dari ChromaDB untuk topik ini.

    Returns:
        Dict hasil penilaian dengan credit_level, score, is_graded, feedback.
    """
    user_prompt = build_tutor_corrector_prompt(
        topic=topic,
        question_type=question_type,
        question_text=question_text,
        reference_answer=reference_answer,
        user_answer=user_answer,
        rag_context=rag_context,
    )

    response = _get_client().messages.create(
        model=SONNET_MODEL,
        max_tokens=1024,
        system=TUTOR_CORRECTOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_corrector_response(raw)


def run_corrector(
    topic: str,
    question_type: str,
    question_text: str,
    reference_answer: str,
    user_answer: str,
    session_id: Optional[str] = None,
) -> dict:
    """
    Jalankan Tutor Corrector Agent.

    Menilai jawaban user menggunakan sistem 3-tier: full_credit (1.0),
    partial_credit (0.5), atau no_credit (0.0). Penilaian berbasis
    LLM reasoning — bukan string matching — karena jawaban bisa benar
    secara konsep meski terminologinya berbeda.

    Jika LLM gagal setelah 3x retry, return fallback dict dengan
    is_graded=False agar sesi tetap bisa berjalan.

    Args:
        topic           : Topik grammar soal yang dinilai.
        question_type   : Tipe soal (type_1_recall s/d type_6_reason).
        question_text   : Teks pertanyaan yang ditampilkan ke user.
        reference_answer: Jawaban acuan dari Generator.
        user_answer     : Jawaban yang diinput user.
        session_id      : ID sesi untuk logging (opsional).

    Returns:
        dict: {
            "credit_level" : "full_credit" | "partial_credit" | "no_credit",
            "score"        : 1.0 | 0.5 | 0.0,
            "is_graded"    : bool,
            "feedback"     : {
                "verdict"      : str,
                "concept_rule" : str,
                "memory_tip"   : str,
            }
        }
    """
    logger.info(
        f"[tutor_corrector] Correcting topic='{topic}' "
        f"question_type='{question_type}'"
    )

    # Retrieve RAG context — hasil di-cache per topik
    rag_context = _get_rag_context_for_correction(topic)

    try:
        result = _call_corrector_llm(
            topic=topic,
            question_type=question_type,
            question_text=question_text,
            reference_answer=reference_answer,
            user_answer=user_answer,
            rag_context=rag_context,
        )
        logger.info(
            f"[tutor_corrector] Done — credit_level='{result.get('credit_level')}' "
            f"score={result.get('score')}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="tutor_corrector",
            session_id=session_id,
            context={
                "topic": topic,
                "question_type": question_type,
                "error": str(e),
            },
            fallback_used=True,
        )
        logger.warning(
            f"[tutor_corrector] Failed after 3 retries for topic='{topic}' "
            f"— marking as ungraded"
        )

        # Sesi tetap jalan — soal ini ditandai ungraded, tidak masuk kalkulasi skor
        return {
            "credit_level": "no_credit",
            "score": 0.0,
            "is_graded": False,
            "feedback": {
                "verdict": "Maaf, terjadi kendala teknis saat menilai jawabanmu.",
                "concept_rule": "Soal ini ditandai sebagai 'belum dinilai' dan tidak mempengaruhi skor akhirmu.",
                "memory_tip": "-",
            },
        }
