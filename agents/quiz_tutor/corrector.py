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
