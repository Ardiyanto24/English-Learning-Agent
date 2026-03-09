"""
agents/vocab/planner.py
------------------------
Vocab Planner Agent.

Tugas: Baca history user → tentukan konfigurasi sesi optimal.

Keunikan agent ini:
- Cold start → SKIP LLM call, langsung pakai DEFAULT_PLANNER_CONFIG
- Returning user → panggil Claude Haiku untuk tentukan config adaptif

Input  : topic (str), user_id (str, default "default_user")
Output : dict sesuai spesifikasi Part 5
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.vocab.planner_prompt import (
    PLANNER_SYSTEM_PROMPT,
    DEFAULT_PLANNER_CONFIG,
    build_planner_prompt,
)
from database.repositories.vocab_repository import (
    get_weak_words,
    get_word_tracking,
)
from utils.helpers import is_cold_start, calculate_score_pct
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


def _build_history_summary(topic: str) -> dict:
    """
    Ambil dan ringkas history user dari DB untuk dikirim ke Planner LLM.

    Returns dict dengan struktur yang diharapkan build_planner_prompt().
    """
    try:
        # Cek weak words untuk spaced repetition
        weak_words = get_weak_words(topic=topic, threshold=60.0, limit=50)
        weak_count = len(weak_words) if weak_words else 0

        # Hitung rata-rata mastery per difficulty level
        # Ambil sample kata dari DB untuk estimasi mastery per level
        avg_mastery = {"easy": -1.0, "medium": -1.0, "hard": -1.0}

        # Cold start jika tidak ada weak words dan tidak ada tracking
        if weak_count == 0:
            # Coba cek apakah ada data sama sekali
            sample = get_word_tracking("sample_check", topic)
            if is_cold_start(sample) and weak_count == 0:
                return {"is_cold_start": True}

        # Hitung avg mastery dari weak words yang ada
        if weak_words:
            by_difficulty: dict[str, list] = {}
            for w in weak_words:
                diff = w.get("difficulty", "easy")
                score = w.get("mastery_score", 0.0)
                by_difficulty.setdefault(diff, []).append(score)

            for diff, scores in by_difficulty.items():
                if scores:
                    avg_mastery[diff] = round(sum(scores) / len(scores), 1)

        return {
            "is_cold_start": False,
            "current_difficulty": _determine_current_difficulty(avg_mastery),
            "avg_mastery_easy": avg_mastery["easy"],
            "avg_mastery_medium": avg_mastery["medium"],
            "avg_mastery_hard": avg_mastery["hard"],
            "weak_words_count": weak_count,
            "total_sessions": 0,  # Simplified — bisa diperluas nanti
        }

    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="vocab_planner",
            context={"topic": topic, "error": str(e)},
            fallback_used=True,
        )
        # DB error → fallback cold start
        return {"is_cold_start": True}


def _determine_current_difficulty(avg_mastery: dict) -> str:
    """Tentukan difficulty level saat ini berdasarkan mastery scores.
    
    Upgrade : avg mastery level saat ini >= 80%
    Downgrade: avg mastery level saat ini < 40%
    Stay    : di antara 40%-80%
    """
    easy   = avg_mastery.get("easy",   -1)
    medium = avg_mastery.get("medium", -1)
    hard   = avg_mastery.get("hard",   -1)

    # Tentukan level tertinggi yang punya data
    if hard >= 0:
        current = "hard"
        current_score = hard
    elif medium >= 0:
        current = "medium"
        current_score = medium
    elif easy >= 0:
        current = "easy"
        current_score = easy
    else:
        return "easy"  # Tidak ada data sama sekali

    # Upgrade
    if current_score >= 80:
        if current == "easy":
            return "medium"
        elif current == "medium":
            return "hard"
        else:
            return "hard"  # Sudah di hard, tetap

    # Downgrade
    if current_score < 40:
        if current == "hard":
            return "medium"
        elif current == "medium":
            return "easy"
        else:
            return "easy"  # Sudah di easy, tetap

    # Stay (40–79%)
    return current


@retry_llm
def _call_planner_llm(topic: str, history_summary: dict) -> dict:
    """
    Panggil Claude Haiku untuk generate planner config.
    Di-wrap @retry_llm: max 3x retry, exponential backoff 2s→4s→8s.
    """
    user_prompt = build_planner_prompt(topic, history_summary)

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=PLANNER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text.strip()

    # Handle jika LLM menambahkan markdown code block
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    return json.loads(raw)


def run_planner(topic: str = "sehari_hari") -> dict:
    """
    Jalankan Vocab Planner Agent.

    Args:
        topic: Topik situasi untuk sesi ini

    Returns:
        dict konfigurasi sesi:
        {
            "topic": str,
            "total_words": int,
            "new_words": int,
            "review_words": int,
            "difficulty_target": str,
            "format_distribution": dict
        }
    """
    history_summary = _build_history_summary(topic)

    # Cold start: skip LLM, pakai default config
    if history_summary.get("is_cold_start"):
        logger.info("[vocab_planner] Cold start detected — using default config")
        config = DEFAULT_PLANNER_CONFIG.copy()
        config["topic"] = topic
        return config

    # Returning user: panggil LLM
    prompt = build_planner_prompt(topic, history_summary)
    if prompt is None:
        # build_planner_prompt return None = cold start
        config = DEFAULT_PLANNER_CONFIG.copy()
        config["topic"] = topic
        return config

    try:
        config = _call_planner_llm(topic, history_summary)
        config["topic"] = topic  # Pastikan topic dari input, bukan LLM
        logger.info(f"[vocab_planner] Config generated: {config}")
        return config

    except Exception as e:
        # Setelah 3x retry tetap gagal → fallback default config
        log_error(
            error_type="llm_timeout",
            agent_name="vocab_planner",
            context={"topic": topic, "error": str(e)},
            fallback_used=True,
        )
        logger.warning("[vocab_planner] LLM failed after retries — using default config")
        config = DEFAULT_PLANNER_CONFIG.copy()
        config["topic"] = topic
        return config