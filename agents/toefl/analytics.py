"""
agents/toefl/analytics.py
--------------------------
TOEFL Analytics Agent.

Dijalankan di akhir setiap sesi simulasi TOEFL.
Menganalisis riwayat semua sesi untuk menghasilkan insight
perkembangan skor, kelemahan section, dan rekomendasi fokus latihan.

Threshold minimum: 3 simulasi (sesuai brief).

Flow:
  1. Fetch semua sesi completed dari DB
  2. Jika < 3 sesi → return empty insight
  3. Hitung agregasi per section dan per mode
  4. Kirim ke Sonnet untuk insight naratif
  5. Simpan snapshot ke analytics_snapshots
"""

import json
from typing import Optional
from datetime import datetime

import anthropic
from dotenv import load_dotenv

from prompts.analytics.toefl_analytics_prompt import (
    TOEFL_ANALYTICS_SYSTEM_PROMPT,
    build_toefl_analytics_prompt,
)
from database.connection import get_db
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL, MIN_SESSIONS_FOR_ANALYTICS

load_dotenv()

# Threshold minimum sesuai brief
MIN_TOEFL_SESSIONS = max(3, MIN_SESSIONS_FOR_ANALYTICS)

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _fetch_toefl_data() -> list[dict]:
    """
    Ambil semua sesi TOEFL yang sudah selesai dari DB.

    Returns:
        List dict dari toefl_sessions dengan score_status='completed',
        diurutkan dari yang paling lama ke paling baru (untuk trend).
    """
    try:
        with get_db() as conn:
            sessions = conn.execute(
                """
                SELECT
                    ts.session_id,
                    ts.mode,
                    ts.listening_raw,
                    ts.structure_raw,
                    ts.reading_raw,
                    ts.listening_extrapolated,
                    ts.structure_extrapolated,
                    ts.reading_extrapolated,
                    ts.listening_scaled,
                    ts.structure_scaled,
                    ts.reading_scaled,
                    ts.estimated_score,
                    ts.score_status,
                    ts.created_at
                FROM toefl_sessions ts
                WHERE ts.score_status = 'completed'
                ORDER BY ts.created_at ASC
                """
            ).fetchall()

        return [dict(row) for row in sessions]

    except Exception as e:
        log_error(
            error_type    = "db_error",
            agent_name    = "toefl_analytics",
            context       = {"error": str(e)},
            fallback_used = True,
        )
        logger.error(f"[toefl_analytics] DB fetch failed: {e}")
        return []


def _save_snapshot(result: dict) -> None:
    """Simpan hasil analytics ke analytics_snapshots."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO analytics_snapshots
                    (agent_type, snapshot_data, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    "toefl",
                    json.dumps(result, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
    except Exception as e:
        log_error(
            error_type = "db_error",
            agent_name = "toefl_analytics",
            context    = {"error": str(e), "action": "save_snapshot"},
        )
        logger.error(f"[toefl_analytics] Snapshot save failed: {e}")


def _empty_insight() -> dict:
    """Struktur kosong jika data tidak mencukupi."""
    return {
        "total_simulations":  0,
        "avg_estimated_score": None,
        "score_trend":        [],
        "weakest_section":    None,
        "section_breakdown": {
            "listening": {"avg_scaled": None, "trend": "insufficient_data"},
            "structure": {"avg_scaled": None, "trend": "insufficient_data"},
            "reading":   {"avg_scaled": None, "trend": "insufficient_data"},
        },
        "insight": None,
    }


def _parse_response(raw: str) -> dict:
    """Parse dan validasi JSON dari LLM."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text  = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    required = {"total_simulations", "avg_estimated_score", "score_trend",
                "weakest_section", "section_breakdown", "insight"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Analytics response missing fields: {missing}")

    return parsed


@retry_llm
def _call_analytics_llm(sessions: list[dict]) -> dict:
    """Panggil Claude Sonnet untuk generate insight dari data sesi."""
    prompt = build_toefl_analytics_prompt(sessions_data=sessions)

    client = _get_client()
    response = client.messages.create(
        model      = SONNET_MODEL,
        max_tokens = 1024,
        system     = TOEFL_ANALYTICS_SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": prompt}],
    )

    return _parse_response(response.content[0].text)


def run_analytics() -> dict:
    """
    Jalankan TOEFL Analytics Agent.

    Return empty insight jika data < 3 simulasi completed.

    Returns:
        dict: {
            "total_simulations"  : int,
            "avg_estimated_score": float | None,
            "score_trend"        : list[int],  ← estimated_score tiap sesi
            "weakest_section"    : "listening"|"structure"|"reading"|None,
            "section_breakdown"  : {
                "listening": {"avg_scaled": float|None, "trend": str},
                "structure": {"avg_scaled": float|None, "trend": str},
                "reading"  : {"avg_scaled": float|None, "trend": str},
            },
            "insight"            : str | None,  ← narasi dalam Bahasa Indonesia
        }
    """
    logger.info("[toefl_analytics] Starting analytics run...")

    sessions = _fetch_toefl_data()

    if len(sessions) < MIN_TOEFL_SESSIONS:
        logger.info(
            f"[toefl_analytics] Insufficient data: {len(sessions)} sessions "
            f"(minimum: {MIN_TOEFL_SESSIONS})"
        )
        return _empty_insight()

    try:
        result = _call_analytics_llm(sessions)
        _save_snapshot(result)
        logger.info(
            f"[toefl_analytics] Done — "
            f"total={result.get('total_simulations')} "
            f"avg_score={result.get('avg_estimated_score')} "
            f"weakest={result.get('weakest_section')}"
        )
        return result

    except Exception as e:
        log_error(
            error_type    = "llm_timeout",
            agent_name    = "toefl_analytics",
            context       = {"sessions": len(sessions), "error": str(e)},
            fallback_used = True,
        )
        logger.error(f"[toefl_analytics] LLM call failed: {e}")
        return _empty_insight()