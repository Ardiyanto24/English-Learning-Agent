"""
agents/orchestrator/master_analytics.py
-----------------------------------------
Master Analytics Agent — insight holistik lintas semua mode.

Cara kerja yang efisien:
  TIDAK memanggil ulang keempat analytics agent dari awal.
  Sebagai gantinya, membaca snapshot TERAKHIR dari tabel
  analytics_snapshots per mode. Snapshot ini sudah di-generate
  secara otomatis setiap kali user menyelesaikan sesi.

  Kenapa snapshot, bukan live query?
  - 4 LLM call paralel hanya untuk "persiapan" terlalu mahal
  - Snapshot adalah cache yang sudah valid — tidak perlu di-recompute
  - Jika snapshot tidak ada → mode dianggap belum punya data cukup

Threshold:
  Minimal ADA SATU mode dengan snapshot tersedia.
  Jika tidak ada sama sekali → return early, minta user mulai latihan dulu.

Dipanggil on-demand dari Dashboard Layer 3 (bukan otomatis setelah sesi).
"""

import json
from datetime import datetime
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import SONNET_MODEL
from database.connection import get_db
from prompts.analytics.master_analytics_prompt import (
    MASTER_ANALYTICS_SYSTEM_PROMPT,
    build_master_analytics_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm

load_dotenv()

_client: Optional[anthropic.Anthropic] = None

# Mapping nama snapshot_type ke key yang dipakai di prompt
_SNAPSHOT_TYPES = {
    "vocab": "vocab_analytics",
    "quiz": "quiz_analytics",
    "speaking": "speaking_analytics",
    "toefl": "toefl_analytics",
}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ===================================================
# Load snapshots dari DB
# ===================================================
def _load_latest_snapshots() -> dict:
    """
    Baca snapshot analytics terakhir per mode dari DB.

    Query menggunakan subquery untuk ambil id MAX per snapshot_type,
    sehingga hanya satu row terbaru per mode yang dikembalikan.

    Returns:
        dict dengan key: 'vocab', 'quiz', 'speaking', 'toefl'
        Value: dict hasil parse JSON snapshot, atau None jika belum ada
    """
    results = {mode: None for mode in _SNAPSHOT_TYPES}

    try:
        with get_db() as conn:
            for mode, snapshot_type in _SNAPSHOT_TYPES.items():
                row = conn.execute(
                    """
                    SELECT content, generated_at
                    FROM analytics_snapshots
                    WHERE snapshot_type = ?
                    ORDER BY generated_at DESC
                    LIMIT 1
                    """,
                    (snapshot_type,),
                ).fetchone()

                if row:
                    try:
                        results[mode] = json.loads(row["content"])
                        logger.info(f"[master_analytics] Loaded {mode} snapshot " f"(generated: {row['generated_at']})")
                    except json.JSONDecodeError as e:
                        logger.warning(f"[master_analytics] Failed to parse " f"{mode} snapshot: {e}")

    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="master_analytics",
            context=str(e),
            fallback_used="empty_snapshots",
        )

    return results


# ===================================================
# Save snapshot
# ===================================================
def _save_snapshot(result: dict) -> None:
    """Simpan output Master Analytics ke analytics_snapshots."""
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO analytics_snapshots
                (snapshot_type, content, generated_at)
                VALUES (?, ?, ?)                """,
                (
                    "master_analytics",
                    json.dumps(result, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="master_analytics",
            context=str(e),
        )


# ===================================================
# Empty result
# ===================================================
def _empty_insight(reason: str = "insufficient_data") -> dict:
    """
    Return dict kosong dengan reason.
    Dipanggil saat tidak ada snapshot sama sekali atau LLM gagal.
    """
    return {
        "modes_with_data": [],
        "modes_without_data": ["vocab", "quiz", "speaking", "toefl"],
        "overall_trend": "insufficient_data",
        "cross_mode_correlations": [],
        "toefl_readiness": {
            "target_score": None,
            "best_estimated_score": None,
            "gap": None,
            "avg_improvement_per_sim": None,
            "estimated_weeks": None,
            "readiness_level": "no_data",
            "recommendation": ("Mulai latihan di semua mode untuk mendapatkan analisis lengkap."),
        },
        "top_priority": None,
        "insight": None,
        "_reason": reason,
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

    required = {"overall_trend", "cross_mode_correlations", "toefl_readiness", "insight"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Master analytics response missing fields: {missing}")

    return parsed


# ===================================================
# LLM call
# ===================================================
@retry_llm
def _call_master_llm(
    snapshots: dict,
    target_score: int,
) -> dict:
    prompt = build_master_analytics_prompt(
        vocab_analytics=snapshots.get("vocab"),
        quiz_analytics=snapshots.get("quiz"),
        speaking_analytics=snapshots.get("speaking"),
        toefl_analytics=snapshots.get("toefl"),
        target_score=target_score,
    )
    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=1500,
        system=MASTER_ANALYTICS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_response(response.content[0].text)


# ===================================================
# Public entry point
# ===================================================
def run_master_analytics(target_toefl: int) -> dict:
    """
    Jalankan Master Analytics Agent.

    Args:
        target_toefl: Target skor TOEFL ITP dari tabel users.
                      Dipakai untuk kalkulasi readiness dan gap.

    Returns:
        dict insight holistik lintas mode.
        Jika tidak ada data sama sekali, return _empty_insight().
    """
    logger.info(f"[master_analytics] Starting master analytics run " f"(target={target_toefl})...")

    # Load semua snapshot terakhir
    snapshots = _load_latest_snapshots()

    # Cek apakah minimal satu mode punya snapshot
    modes_with_data = [mode for mode, data in snapshots.items() if data and data.get("insight")]

    if not modes_with_data:
        logger.info("[master_analytics] No snapshots available — user has not completed" "enough sessions in any mode.")
        result = _empty_insight("no_snapshots_available")
        result["toefl_readiness"]["target_score"] = target_toefl
        return result

    logger.info(f"[master_analytics] Snapshots loaded for: {modes_with_data}")

    try:
        result = _call_master_llm(snapshots, target_toefl)

        # Pastikan target_score konsisten dengan input
        if "toefl_readiness" in result:
            result["toefl_readiness"]["target_score"] = target_toefl

        _save_snapshot(result)

        logger.info(
            f"[master_analytics] Done — trend={result.get('overall_trend')}, "
            f"correlations={len(result.get('cross_mode_correlations', []))}, "
            f"readiness={result.get('toefl_readiness', {}).get('readiness_level')}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="master_analytics",
            context=str(e),
            fallback_used="empty_insight",
        )
        result = _empty_insight("llm_failed")
        result["toefl_readiness"]["target_score"] = target_toefl
        return result
