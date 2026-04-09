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

from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import HAIKU_MODEL
from prompts.quiz_tutor.planner_prompt import (
    TUTOR_PLANNER_SYSTEM_PROMPT,
    build_tutor_planner_prompt,
)
from utils.retry import retry_llm

load_dotenv()

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client

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


# ===================================================
# Distribusi Soal
# ===================================================
def _distribute_questions(
    selected_topics: list[str],
    total_questions: int,
) -> dict[str, int]:
    """
    Hitung jumlah soal per topik secara merata.

    Jika pembagian tidak habis (sisa modulo), soal ekstra diberikan
    ke topik dengan avg_score_pct terendah. Topik yang belum pernah
    dilatih (cold start) diprioritaskan mendapat soal ekstra karena
    dianggap paling butuh latihan.

    Args:
        selected_topics : List topik yang dipilih user (1–3 topik).
        total_questions : Total soal yang diminta user (5/10/15/20).

    Returns:
        Dict {topic: question_count} untuk semua topik.
    """
    n_topics = len(selected_topics)
    if n_topics == 0:
        return {}

    base_count = total_questions // n_topics
    remainder = total_questions % n_topics

    # Inisialisasi semua topik dengan base_count
    distribution = {topic: base_count for topic in selected_topics}

    if remainder == 0:
        return distribution

    # Ambil skor per topik untuk menentukan penerima soal ekstra
    topic_scores: list[tuple[str, float]] = []
    for topic in selected_topics:
        record = get_tutor_topic_tracking(topic)
        if record is None:
            # Cold start — skor 0, prioritas tertinggi untuk soal ekstra
            score = -1.0
        else:
            score = record.get("avg_score_pct", 0.0)
        topic_scores.append((topic, score))

    # Urutkan dari skor terendah (termasuk cold start di posisi terdepan)
    topic_scores.sort(key=lambda x: x[1])

    # Bagikan sisa soal ke topik-topik terlemah
    for i in range(remainder):
        weak_topic = topic_scores[i][0]
        distribution[weak_topic] += 1

    return distribution


def _get_topic_history(selected_topics: list[str]) -> dict[str, dict]:
    """
    Ambil data historis performa user untuk setiap topik yang dipilih.

    Digunakan sebagai input ke build_planner_prompt() agar LLM
    bisa menentukan proficiency level dan type_distribution yang tepat.

    Args:
        selected_topics: List topik yang dipilih user.

    Returns:
        Dict {topic: {"avg_score_pct": float, "total_sessions": int}}
        Topik cold start mendapat avg_score_pct=0 dan total_sessions=0.
    """
    history = {}
    for topic in selected_topics:
        record = get_tutor_topic_tracking(topic)
        if record:
            history[topic] = {
                "avg_score_pct": record.get("avg_score_pct", 0.0),
                "total_sessions": record.get("total_sessions", 0),
            }
        else:
            # Cold start — topik belum pernah dilatih di Grammar Tutor
            history[topic] = {
                "avg_score_pct": 0.0,
                "total_sessions": 0,
            }
    return history


# ===================================================
# LLM Call & Parse
# ===================================================
def _parse_planner_response(raw: str) -> dict:
    """
    Parse dan validasi JSON response dari Tutor Planner LLM.

    Args:
        raw: String response mentah dari LLM.

    Returns:
        Dict planner output dengan status, total_questions, dan plan.

    Raises:
        ValueError jika struktur tidak valid atau field wajib hilang.
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    # Validasi top-level keys
    required_top = {"status", "total_questions", "plan"}
    missing = required_top - set(parsed.keys())
    if missing:
        raise ValueError(f"Planner response missing top-level fields: {missing}")

    # Validasi setiap entry di plan
    required_plan = {"topic", "question_count", "proficiency_level", "type_distribution"}
    for i, entry in enumerate(parsed.get("plan", [])):
        missing_entry = required_plan - set(entry.keys())
        if missing_entry:
            raise ValueError(
                f"Plan entry[{i}] missing fields: {missing_entry}"
            )

    return parsed


@retry_llm
def _call_planner_llm(
    selected_topics: list[str],
    total_questions: int,
    topic_history: dict,
    question_distribution: dict[str, int],
) -> dict:
    """
    Panggil Claude Haiku untuk menyusun type_distribution per topik.
    Di-wrap @retry_llm: max 3x retry, exponential backoff.

    Args:
        selected_topics     : Topik yang dipilih user.
        total_questions     : Total soal sesi ini.
        topic_history       : Data historis performa per topik.
        question_distribution: Distribusi jumlah soal per topik
                               (hasil _distribute_questions).

    Returns:
        Dict planner output dengan plan per topik.
    """
    user_prompt = build_tutor_planner_prompt(
        selected_topics=selected_topics,
        total_questions=total_questions,
        topic_history=topic_history,
        question_distribution=question_distribution,
    )

    response = _get_client().messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        system=TUTOR_PLANNER_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_planner_response(raw)