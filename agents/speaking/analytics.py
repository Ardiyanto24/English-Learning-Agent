"""
agents/speaking/analytics.py
------------------------------
Speaking Analytics Agent.

Analisis lebih kompleks dari Vocab/Quiz karena harus breakdown
per sub-mode dan per kriteria secara bersamaan.
"""

import json
from typing import Optional
from datetime import datetime

import anthropic
from dotenv import load_dotenv

from prompts.analytics.speaking_analytics_prompt import (
    SPEAKING_ANALYTICS_SYSTEM_PROMPT,
    build_speaking_analytics_prompt,
)
from database.connection import get_db
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL, MIN_SESSIONS_FOR_ANALYTICS

load_dotenv()

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _fetch_speaking_data() -> tuple[list, list]:
    """Ambil speaking sessions dan exchanges dari DB."""
    try:
        with get_db() as conn:
            sessions = conn.execute(
                """SELECT ss.*, s.created_at, s.completed_at
                   FROM speaking_sessions ss
                   JOIN sessions s ON ss.session_id = s.session_id
                   WHERE s.status = 'completed'
                   ORDER BY s.created_at ASC"""
            ).fetchall()

            exchanges = conn.execute(
                """SELECT se.role, se.text, se.exchange_index,
                          s.created_at as session_date
                   FROM speaking_exchanges se
                   JOIN sessions s ON se.session_id = s.session_id
                   WHERE s.status = 'completed'
                   ORDER BY s.created_at DESC, se.exchange_index ASC
                   LIMIT 100"""
            ).fetchall()

        return (
            [dict(r) for r in sessions],
            [dict(r) for r in exchanges],
        )
    except Exception as e:
        log_error("db_error", "speaking_analytics",
                  context={"error": str(e)}, fallback_used=True)
        return [], []


def _save_snapshot(result: dict) -> None:
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO analytics_snapshots
                   (agent_type, snapshot_data, created_at)
                   VALUES (?, ?, ?)""",
                ("speaking_analytics",
                 json.dumps(result, ensure_ascii=False),
                 datetime.now().isoformat()),
            )
    except Exception as e:
        log_error("db_error", "speaking_analytics",
                  context={"error": str(e), "action": "save_snapshot"})


def _empty_insight() -> dict:
    return {
        "total_sessions":     0,
        "avg_scores_by_mode": {},
        "strongest_criterion": None,
        "weakest_criterion":   None,
        "trend":               "insufficient_data",
        "pattern_insight":     None,
        "insight":             None,
    }


def _parse_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text.strip())
    required = {"total_sessions", "trend", "insight"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Analytics response missing fields: {missing}")
    return parsed


@retry_llm
def _call_analytics_llm(sessions: list, exchanges: list) -> dict:
    prompt = build_speaking_analytics_prompt(
        sessions_data  = sessions,
        exchanges_data = exchanges,
    )
    client = _get_client()
    response = client.messages.create(
        model     = SONNET_MODEL,
        max_tokens= 1024,
        system    = SPEAKING_ANALYTICS_SYSTEM_PROMPT,
        messages  = [{"role": "user", "content": prompt}],
    )
    return _parse_response(response.content[0].text)


def run_analytics() -> dict:
    """
    Jalankan Speaking Analytics Agent.
    Return empty insight jika data < MIN_SESSIONS_FOR_ANALYTICS.
    """
    logger.info("[speaking_analytics] Starting analytics run...")

    sessions, exchanges = _fetch_speaking_data()

    if len(sessions) < MIN_SESSIONS_FOR_ANALYTICS:
        logger.info(
            f"[speaking_analytics] Insufficient data: {len(sessions)} sessions "
            f"(minimum: {MIN_SESSIONS_FOR_ANALYTICS})"
        )
        return _empty_insight()

    try:
        result = _call_analytics_llm(sessions, exchanges)
        _save_snapshot(result)
        logger.info(
            f"[speaking_analytics] Done — trend={result.get('trend')} "
            f"weakest={result.get('weakest_criterion')}"
        )
        return result
    except Exception as e:
        log_error("llm_timeout", "speaking_analytics",
                  context={"sessions": len(sessions), "error": str(e)},
                  fallback_used=True)
        return _empty_insight()