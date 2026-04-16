"""
agents/vocab/generator.py
--------------------------
Vocab Generator Agent.

Tugas: Generate soal vocab berdasarkan instruksi Planner.

Input  : output dari Planner Agent (dict)
Output : dict dengan key "words" berisi list soal vocab

Flow:
1. Ambil review words dari DB via spaced repetition (Python, bukan LLM)
2. Split format_distribution planner menjadi 2 bagian: new_formats & review_formats
3. LLM generate NEW words saja menggunakan new_formats
4. LLM enrich review words dari DB menggunakan review_formats
5. Gabung new words + enriched review words → return

Error handling:
- @retry_llm: max 3x retry untuk LLM call
- JSON parse error: retry sekali dengan instruksi lebih ketat
- Enrich gagal: skip review words, sesi tetap jalan dengan new words saja
- Setelah semua retry habis: raise exception → sesi dibatalkan
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from database.repositories.vocab_repository import get_spaced_repetition_words
from prompts.vocab.generator_prompt import (
    ENRICH_SYSTEM_PROMPT,
    GENERATOR_SYSTEM_PROMPT,
    build_enrich_prompt,
    build_generator_prompt,
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


# ──────────────────────────────────────────────────────────────────────────────
# Parse helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_generator_response(raw: str) -> dict:
    """
    Parse JSON response dari LLM Generator.
    Handle kasus LLM menambahkan markdown atau teks ekstra.

    Raises:
        ValueError jika JSON tidak valid atau struktur tidak sesuai
    """
    text = raw.strip()

    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    if "words" not in parsed:
        raise ValueError(f"Response missing 'words' key: {parsed}")
    if not isinstance(parsed["words"], list):
        raise ValueError(f"'words' must be a list, got: {type(parsed['words'])}")
    if len(parsed["words"]) == 0:
        raise ValueError("'words' list is empty")

    required_fields = {"word", "difficulty", "format", "question_text", "correct_answer", "is_new"}
    for i, word in enumerate(parsed["words"]):
        missing = required_fields - set(word.keys())
        if missing:
            raise ValueError(f"Word[{i}] missing fields: {missing}")

    return parsed


def _parse_enrich_response(raw: str) -> list:
    """
    Parse JSON response dari LLM Enrich (untuk review words).

    Returns:
        list of enriched word objects

    Raises:
        ValueError jika JSON tidak valid atau struktur tidak sesuai
    """
    text = raw.strip()

    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    if "words" not in parsed:
        raise ValueError(f"Enrich response missing 'words' key: {parsed}")
    if not isinstance(parsed["words"], list):
        raise ValueError(f"'words' must be a list, got: {type(parsed['words'])}")

    required_fields = {"word", "difficulty", "format", "question_text", "correct_answer", "is_new"}
    for i, word in enumerate(parsed["words"]):
        missing = required_fields - set(word.keys())
        if missing:
            raise ValueError(f"Enriched word[{i}] missing fields: {missing}")

    return parsed["words"]


# ──────────────────────────────────────────────────────────────────────────────
# Format distribution helper
# ──────────────────────────────────────────────────────────────────────────────

def _split_format_distribution(
    planner_format_dist: dict,
    new_words_count: int,
) -> tuple[dict, dict]:
    """
    Bagi format_distribution planner menjadi 2 bagian:
    - new_formats   : slot format khusus untuk new words (dikirim ke generator LLM)
    - review_formats: slot format khusus untuk review words (dikirim ke enrich LLM)

    format_distribution dari planner adalah untuk TOTAL kata (new + review).
    Fungsi ini mengambil new_words_count slot pertama untuk new words,
    sisanya otomatis menjadi jatah review words.

    Contoh:
        planner_format_dist = {tebak_arti: 3, sinonim_antonim: 1, tebak_inggris: 1}
        new_words_count = 1
        → new_formats    = {tebak_arti: 1}
        → review_formats = {tebak_arti: 2, sinonim_antonim: 1, tebak_inggris: 1}

    Contoh 2:
        planner_format_dist = {tebak_arti: 3, sinonim_antonim: 1, tebak_inggris: 1}
        new_words_count = 3
        → new_formats    = {tebak_arti: 3}
        → review_formats = {sinonim_antonim: 1, tebak_inggris: 1}

    Args:
        planner_format_dist: Format distribution dari planner (total new + review)
        new_words_count    : Jumlah new words yang harus di-generate LLM

    Returns:
        (new_formats, review_formats)
    """
    new_formats: dict = {}
    review_formats: dict = {}
    slots_left = new_words_count

    for fmt, count in planner_format_dist.items():
        if slots_left <= 0:
            # Semua slot new words sudah terpenuhi → sisanya untuk review
            review_formats[fmt] = count
        elif count <= slots_left:
            # Format ini seluruhnya masuk ke new words
            new_formats[fmt] = count
            slots_left -= count
        else:
            # Format ini dibagi: sebagian new, sebagian review
            new_formats[fmt] = slots_left
            review_formats[fmt] = count - slots_left
            slots_left = 0

    return new_formats, review_formats


# ──────────────────────────────────────────────────────────────────────────────
# LLM call helpers
# ──────────────────────────────────────────────────────────────────────────────

@retry_llm
def _call_generator_llm(
    planner_output: dict,
    new_words_count: int,
    new_format_distribution: dict,
) -> dict:
    """
    Panggil LLM untuk generate NEW words saja.
    Review words TIDAK disebutkan di sini — sudah diambil dari DB.

    Args:
        new_format_distribution: Format distribution KHUSUS untuk new words saja.
                                 Bukan format_distribution penuh dari planner.
    """
    user_prompt = build_generator_prompt(
        topic=planner_output["topic"],
        difficulty_target=planner_output["difficulty_target"],
        format_distribution=new_format_distribution,
        new_words=new_words_count,
    )

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=GENERATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_generator_response(raw)


@retry_llm
def _call_enrich_llm(
    review_words: list[dict],
    planner_output: dict,
    remaining_format_distribution: dict,
) -> list:
    """
    Panggil LLM untuk enrich review words dari DB.
    Review words dari DB hanya punya word + difficulty — perlu ditambah
    format, question_text, correct_answer agar valid di validator.
    """
    user_prompt = build_enrich_prompt(
        review_words=review_words,
        topic=planner_output["topic"],
        difficulty_target=planner_output["difficulty_target"],
        format_distribution=remaining_format_distribution,
    )

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=ENRICH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_enrich_response(raw)


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def run_generator(planner_output: dict) -> dict:
    """
    Jalankan Vocab Generator Agent.

    Args:
        planner_output: Output dari Planner Agent

    Returns:
        dict: {"words": [list of word objects]}
        - new words   : di-generate oleh LLM (Step 3)
        - review words: diambil dari DB + di-enrich oleh LLM (Step 1 + Step 4)

    Raises:
        RuntimeError jika LLM generate new words gagal setelah semua retry habis
    """
    topic = planner_output.get("topic")
    new_count = planner_output.get("new_words", 5)
    review_count = planner_output.get("review_words", 0)

    logger.info(
        f"[vocab_generator] Generating {planner_output.get('total_words')} words "
        f"for topic={topic} "
        f"difficulty={planner_output.get('difficulty_target')} "
        f"(new={new_count}, review={review_count})"
    )

# ── Step 1: Ambil review words dari DB via spaced repetition ──────────
    # Python yang memilih kata — bukan LLM
    # Prioritas: kata yang paling lama tidak dilihat (last_seen_at ASC)
    review_words = []
    if review_count > 0:
        db_words = get_spaced_repetition_words(
            topic=topic,
            threshold=60.0,
            limit=review_count,
        )
        for w in db_words:
            review_words.append(
                {
                    "word": w["word"],
                    "difficulty": w["difficulty"],
                    "is_new": False,
                }
            )

        logger.info(
            f"[vocab_generator] Spaced repetition: "
            f"{len(review_words)}/{review_count} review words from DB"
        )

    # ── Step 1b: Kompensasi jika review words dari DB kurang ─────────────
    # Jika DB tidak punya cukup kata untuk review (mastery_score < 60%),
    # sisa slot dialihkan ke new words agar total tetap sesuai planner.
    actual_review_count = len(review_words)
    shortfall = review_count - actual_review_count
    if shortfall > 0:
        new_count = new_count + shortfall
        logger.info(
            f"[vocab_generator] Review shortfall={shortfall} — "
            f"compensating with extra new words, new_count adjusted to {new_count}"
        )

    # ── Step 2: Split format_distribution ────────────────────────────────
    # format_distribution planner adalah untuk TOTAL kata (new + review).
    # Gunakan actual_review_count (bukan review_count) agar split akurat
    # sesuai kondisi nyata dari DB.
    new_formats, review_formats = _split_format_distribution(
        planner_output.get("format_distribution", {}),
        new_count,
    )
    logger.info(
        f"[vocab_generator] Format split — "
        f"new: {new_formats}, review: {review_formats}"
    )

    # ── Step 3: LLM generate NEW words saja ──────────────────────────────
    # Review words TIDAK disebutkan ke LLM — sudah diambil dari DB di Step 1
    try:
        result = _call_generator_llm(
            planner_output,
            new_words_count=new_count,
            new_format_distribution=new_formats,
        )
    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="vocab_generator",
            context={
                "topic": topic,
                "total_words": planner_output.get("total_words"),
                "error": str(e),
            },
            fallback_used=False,
        )
        raise RuntimeError(f"Vocab Generator gagal setelah 3x retry: {e}") from e

    # ── Step 4: LLM enrich review words dari DB ───────────────────────────
    # Review words dari DB hanya punya word + difficulty.
    # Perlu ditambah format + question_text + correct_answer agar lolos validator.
    enriched_reviews = []
    if review_words:
        try:
            enriched_reviews = _call_enrich_llm(
                review_words=review_words,
                planner_output=planner_output,
                remaining_format_distribution=review_formats,
            )
            logger.info(
                f"[vocab_generator] Enriched {len(enriched_reviews)} review words"
            )
        except Exception as e:
            # Enrich gagal — tetap lanjut dengan new words saja
            # Lebih baik sesi jalan dengan kata lebih sedikit daripada crash
            logger.warning(
                f"[vocab_generator] Enrich failed — skipping review words: {e}"
            )
            log_error(
                error_type="enrich_failed",
                agent_name="vocab_generator",
                context={
                    "review_words": [w["word"] for w in review_words],
                    "error": str(e),
                },
                fallback_used=True,
            )

    # ── Step 5: Gabung new words + enriched review words ─────────────────
    all_words = result["words"] + enriched_reviews

    logger.info(
        f"[vocab_generator] Done — "
        f"{len(all_words)} total words "
        f"({len(result['words'])} new, "
        f"{len(enriched_reviews)} review)"
    )

    result["words"] = all_words
    return result
