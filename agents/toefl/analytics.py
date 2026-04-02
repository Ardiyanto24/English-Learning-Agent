"""
agents/toefl/analytics.py
--------------------------
TOEFL Analytics Agent.

Dibandingkan Quiz/Vocab Analytics, TOEFL Analytics lebih sederhana
dari sisi data structure — tidak ada topic tracking, hanya riwayat
estimated score dan breakdown per section.

Yang membuatnya unik:
- Harus exclude sesi abandoned (partial data merusak trend kalkulasi)
- Analisis lintas section, bukan lintas topik
- Mode awareness: apakah user siap naik ke mode yang lebih berat?

Threshold minimum: 3 simulasi completed (bukan abandoned/incomplete)
"""

import json
from datetime import datetime
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import MIN_SESSIONS_FOR_ANALYTICS, SONNET_MODEL
from database.connection import get_db
from database.repositories.session_repository import get_abandoned_sessions
from prompts.analytics.toefl_analytics_prompt import (
    TOEFL_ANALYTICS_SYSTEM_PROMPT,
    build_toefl_analytics_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm

load_dotenv()

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ===================================================
# Data fetching
# ===================================================
def _fetch_toefl_data() -> list:
    """
    Ambil semua sesi TOEFL yang benar-benar selesai (status='completed',
    score_status='completed'), exclude sesi abandoned.

    Returns:
        List dict sesi, chronological order
    """
    try:
        # Ambil session_id yang abandoned untuk di-exclude
        abandoned_ids = {s["session_id"] for s in get_abandoned_sessions(mode="toefl")}

        with get_db() as conn:
            rows = conn.execute("""
                SELECT
                    ts.session_id,
                    ts.mode,
                    ts.estimated_score,
                    ts.listening_raw,   ts.structure_raw,   ts.reading_raw,
                    ts.listening_scaled, ts.structure_scaled, ts.reading_scaled,
                    ts.listening_extrapolated,
                    ts.structure_extrapolated,
                    ts.reading_extrapolated,
                    s.started_at,
                    s.completed_at
                FROM toefl_sessions ts
                JOIN sessions s ON ts.session_id = s.session_id
                WHERE ts.score_status = 'completed'
                  AND s.status = 'completed'
                ORDER BY s.completed_at ASC
                """).fetchall()

        sessions = [dict(r) for r in rows]

        # Filter keluar yang abandoned
        sessions = [s for s in sessions if s["session_id"] not in abandoned_ids]

        return sessions

    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="toefl_analytics",
            context=str(e),
            fallback_used="empty_list",
        )
        return []


# ===================================================
# Snapshot
# ===================================================
def _save_snapshot(result: dict) -> None:
    """Simpan hasil analytics ke tabel analytics_snapshots."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO analytics_snapshots (snapshot_type, content, generated_at)
                VALUES (?, ?, ?)
                """,
                (
                    "toefl_analytics",
                    json.dumps(result, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="toefl_analytics",
            context=str(e),
        )


# ===================================================
# Empty result — returned jika data tidak cukup
# ===================================================
def _empty_insight() -> dict:
    return {
        "total_simulations": 0,
        "avg_estimated_score": None,
        "best_estimated_score": None,
        "latest_estimated_score": None,
        "section_averages": {
            "listening_scaled": None,
            "structure_scaled": None,
            "reading_scaled": None,
        },
        "weakest_section": None,
        "most_improved_section": None,
        "score_trend": "insufficient_data",
        "mode_recommendation": None,
        "insight": None,
    }


# ===================================================
# Response parsing
# ===================================================
def _parse_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text.strip())

    # Validasi field wajib
    required = {"total_simulations", "weakest_section", "score_trend", "insight"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"TOEFL analytics response missing fields: {missing}")

    return parsed


# ===================================================
# LLM call
# ===================================================
@retry_llm
def _call_analytics_llm(sessions_data: list) -> dict:
    prompt = build_toefl_analytics_prompt(sessions_data)
    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=1024,
        system=TOEFL_ANALYTICS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_response(response.content[0].text)


# ===================================================
# Public entry point
# ===================================================
def run_analytics() -> dict:
    """
    Jalankan TOEFL Analytics Agent.

    Returns:
        dict insight jika data cukup (>= MIN_SESSIONS_FOR_ANALYTICS),
        dict kosong (_empty_insight) jika belum cukup data atau LLM gagal.
    """
    logger.info("[toefl_analytics] Starting analytics run...")

    sessions = _fetch_toefl_data()

    if len(sessions) < MIN_SESSIONS_FOR_ANALYTICS:
        logger.info(
            f"[toefl_analytics] Insufficient data: {len(sessions)} completed simulations "
            f"(minimum: {MIN_SESSIONS_FOR_ANALYTICS})"
        )
        return _empty_insight()

    try:
        result = _call_analytics_llm(sessions)
        _save_snapshot(result)
        logger.info(
            f"[toefl_analytics] Done — trend={result.get('score_trend')}, "
            f"weakest={result.get('weakest_section')}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="toefl_analytics",
            context=str(e),
            fallback_used="empty_insight",
        )
        return _empty_insight()
