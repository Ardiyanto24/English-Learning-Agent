"""
agents/quiz/planner.py
-----------------------
Quiz Planner Agent.

Tidak memanggil LLM — bekerja murni dengan logika Python berdasarkan:
- prerequisite_rules.json  : topik mana yang bisa diakses
- cluster_metadata.json    : topik mana yang related
- quiz_topic_tracking (DB) : skor dan history per topik

5 Logic Hierarki (dijalankan berurutan):
  1. Prerequisite Awareness  — filter topik yang belum bisa diakses
  2. Cognitive Load          — batasi max 1 topik baru per sesi
  3. Difficulty Progression  — tentukan level berdasarkan skor
  4. Weak Topic Reinforcement — prioritaskan topik dengan skor terendah
  5. Topic Clustering        — pilih topik dari cluster yang sama

Output planner adalah REKOMENDASI — user harus konfirmasi sebelum
Generator dipanggil (Human in the Loop).
"""

import json
from pathlib import Path
from database.connection import get_db
from utils.logger import log_error, logger
from config.settings import (
    MASTERY_THRESHOLD,
    DIFFICULTY_UPGRADE_THRESHOLD,
    DIFFICULTY_DOWNGRADE_THRESHOLD,
)

# ===================================================
# Load config files saat module di-import
# ===================================================
_CONFIG_DIR = Path("config")


def _load_json(filename: str) -> dict:
    path = _CONFIG_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


try:
    PREREQUISITE_RULES = _load_json("prerequisite_rules.json")
    CLUSTER_METADATA = _load_json("cluster_metadata.json")
except Exception as e:
    logger.error(f"[quiz_planner] Gagal load config: {e} — blocking all topics as safe fallback")
    # INTENTIONAL: dict kosong akan menyebabkan semua topik dianggap
    # tidak ditemukan di _filter_by_prerequisite(), sehingga requires
    # tidak pernah kosong. Kita gunakan sentinel khusus agar semua
    # topik advance diblokir.
    PREREQUISITE_RULES = None   # ← None, bukan {} — sinyal "data tidak tersedia"
    CLUSTER_METADATA = {"clusters": {}}

# Default format distribution (60/20/20)
DEFAULT_FORMAT_DISTRIBUTION = {
    "multiple_choice": 7,
    "error_id":        1,
    "fill_blank":      2,
}
DEFAULT_TOTAL_QUESTIONS = 10


# ===================================================
# DB Helpers
# ===================================================
def _get_all_topic_tracking() -> dict[str, dict]:
    """
    Ambil semua data quiz_topic_tracking dari DB.
    Return dict: {topic_name: {avg_score_pct, is_prerequisite_met, ...}}
    """
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM quiz_topic_tracking"
            ).fetchall()
        return {row["topic"]: dict(row) for row in rows}
    except Exception as e:
        log_error("db_error", "quiz_planner", context={"error": str(e)})
        return {}


def _get_practiced_topics_this_session_pool() -> set[str]:
    """
    Ambil topik yang sudah pernah dilatih (ada di DB).
    Digunakan untuk deteksi cold start dan cognitive load.
    """
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT topic FROM quiz_topic_tracking "
                "WHERE total_sessions > 0"
            ).fetchall()
        return {row["topic"] for row in rows}
    except Exception:
        return set()


# ===================================================
# Logic 1: Prerequisite Awareness
# ===================================================
def _filter_by_prerequisite(
    all_topics: list[str],
    topic_tracking: dict[str, dict],
) -> list[str]:
    # Jika config gagal load (None), blokir semua topik advance —
    # hanya topik tanpa prerequisite yang lolos via fallback list.
    if PREREQUISITE_RULES is None:
        logger.warning(
            "[quiz_planner] PREREQUISITE_RULES unavailable — "
            "blocking all topics as safe fallback"
        )
        return []   # Tidak ada topik yang accessible → fallback di run_planner()

    accessible = []
    for topic in all_topics:
        rules = PREREQUISITE_RULES.get(topic, {})
        requires = rules.get("requires", [])

        if not requires:
            accessible.append(topic)
            continue

        # Cek semua prerequisite terpenuhi
        all_met = True
        for req in requires:
            req_data = topic_tracking.get(req, {})
            req_score = req_data.get("avg_score_pct", 0)
            req_practiced = req_data.get("total_sessions", 0) > 0

            if not req_practiced or req_score < (MASTERY_THRESHOLD * 100):
                all_met = False
                break

        if all_met:
            accessible.append(topic)

    return accessible


# ===================================================
# Logic 2: Cognitive Load
# ===================================================
def _apply_cognitive_load(
    accessible_topics: list[str],
    practiced_topics: set[str],
) -> tuple[list[str], list[str]]:
    """
    Pisahkan topik menjadi new_topics dan review_topics.
    Batasi new_topics maksimal 1 per sesi.

    Returns:
        (new_topics, review_topics)
    """
    new_topics = [t for t in accessible_topics if t not in practiced_topics]
    review_topics = [t for t in accessible_topics if t in practiced_topics]

    # Max 1 topik baru per sesi
    new_topics = new_topics[:1]

    return new_topics, review_topics


# ===================================================
# Logic 3: Difficulty Progression
# ===================================================
def _determine_difficulty(
    review_topics: list[str],
    topic_tracking: dict[str, dict],
) -> str:
    """
    Tentukan difficulty target berdasarkan rata-rata skor review topics.

    - avg < DOWNGRADE_THRESHOLD → easy
    - avg >= UPGRADE_THRESHOLD  → hard
    - otherwise                 → medium
    """
    if not review_topics:
        return "easy"  # Cold start atau semua topik baru

    scores = []
    for topic in review_topics:
        data = topic_tracking.get(topic, {})
        score = data.get("avg_score_pct", 0)
        if data.get("total_sessions", 0) > 0:
            scores.append(score)

    if not scores:
        return "easy"

    avg = sum(scores) / len(scores)

    if avg >= DIFFICULTY_UPGRADE_THRESHOLD:
        return "hard"
    elif avg < DIFFICULTY_DOWNGRADE_THRESHOLD:
        return "easy"
    else:
        return "medium"


# ===================================================
# Logic 4: Weak Topic Reinforcement
# ===================================================
def _prioritize_weak_topics(
    review_topics: list[str],
    topic_tracking: dict[str, dict],
    max_topics: int = 2,
) -> list[str]:
    """
    Urutkan review_topics dari yang paling lemah (avg_score rendah).
    Ambil max_topics teratas untuk sesi ini.
    """
    def score_key(topic):
        data = topic_tracking.get(topic, {})
        # Topik yang belum pernah dilatih dapat skor 0 (prioritas tinggi)
        return data.get("avg_score_pct", 0)

    sorted_topics = sorted(review_topics, key=score_key)
    return sorted_topics[:max_topics]


# ===================================================
# Logic 5: Topic Clustering
# ===================================================
def _apply_clustering(
    selected_topics: list[str],
    accessible_topics: list[str],
) -> list[str]:
    """
    Jika hanya ada 1 topik terpilih, coba tambah topik lain
    dari cluster yang sama agar sesi lebih kohesif.

    Tidak menambah jika sudah ada 2+ topik.
    """
    if len(selected_topics) >= 2:
        return selected_topics

    if not selected_topics:
        return selected_topics

    # Temukan cluster dari topik pertama
    primary_topic = selected_topics[0]
    primary_cluster = PREREQUISITE_RULES.get(primary_topic, {}).get("cluster")

    if not primary_cluster:
        return selected_topics

    # Cari topik lain di cluster yang sama yang accessible
    cluster_topics = CLUSTER_METADATA["clusters"].get(
        primary_cluster, {}
    ).get("topics", [])

    for candidate in cluster_topics:
        if candidate not in selected_topics and candidate in accessible_topics:
            selected_topics.append(candidate)
            break

    return selected_topics


# ===================================================
# Format Distribution Helper
# ===================================================
def _build_format_distribution(total_questions: int) -> dict:
    """
    Hitung distribusi format soal berdasarkan total_questions.
    Rasio: multiple_choice 60%, error_id 20%, fill_blank 20%.
    """
    mc = max(1, round(total_questions * 0.6))
    ei = max(1, round(total_questions * 0.2))
    fb = total_questions - mc - ei

    # Pastikan tidak ada yang negatif
    if fb < 0:
        mc += fb
        fb = 0

    return {
        "multiple_choice": mc,
        "error_id":        ei,
        "fill_blank":      max(0, fb),
    }


# ===================================================
# Main: run_planner
# ===================================================
def run_planner(total_questions: int = DEFAULT_TOTAL_QUESTIONS) -> dict:
    """
    Jalankan Quiz Planner Agent.

    Menjalankan 5 logic hierarki dan menghasilkan rekomendasi topik.
    Output ini adalah REKOMENDASI — UI harus meminta konfirmasi user
    sebelum memanggil Generator.

    Args:
        total_questions: Jumlah soal yang diinginkan (default 5)

    Returns:
        dict: {
            "topics"              : list topik yang direkomendasikan,
            "cluster"             : nama cluster utama,
            "total_questions"     : int,
            "difficulty_target"   : "easy"|"medium"|"hard",
            "format_distribution" : dict,
            "new_topics"          : list topik baru,
            "review_topics"       : list topik review,
            "is_cold_start"       : bool,
            "accessible_topics"   : list semua topik yang bisa diakses,
        }
    """
    logger.info("[quiz_planner] Starting planner run...")

    # Ambil semua data dari DB dan config
    topic_tracking = _get_all_topic_tracking()
    practiced_topics = _get_practiced_topics_this_session_pool()
    all_topics = list(PREREQUISITE_RULES.keys())

    # Deteksi cold start
    is_cold_start = len(practiced_topics) == 0

    # ── Logic 1: Prerequisite Awareness ──────────────────
    accessible = _filter_by_prerequisite(all_topics, topic_tracking)
    logger.info(
        "[quiz_planner] Accessible topics after "
        f"prereq filter: {len(accessible)}"
    )

    # ── Logic 2: Cognitive Load ───────────────────────────
    new_topics, review_topics = _apply_cognitive_load(
        accessible, practiced_topics
    )

    # ── Logic 3: Difficulty Progression ──────────────────
    difficulty = _determine_difficulty(review_topics, topic_tracking)

    # ── Logic 4: Weak Topic Reinforcement ────────────────
    prioritized_review = _prioritize_weak_topics(review_topics, topic_tracking)

    # Gabung: new + review (prioritized)
    # Komposisi: max 1 new topic + max 1 review topic
    # Tidak boleh new_topics mendominasi dan memotong review topics
    selected_new = new_topics[:1]
    selected_review = prioritized_review[:1]
    selected = selected_new + selected_review

    # Fallback jika tidak ada topik sama sekali
    if not selected and accessible:
        selected = accessible[:1]

    # ── Logic 5: Topic Clustering ─────────────────────────
    selected = _apply_clustering(selected, accessible)

    # Tentukan cluster utama
    primary_cluster = None
    if selected:
        primary_cluster = PREREQUISITE_RULES.get(
            selected[0], {}
        ).get("cluster", "Unknown")

    # Hitung format distribution
    format_dist = _build_format_distribution(total_questions)

    result = {
        "topics":               selected,
        "cluster":              primary_cluster,
        "total_questions":      total_questions,
        "difficulty_target":    difficulty,
        "format_distribution":  format_dist,
        "new_topics":           new_topics,
        "review_topics":        [t for t in selected if t in review_topics],
        "is_cold_start":        is_cold_start,
        "accessible_topics":    accessible,
    }

    logger.info(
        f"[quiz_planner] Recommendation: topics={selected} "
        f"difficulty={difficulty} cluster={primary_cluster}"
    )
    return result
