"""
agents/vocab/generator.py
--------------------------
Vocab Generator Agent.

Tugas: Generate soal vocab berdasarkan instruksi Planner.

Input  : output dari Planner Agent (dict)
Output : dict dengan key "words" berisi list soal vocab

Error handling:
- @retry_llm: max 3x retry untuk LLM call
- JSON parse error: retry sekali dengan instruksi lebih ketat
- Setelah semua retry habis: raise exception → sesi dibatalkan
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv
from database.repositories.vocab_repository import (
    get_weak_words,
    get_word_tracking,
    get_spaced_repetition_words,
)

from prompts.vocab.generator_prompt import (
    GENERATOR_SYSTEM_PROMPT,
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


def _parse_generator_response(raw: str) -> dict:
    """
    Parse JSON response dari LLM Generator.
    Handle kasus LLM menambahkan markdown atau teks ekstra.

    Raises:
        ValueError jika JSON tidak valid atau struktur tidak sesuai
    """
    text = raw.strip()

    # Strip markdown code block jika ada
    if text.startswith("```"):
        parts = text.split("```")
        # Ambil bagian dalam code block
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    # Validasi struktur minimal
    if "words" not in parsed:
        raise ValueError(f"Response missing 'words' key: {parsed}")
    if not isinstance(parsed["words"], list):
        raise ValueError(f"'words' must be a list, got: {type(parsed['words'])}")
    if len(parsed["words"]) == 0:
        raise ValueError("'words' list is empty")

    # Validasi setiap word object
    required_fields = {"word", "difficulty", "format", "question_text",
                       "correct_answer", "is_new"}
    for i, word in enumerate(parsed["words"]):
        missing = required_fields - set(word.keys())
        if missing:
            raise ValueError(f"Word[{i}] missing fields: {missing}")

    return parsed


@retry_llm
def _call_generator_llm(planner_output: dict, new_words_count: int) -> dict:
    """
    Panggil Claude Haiku untuk generate vocab questions.
    Di-wrap @retry_llm: max 3x retry, exponential backoff 2s→4s→8s.
    """
    user_prompt = build_generator_prompt(
        topic=planner_output["topic"],
        difficulty_target=planner_output["difficulty_target"],
        format_distribution=planner_output["format_distribution"],
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


def run_generator(planner_output: dict) -> dict:
    """
    Jalankan Vocab Generator Agent.

    Args:
        planner_output: Output dari Planner Agent

    Returns:
        dict: {"words": [list of word objects]}
        - new words  : di-generate oleh LLM
        - review words: diambil dari DB via spaced repetition (last_seen_at ASC)

    Raises:
        RuntimeError jika LLM gagal setelah semua retry habis
    """
    topic        = planner_output.get("topic")
    new_count    = planner_output.get("new_words", 5)
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
            topic     = topic,
            threshold = 60.0,
            limit     = review_count,
        )
        for w in db_words:
            review_words.append({
                "word":           w["word"],
                "difficulty":     w["difficulty"],
                "format":         None,   # ⏳ pending decision — diisi nanti
                "question_text":  None,   # ⏳ pending decision — diisi nanti
                "correct_answer": None,   # ⏳ pending decision — diisi nanti
                "is_new":         False,
            })

        logger.info(
            f"[vocab_generator] Spaced repetition: "
            f"{len(review_words)}/{review_count} review words from DB"
        )

    # ── Step 2: LLM generate new words saja ──────────────────────────────
    try:
        result = _call_generator_llm(planner_output, new_words_count=new_count)

        # Gabung: new words dari LLM + review words dari DB
        result["words"] = result["words"] + review_words

        logger.info(
            f"[vocab_generator] Done — "
            f"{len(result['words'])} total words "
            f"({len(result['words']) - len(review_words)} new, "
            f"{len(review_words)} review)"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="vocab_generator",
            context={
                "topic":       topic,
                "total_words": planner_output.get("total_words"),
                "error":       str(e),
            },
            fallback_used=False,
        )
        # Tidak ada fallback untuk generator — sesi harus dibatalkan
        raise RuntimeError(
            f"Vocab Generator gagal setelah 3x retry: {e}"
        ) from e