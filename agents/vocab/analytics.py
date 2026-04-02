"""
agents/vocab/analytics.py
--------------------------
Vocab Analytics Agent.

Tugas: Analisis data historis user → generate insight yang actionable.

Dipanggil setelah setiap sesi vocab selesai.
Jika data kurang dari MIN_SESSIONS_FOR_ANALYTICS → return empty insight.

Input  : (tidak ada — baca langsung dari DB)
Output : dict sesuai spesifikasi Part 5, tersimpan ke analytics_snapshots
"""

import json
from typing import Optional
from datetime import datetime

import anthropic
from dotenv import load_dotenv

from prompts.analytics.vocab_analytics_prompt import (
    VOCAB_ANALYTICS_SYSTEM_PROMPT,
    build_vocab_analytics_prompt,
)
from database.connection import get_db
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import (
    SONNET_MODEL,
    MIN_SESSIONS_FOR_ANALYTICS,
)

load_dotenv()

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _fetch_vocab_data() -> tuple[list, list, list]:
    """
    Ambil semua data vocab dari DB yang dibutuhkan untuk analytics.

    Returns:
        (sessions_data, word_tracking_data, questions_data)
    """
    try:
        with get_db() as conn:
            # Ambil semua vocab sessions yang completed
            sessions = conn.execute("""
                SELECT vs.*, s.created_at, s.completed_at
                FROM vocab_sessions vs
                JOIN sessions s ON vs.session_id = s.session_id
                WHERE s.status = 'completed'
                ORDER BY s.created_at ASC
                """).fetchall()

            # Ambil semua word tracking
            word_tracking = conn.execute("""
                SELECT * FROM vocab_word_tracking
                ORDER BY last_seen DESC
                LIMIT 200
                """).fetchall()

            # Ambil semua questions yang sudah di-grade
            questions = conn.execute("""
                SELECT vq.word, vq.format, vq.is_correct, vq.is_graded
                FROM vocab_questions vq
                JOIN sessions s ON vq.session_id = s.session_id
                WHERE vq.is_graded = 1
                ORDER BY s.created_at DESC
                LIMIT 500
                """).fetchall()

        return (
            [dict(r) for r in sessions],
            [dict(r) for r in word_tracking],
            [dict(r) for r in questions],
        )

    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="vocab_analytics",
            context={"error": str(e)},
            fallback_used=True,
        )
        return [], [], []


def _save_analytics_snapshot(analytics_result: dict) -> None:
    """Simpan hasil analytics ke tabel analytics_snapshots."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO analytics_snapshots
                    (agent_type, snapshot_data, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    "vocab_analytics",
                    json.dumps(analytics_result, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
        logger.info("[vocab_analytics] Snapshot saved to DB")
    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="vocab_analytics",
            context={"error": str(e), "action": "save_snapshot"},
            fallback_used=False,
        )


def _empty_insight() -> dict:
    """Return empty insight jika data tidak cukup."""
    return {
        "total_words_learned": 0,
        "mastery_distribution": {
            "easy": 0.0,
            "medium": 0.0,
            "hard": 0.0,
        },
        "weakest_format": None,
        "strongest_format": None,
        "weak_words": [],
        "trend": "insufficient_data",
        "insight": None,
    }


def _parse_analytics_response(raw: str) -> dict:
    """Parse JSON response dari LLM Analytics."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    # Validasi field wajib ada
    required = {"total_words_learned", "mastery_distribution", "weakest_format", "trend", "insight"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Analytics response missing fields: {missing}")

    return parsed


@retry_llm
def _call_analytics_llm(
    sessions_data: list,
    word_tracking_data: list,
    questions_data: list,
) -> dict:
    """Panggil Claude Sonnet untuk generate insight. Di-wrap @retry_llm."""
    user_prompt = build_vocab_analytics_prompt(
        sessions_data=sessions_data,
        word_tracking_data=word_tracking_data,
        questions_data=questions_data,
    )

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=1024,
        system=VOCAB_ANALYTICS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_analytics_response(raw)


def run_analytics() -> dict:
    """
    Jalankan Vocab Analytics Agent.

    Flow:
    1. Ambil semua data dari DB
    2. Cek threshold minimum 3 sesi
    3. Jika kurang → return empty insight
    4. Panggil Claude Sonnet untuk generate insight
    5. Simpan ke analytics_snapshots
    6. Return hasil

    Returns:
        dict insight sesuai spesifikasi Part 5
    """
    logger.info("[vocab_analytics] Starting analytics run...")

    sessions_data, word_tracking_data, questions_data = _fetch_vocab_data()

    # Cek threshold minimum
    total_sessions = len(sessions_data)
    if total_sessions < MIN_SESSIONS_FOR_ANALYTICS:
        logger.info(
            f"[vocab_analytics] Insufficient data: {total_sessions} sessions "
            f"(minimum: {MIN_SESSIONS_FOR_ANALYTICS}) — returning empty insight"
        )
        return _empty_insight()

    logger.info(
        f"[vocab_analytics] Analyzing {total_sessions} sessions, "
        f"{len(word_tracking_data)} words tracked, "
        f"{len(questions_data)} questions"
    )

    try:
        result = _call_analytics_llm(
            sessions_data=sessions_data,
            word_tracking_data=word_tracking_data,
            questions_data=questions_data,
        )

        # Simpan ke DB
        _save_analytics_snapshot(result)

        logger.info(
            f"[vocab_analytics] Done — trend={result.get('trend')} "
            f"words_learned={result.get('total_words_learned')}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="vocab_analytics",
            context={"sessions": total_sessions, "error": str(e)},
            fallback_used=True,
        )
        logger.warning("[vocab_analytics] LLM failed — returning empty insight")
        return _empty_insight()
