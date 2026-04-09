"""
agents/quiz_tutor/planner.py
-----------------------------
Grammar Tutor Planner Agent.

Planner bekerja dalam dua tahap:

Tahap 1 — Prerequisite Check (murni Python, tanpa LLM):
  Memvalidasi apakah topik yang dipilih user sudah bisa diakses.
  Membaca DUA tabel DB secara berurutan:
    1. tutor_topic_tracking  — history dari sesi Grammar Tutor
    2. quiz_topic_tracking   — history dari sesi TOEFL Quiz
  Threshold: avg_score_pct >= 60 di salah satu tabel = prerequisite terpenuhi.
  Jika ada topik yang belum memenuhi prerequisite, return status "blocked"
  dan sesi tidak bisa dimulai (hard block).

Tahap 2 — Distribusi Soal (menggunakan logika Python):
  Setelah semua topik lolos prerequisite, hitung distribusi soal per topik
  dan distribusi tipe soal berdasarkan proficiency level user di setiap topik.
  Proficiency level ditentukan dari tutor_topic_tracking:
    cold_start : belum ada record
    familiar   : avg_score_pct 1–79
    advanced   : avg_score_pct >= 80
"""

import json
from pathlib import Path

from database.repositories.quiz_repository import get_topic_tracking
from database.repositories.tutor_repository import (
    get_tutor_topic_tracking,
)
from utils.logger import log_error, logger

# ===================================================
# Load config saat module di-import
# ===================================================
_CONFIG_DIR = Path("config")

try:
    with open(_CONFIG_DIR / "prerequisite_rules.json", encoding="utf-8") as _f:
        PREREQUISITE_RULES = json.load(_f)
except Exception as _e:
    logger.error(
        f"[tutor_planner] Gagal load prerequisite_rules.json: {_e} "
        f"— semua topik dengan prerequisite akan diblok"
    )
    PREREQUISITE_RULES = None  # Sentinel: None berarti data tidak tersedia

PREREQUISITE_THRESHOLD = 60.0


# ===================================================
# Prerequisite Check Helpers
# ===================================================
def _is_prerequisite_met(topic: str) -> bool:
    """
    Cek apakah satu topik sudah dianggap dikuasai oleh user.

    Mengquery dua tabel secara berurutan — cukup salah satu yang
    memenuhi threshold untuk dianggap terpenuhi:
    1. tutor_topic_tracking (Grammar Tutor history)
    2. quiz_topic_tracking  (TOEFL Quiz history)

    Args:
        topic: Nama topik grammar yang dicek prerequisite-nya.

    Returns:
        True jika avg_score_pct >= 60 di salah satu tabel,
        False jika keduanya tidak memenuhi atau belum ada record.
    """
    # Cek 1: tutor_topic_tracking
    tutor_record = get_tutor_topic_tracking(topic)
    if tutor_record and tutor_record.get("avg_score_pct", 0) >= PREREQUISITE_THRESHOLD:
        return True

    # Cek 2: quiz_topic_tracking
    quiz_record = get_topic_tracking(topic)
    if quiz_record and quiz_record.get("avg_score_pct", 0) >= PREREQUISITE_THRESHOLD:
        return True

    return False


def _check_prerequisites(selected_topics: list[str]) -> dict:
    """
    Validasi prerequisite untuk semua topik yang dipilih user.

    Jika PREREQUISITE_RULES gagal load (None), semua topik dianggap
    diblok karena tidak ada cara untuk memverifikasi keamanannya.

    Args:
        selected_topics: List topik yang dipilih user (1–3 topik).

    Returns:
        {"status": "ok", "blocked_topics": []}
        — jika semua topik lolos prerequisite.

        {"status": "blocked", "blocked_topics": [
            {"topic": str, "missing_prerequisites": [str]}
        ]}
        — jika ada topik yang belum bisa diakses.
    """
    # Guard: config tidak tersedia
    if PREREQUISITE_RULES is None:
        log_error(
            error_type="config_unavailable",
            agent_name="tutor_planner",
            context={"reason": "prerequisite_rules.json failed to load"},
            fallback_used=False,
        )
        return {
            "status": "blocked",
            "blocked_topics": [
                {
                    "topic": topic,
                    "missing_prerequisites": ["[Config tidak tersedia — coba restart aplikasi]"],
                }
                for topic in selected_topics
            ],
        }

    blocked_topics = []

    for topic in selected_topics:
        rules = PREREQUISITE_RULES.get(topic, {})
        requires = rules.get("requires", [])

        if not requires:
            # Topik tanpa prerequisite — langsung lolos
            continue

        # Cek setiap prerequisite
        missing = [
            req for req in requires
            if not _is_prerequisite_met(req)
        ]

        if missing:
            blocked_topics.append({
                "topic": topic,
                "missing_prerequisites": missing,
            })

    if blocked_topics:
        logger.warning(
            f"[tutor_planner] Prerequisite check failed — "
            f"blocked: {[b['topic'] for b in blocked_topics]}"
        )
        return {"status": "blocked", "blocked_topics": blocked_topics}

    logger.info(
        f"[tutor_planner] Prerequisite check passed for all topics: {selected_topics}"
    )
    return {"status": "ok", "blocked_topics": []}
