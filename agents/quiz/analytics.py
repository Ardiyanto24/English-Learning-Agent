"""
agents/quiz/analytics.py
-------------------------
Quiz Analytics Agent.

Berbeda dari Vocab Analytics karena harus sadar prerequisite
dan cluster — insight yang dihasilkan lebih kompleks.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import MIN_SESSIONS_FOR_ANALYTICS, SONNET_MODEL
from database.connection import get_db
from prompts.analytics.quiz_analytics_prompt import (
    QUIZ_ANALYTICS_SYSTEM_PROMPT,
    build_quiz_analytics_prompt,
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


def _load_prerequisite_rules() -> dict:
    try:
        path = Path("config/prerequisite_rules.json")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _fetch_quiz_data() -> tuple[list, list, list]:
    """Ambil semua data quiz dari DB."""
    try:
        with get_db() as conn:
            sessions = conn.execute(
                """SELECT qs.*, s.created_at, s.completed_at
                   FROM quiz_sessions qs
                   JOIN sessions s ON qs.session_id = s.session_id
                   WHERE s.status = 'completed'
                   ORDER BY s.created_at ASC"""
            ).fetchall()

            topic_tracking = conn.execute(
                "SELECT * FROM quiz_topic_tracking ORDER BY avg_score_pct ASC LIMIT 46"
            ).fetchall()

            questions = conn.execute(
                """SELECT qq.topic, qq.cluster, qq.format,
                          qq.difficulty, qq.is_correct, qq.is_graded
                   FROM quiz_questions qq
                   JOIN sessions s ON qq.session_id = s.session_id
                   WHERE qq.is_graded = 1
                   ORDER BY s.created_at DESC LIMIT 500"""
            ).fetchall()

        return (
            [dict(r) for r in sessions],
            [dict(r) for r in topic_tracking],
            [dict(r) for r in questions],
        )
    except Exception as e:
        log_error(
            "db_error", "quiz_analytics", context={"error": str(e)}, fallback_used=True
        )
        return [], [], []


def _save_snapshot(result: dict) -> None:
    try:
        with get_db() as conn:
            conn.execute(
                """INSERT INTO analytics_snapshots
                   (agent_type, snapshot_data, created_at)
                   VALUES (?, ?, ?)""",
                (
                    "quiz_analytics",
                    json.dumps(result, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
    except Exception as e:
        log_error(
            "db_error",
            "quiz_analytics",
            context={"error": str(e), "action": "save_snapshot"},
        )


def _empty_insight() -> dict:
    return {
        "coverage_pct": 0,
        "total_topics_practiced": 0,
        "weakest_topics": [],
        "strongest_topics": [],
        "cluster_progress": {},
        "prerequisite_bottleneck": None,
        "trend": "insufficient_data",
        "insight": None,
    }


def _parse_response(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text.strip())
    required = {"coverage_pct", "weakest_topics", "trend", "insight"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Analytics response missing fields: {missing}")
    return parsed


@retry_llm
def _call_analytics_llm(sessions, topic_tracking, questions, prereq_rules) -> dict:
    prompt = build_quiz_analytics_prompt(
        sessions_data=sessions,
        topic_tracking_data=topic_tracking,
        questions_data=questions,
        prerequisite_rules=prereq_rules,
    )
    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=1024,
        system=QUIZ_ANALYTICS_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_response(response.content[0].text)


def run_analytics() -> dict:
    """
    Jalankan Quiz Analytics Agent.
    Return empty insight jika data < MIN_SESSIONS_FOR_ANALYTICS.
    """
    logger.info("[quiz_analytics] Starting analytics run...")

    sessions, topic_tracking, questions = _fetch_quiz_data()

    if len(sessions) < MIN_SESSIONS_FOR_ANALYTICS:
        logger.info(
            f"[quiz_analytics] Insufficient data: {len(sessions)} sessions "
            f"(minimum: {MIN_SESSIONS_FOR_ANALYTICS})"
        )
        return _empty_insight()

    prereq_rules = _load_prerequisite_rules()

    try:
        result = _call_analytics_llm(sessions, topic_tracking, questions, prereq_rules)
        _save_snapshot(result)
        logger.info(f"[quiz_analytics] Done — trend={result.get('trend')}")
        return result
    except Exception as e:
        log_error(
            "llm_timeout",
            "quiz_analytics",
            context={"sessions": len(sessions), "error": str(e)},
            fallback_used=True,
        )
        return _empty_insight()
