"""
agents/quiz_tutor/analytics.py
-------------------------------
Grammar Tutor Analytics Agent.

Perbedaan dari TOEFL Quiz Analytics:
- Fokus pemahaman konseptual — analisis pola credit_level per tipe soal,
  bukan skor scaled per section seperti TOEFL.
- Tidak memerlukan prerequisite_rules — insight murni berbasis performa
  historis di tutor_sessions, tutor_questions, tutor_topic_tracking.
- Threshold minimum 3 sesi sebelum analytics bisa dijalankan
  (lebih rendah dari TOEFL Analytics karena Grammar Tutor sesi lebih pendek).
- Output berfokus pada: topik lemah, tipe soal lemah, pola recall vs aplikasi,
  dan rekomendasi topik untuk sesi berikutnya.

Model    : claude-sonnet (SONNET_MODEL)
Threshold: minimum 3 sesi Grammar Tutor (get_tutor_session_count() < 3 → skip)
"""

import json
from datetime import datetime
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import SONNET_MODEL
from database.connection import get_db
from database.repositories.tutor_repository import (
    get_all_tutor_topic_tracking,
    get_tutor_questions_for_analytics,
    get_tutor_session_count,
    get_tutor_sessions_for_analytics,
)
from prompts.quiz_tutor.analytics_prompt import (
    TUTOR_ANALYTICS_SYSTEM_PROMPT,
    build_tutor_analytics_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm

load_dotenv()

_client: Optional[anthropic.Anthropic] = None

TUTOR_MIN_SESSIONS = 3


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _fetch_tutor_data() -> tuple[list, list, list]:
    """
    Ambil semua data Grammar Tutor dari DB untuk keperluan analytics.

    Returns:
        Tuple (sessions, topic_tracking, questions).
        Semua elemen adalah list of dict.
        Jika DB error, return tuple tiga list kosong sebagai fallback.
    """
    try:
        sessions = get_tutor_sessions_for_analytics()
        topic_tracking = get_all_tutor_topic_tracking()
        questions = get_tutor_questions_for_analytics()
        return sessions, topic_tracking, questions
    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="tutor_analytics",
            context={"error": str(e)},
            fallback_used=True,
        )
        return [], [], []


def _empty_insight() -> dict:
    """
    Return dict analytics dengan semua nilai default yang aman.

    Dipakai ketika data tidak cukup (< 3 sesi) atau saat analytics
    gagal total setelah semua retry habis.

    Returns:
        Dict dengan semua field output analytics terisi nilai kosong/None.
    """
    return {
        "weak_topics": [],
        "weak_question_types": [],
        "recall_vs_application": {
            "recall_score": 0,
            "application_score": 0,
        },
        "pattern_insight": None,
        "recommendations": [],
        "overall_insight": None,
    }


def _save_snapshot(result: dict) -> None:
    """
    Simpan hasil analytics ke tabel analytics_snapshots.

    Kegagalan simpan snapshot tidak menggagalkan analytics run —
    error di-log tapi tidak di-raise ke caller.

    Args:
        result: Dict hasil analytics yang akan disimpan.
    """
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO analytics_snapshots (snapshot_type, content, created_at)
                VALUES (?, ?, ?)
                """,
                (
                    "tutor_analytics",
                    json.dumps(result, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="tutor_analytics",
            context={"error": str(e), "action": "save_snapshot"},
        )


def _parse_response(raw: str) -> dict:
    """
    Parse dan validasi JSON response dari Tutor Analytics LLM.

    Args:
        raw: String response mentah dari LLM.

    Returns:
        Dict hasil analytics yang sudah tervalidasi.

    Raises:
        ValueError jika field wajib tidak ada.
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    required = {"weak_topics", "weak_question_types", "pattern_insight", "overall_insight"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Analytics response missing fields: {missing}")

    return parsed
