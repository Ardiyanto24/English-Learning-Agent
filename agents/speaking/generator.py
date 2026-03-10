"""
agents/speaking/generator.py
------------------------------
Speaking Generator Agent.

Menghasilkan prompt pembuka untuk 3 sub-mode berbeda:
- prompted_response    : 1 pertanyaan fokus, jawab 1-2 menit
- conversation_practice: pertanyaan pembuka natural yang bisa berkembang
- oral_presentation    : topik luas untuk presentasi 1-3 menit

Oral presentation TIDAK menggunakan kategori dari metadata —
topik di-generate bebas oleh LLM agar lebih variatif.
"""

import json
import random
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.speaking.generator_prompt import (
    SPEAKING_GENERATOR_SYSTEM_PROMPT,
    build_generator_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL

load_dotenv()

_client: Optional[anthropic.Anthropic] = None

# Load speaking metadata sekali saat module di-import
_METADATA: dict = {}
try:
    _path = Path("config/speaking_metadata.json")
    with open(_path, encoding="utf-8") as f:
        _METADATA = json.load(f)
except Exception as e:
    logger.error(f"[speaking_generator] Failed to load speaking_metadata.json: {e}")


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _pick_random_topic(category: Optional[str] = None) -> tuple[str, str]:
    """
    Pilih topik secara acak dari metadata.

    Returns:
        (category_name, sub_topic)
    """
    categories = _METADATA.get("categories", {})
    if not categories:
        _FALLBACK_TOPICS = [
            ("Everyday Situations",     "Daily routines and habits"),
            ("Campus Life",             "Study habits and time management"),
            ("Technology & Innovation", "Social media and communication"),
            ("Health & Medicine",       "Healthy lifestyle choices"),
            ("Environment & Nature",    "Environmental awareness"),
        ]
        return random.choice(_FALLBACK_TOPICS)

    if category and category in categories:
        cat_name = category
    else:
        cat_name = random.choice(list(categories.keys()))

    sub_topics = categories[cat_name].get("sub_topics", [])
    topic = random.choice(sub_topics) if sub_topics else "General conversation"

    return cat_name, topic


def _parse_generator_response(raw: str) -> dict:
    """Parse JSON response dari Speaking Generator."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    required = {"sub_mode", "category", "topic", "prompt_text", "difficulty"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Generator response missing fields: {missing}")

    return parsed


@retry_llm
def _call_generator_llm(
    sub_mode: str,
    category: str,
    topic: str,
    difficulty: str,
    used_prompts: list[str],
) -> dict:
    """Panggil Claude Sonnet untuk generate speaking prompt."""
    user_prompt = build_generator_prompt(
        sub_mode=sub_mode,
        category=category,
        topic=topic,
        difficulty=difficulty,
        used_prompts=used_prompts,
    )

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=512,
        system=SPEAKING_GENERATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_generator_response(raw)


def run_generator(
    sub_mode: str,
    category: Optional[str] = None,
    topic: Optional[str] = None,
    difficulty: str = "medium",
    used_prompts: Optional[list[str]] = None,
) -> dict:
    """
    Jalankan Speaking Generator Agent.

    Untuk oral_presentation, category dan topic diabaikan —
    LLM generate topik bebas tanpa constraint metadata.

    Args:
        sub_mode    : "prompted_response" | "conversation_practice" | "oral_presentation"
        category    : Nama kategori (opsional, akan di-random jika None)
        topic       : Sub-topik (opsional, akan di-random jika None)
        difficulty  : "easy" | "medium" | "hard"
        used_prompts: Prompt yang sudah dipakai (hindari repetisi)

    Returns:
        dict: {
            "sub_mode"                  : str,
            "category"                  : str,
            "topic"                     : str,
            "prompt_text"               : str,
            "difficulty"                : str,
            "suggested_duration_seconds": int,
        }

    Raises:
        RuntimeError jika gagal setelah semua retry
    """
    # Oral presentation: generate topik bebas
    if sub_mode == "oral_presentation":
        final_category = "Open Topic"
        final_topic    = "Any academic or social topic"
    else:
        # Pilih category & topic (random jika tidak disediakan)
        if category and topic:
            final_category, final_topic = category, topic
        elif category:
            final_category, final_topic = _pick_random_topic(category)
        else:
            final_category, final_topic = _pick_random_topic()

    logger.info(
        f"[speaking_generator] Generating prompt — "
        f"sub_mode={sub_mode} category='{final_category}' topic='{final_topic}'"
    )

    try:
        result = _call_generator_llm(
            sub_mode     = sub_mode,
            category     = final_category,
            topic        = final_topic,
            difficulty   = difficulty,
            used_prompts = used_prompts or [],
        )

        # Pastikan suggested_duration_seconds ada
        if "suggested_duration_seconds" not in result:
            defaults = {
                "prompted_response":    90,
                "conversation_practice": 60,
                "oral_presentation":    180,
            }
            result["suggested_duration_seconds"] = defaults.get(sub_mode, 90)

        logger.info(
            f"[speaking_generator] Done — "
            f"prompt='{result.get('prompt_text', '')[:60]}...'"
        )
        return result

    except Exception as e:
        log_error(
            error_type   = "llm_timeout",
            agent_name   = "speaking_generator",
            context      = {"sub_mode": sub_mode, "error": str(e)},
            fallback_used = False,
        )
        raise RuntimeError(
            f"Speaking Generator gagal setelah 3x retry: {e}"
        ) from e