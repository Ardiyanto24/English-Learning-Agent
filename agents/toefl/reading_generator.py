"""
agents/toefl/reading_generator.py
-----------------------------------
TOEFL Reading Generator Agent.

Two-step generation per passage:
  Step 1: generate_passage() → 400-450 kata, topik akademik
  Step 2: generate_questions(passage) → 6+ soal dengan tipe bervariasi

Kenapa dipisah:
  - Soal yang dibuat "setelah" passage jauh lebih natural
  - Menghindari LLM "merancang" passage agar soal mudah dibuat
  - Jika Step 1 gagal, tidak perlu retry Step 2 yang lebih mahal

Error handling:
  - Setiap step punya @retry_llm sendiri (max 3x masing-masing)
  - Jika satu passage gagal total: skip passage itu, log error
  - Jika semua passage gagal: raise RuntimeError (batalkan section)
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.toefl.reading_prompt import (
    READING_PASSAGE_SYSTEM_PROMPT,
    READING_QUESTIONS_SYSTEM_PROMPT,
    build_passage_prompt,
    build_questions_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL

load_dotenv()

# Tipe soal wajib per passage
REQUIRED_QUESTION_TYPES = {
    "main_idea", "factual", "negative_factual",
    "inference", "vocabulary_in_context", "pronoun_reference",
}

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_passage_response(raw: str) -> dict:
    """Parse dan validasi response Step 1 (passage)."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text  = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    if "passage" not in parsed or not parsed["passage"]:
        raise ValueError("Response missing 'passage'")
    if "title" not in parsed:
        raise ValueError("Response missing 'title'")

    word_count = len(parsed["passage"].split())
    if word_count < 400:
        raise ValueError(
            f"Passage too short: {word_count} words (minimum 400, target 400-450)"
        )

    # Simpan word count aktual
    parsed["word_count"] = word_count
    return parsed


def _parse_questions_response(raw: str, expected_count: int) -> list[dict]:
    """Parse dan validasi response Step 2 (soal)."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text  = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed    = json.loads(text)
    questions = parsed.get("questions", [])

    if not questions:
        raise ValueError("Response missing 'questions'")

    # Truncation dulu sebelum validasi — agar validasi mencerminkan
    # soal yang benar-benar akan dipakai, bukan seluruh list LLM
    questions = questions[:expected_count]

    # Cek semua tipe wajib terpenuhi (post-truncation)
    present_types = {q.get("question_type") for q in questions}
    missing_types = REQUIRED_QUESTION_TYPES - present_types
    if missing_types:
        raise ValueError(
            f"Missing required question types after truncation: {missing_types}"
        )

    # Validasi field per soal
    required = {"question_text", "options", "correct_answer", "question_type"}
    for i, q in enumerate(questions):
        missing = required - set(q.keys())
        if missing:
            raise ValueError(f"Question {i} missing fields: {missing}")
        if q.get("correct_answer") not in ("A", "B", "C", "D"):
            raise ValueError(
                f"Question {i} invalid correct_answer: {q.get('correct_answer')}"
            )

    return questions


# ── Step 1: Generate Passage ─────────────────────────────────────────────────
@retry_llm
def _generate_passage(
    passage_number: int,
    total_passages: int,
    used_domains:   list[str],
) -> dict:
    """Panggil LLM untuk generate satu passage."""
    user_prompt = build_passage_prompt(
        passage_number = passage_number,
        total_passages = total_passages,
        used_domains   = used_domains,
    )

    client = _get_client()
    response = client.messages.create(
        model      = SONNET_MODEL,
        max_tokens = 1024,
        system     = READING_PASSAGE_SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": user_prompt}],
    )

    return _parse_passage_response(response.content[0].text)


# ── Step 2: Generate Questions ────────────────────────────────────────────────
@retry_llm
def _generate_questions(
    passage_title:        str,
    passage_text:         str,
    questions_per_passage: int,
) -> list[dict]:
    """Panggil LLM untuk generate soal dari passage yang sudah ada."""
    user_prompt = build_questions_prompt(
        passage_title         = passage_title,
        passage_text          = passage_text,
        questions_per_passage = questions_per_passage,
    )

    client = _get_client()
    response = client.messages.create(
        model      = SONNET_MODEL,
        max_tokens = 2048,
        system     = READING_QUESTIONS_SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": user_prompt}],
    )

    return _parse_questions_response(
        response.content[0].text, questions_per_passage
    )


# ── Main: run_generator ───────────────────────────────────────────────────────
def run_generator(reading_dist: dict) -> dict:
    """
    Jalankan TOEFL Reading Generator.

    Menggunakan two-step per passage:
      1. generate_passage()
      2. generate_questions(passage)

    Args:
        reading_dist: Output Planner untuk section reading:
                      {total, passages, per_passage}

    Returns:
        dict: {
            "passages"        : [list of passage dicts],
            "total_questions" : int,
            "passages_generated": int,
        }

        Setiap passage dict: {
            "passage_id"    : int,
            "title"         : str,
            "topic_domain"  : str,
            "passage_text"  : str,
            "word_count"    : int,
            "questions"     : [list of question dicts],
        }

    Raises:
        RuntimeError jika semua passage gagal di-generate
    """
    passage_count        = reading_dist.get("passages", 5)
    questions_per_passage = reading_dist.get("per_passage", 10)

    logger.info(
        f"[reading_generator] Generating {passage_count} passages × "
        f"{questions_per_passage} questions each"
    )

    passages     = []
    used_domains: list[str] = []
    total_q      = 0

    for p_num in range(1, passage_count + 1):
        logger.info(
            f"[reading_generator] Passage {p_num}/{passage_count} — Step 1: passage"
        )

        # ── Step 1: Generate passage ──────────────────────────────────────
        try:
            passage_data = _generate_passage(
                passage_number = p_num,
                total_passages = passage_count,
                used_domains   = used_domains,
            )
        except Exception as e:
            log_error(
                error_type    = "llm_timeout",
                agent_name    = "reading_generator",
                context       = {"passage_num": p_num, "step": 1, "error": str(e)},
                fallback_used = False,
            )
            logger.error(
                f"[reading_generator] Passage {p_num} Step 1 failed — skipping"
            )
            continue  # Skip passage ini, coba yang berikutnya

        domain = passage_data.get("topic_domain", "")
        if domain:
            used_domains.append(domain)

        logger.info(
            f"[reading_generator] Passage {p_num} Step 1 done — "
            f"'{passage_data.get('title')}' ({passage_data.get('word_count')} words)"
        )

        # ── Step 2: Generate questions ────────────────────────────────────
        logger.info(
            f"[reading_generator] Passage {p_num}/{passage_count} — Step 2: questions"
        )
        try:
            questions = _generate_questions(
                passage_title         = passage_data["title"],
                passage_text          = passage_data["passage"],
                questions_per_passage = questions_per_passage,
            )
        except Exception as e:
            log_error(
                error_type    = "llm_timeout",
                agent_name    = "reading_generator",
                context       = {"passage_num": p_num, "step": 2, "error": str(e)},
                fallback_used = False,
            )
            logger.error(
                f"[reading_generator] Passage {p_num} Step 2 failed — skipping"
            )
            continue

        passages.append({
            "passage_id":   p_num,
            "title":        passage_data["title"],
            "topic_domain": passage_data.get("topic_domain", ""),
            "passage_text": passage_data["passage"],
            "word_count":   passage_data.get("word_count", 0),
            "questions":    questions,
        })
        total_q += len(questions)

        logger.info(
            f"[reading_generator] Passage {p_num} complete — "
            f"{len(questions)} questions"
        )

    if not passages:
        raise RuntimeError(
            "Reading Generator gagal: tidak ada passage yang berhasil di-generate"
        )

    logger.info(
        f"[reading_generator] Done — "
        f"{len(passages)}/{passage_count} passages, {total_q} total questions"
    )

    return {
        "passages":           passages,
        "total_questions":    total_q,
        "passages_generated": len(passages),
    }