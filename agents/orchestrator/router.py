"""
agents/orchestrator/router.py
------------------------------
Router Agent — logic-based routing tanpa LLM.

Dua tanggung jawab utama:
  1. Deteksi apakah user sudah onboarding atau belum
  2. Load user context dari DB untuk dikirim ke agent manapun

Kenapa tidak pakai LLM?
  Routing adalah keputusan deterministik murni — user pilih mode,
  router load context, selesai. LLM tidak menambah nilai di sini,
  hanya menambah latensi dan potensi failure point.

RoutingContext adalah objek pusat yang berisi semua info tentang
user saat ini. Dashboard dan setiap page bisa import dan memakainya
tanpa perlu query DB sendiri.

Cara pakai di page lain:
    from agents.orchestrator.router import get_routing_context

    ctx = get_routing_context()
    if ctx.needs_onboarding:
        # tampilkan onboarding
    else:
        target = ctx.target_toefl
        level  = ctx.grammar_level
"""

from dataclasses import dataclass, field
from typing import Optional

from database.connection import get_db
from utils.logger import log_error, logger


# ===================================================
# RoutingContext — objek yang dibawa ke seluruh UI
# ===================================================
@dataclass
class RoutingContext:
    """
    Snapshot kondisi user saat ini.

    Jika needs_onboarding=True, semua field lain None/0.
    Dashboard wajib cek needs_onboarding sebelum mengakses field lain.
    """

    needs_onboarding: bool = True

    # Data dari tabel users
    user_id: Optional[int] = None
    target_toefl: Optional[int] = None
    grammar_level: Optional[str] = None
    first_vocab_topic: Optional[str] = None

    # Quick stats per mode — untuk Dashboard quick snapshot
    # Format: {"vocab": {"total_sessions": 5, "last_score": 78.0}, ...}
    mode_stats: dict = field(default_factory=dict)


# ===================================================
# Query helpers
# ===================================================
def _get_user() -> Optional[dict]:
    """
    Ambil record user pertama dari tabel users.
    Aplikasi ini single-user, jadi cukup LIMIT 1.

    Returns:
        dict user jika ada, None jika tabel kosong (belum onboarding)
    """
    try:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM users ORDER BY id ASC LIMIT 1").fetchone()
        return dict(row) if row else None
    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="router",
            context=str(e),
            fallback_used="None",
        )
        return None


def _get_mode_stats() -> dict:
    """
    Ambil statistik ringkas per mode dari DB.
    Dipakai Dashboard untuk menampilkan quick snapshot.

    Returns:
        dict berisi stats per mode:
        {
            "vocab"   : {"total_sessions": int, "last_score": float | None},
            "quiz"    : {"total_sessions": int, "last_score": float | None},
            "speaking": {"total_sessions": int, "last_score": float | None},
            "toefl"   : {"total_sessions": int, "best_score": int | None},
        }
    """
    stats = {
        "vocab": {"total_sessions": 0, "last_score": None},
        "quiz": {"total_sessions": 0, "last_score": None},
        "speaking": {"total_sessions": 0, "last_score": None},
        "toefl": {"total_sessions": 0, "best_score": None},
    }

    try:
        with get_db() as conn:
            # Vocab stats
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       MAX(vs.score_pct) as last_score
                FROM vocab_sessions vs
                JOIN sessions s ON vs.session_id = s.session_id
                WHERE s.status = 'completed'
                """).fetchone()
            if row:
                stats["vocab"]["total_sessions"] = row["total"] or 0
                stats["vocab"]["last_score"] = row["last_score"]

            # Quiz stats — ambil skor sesi terakhir (bukan max)
            row = conn.execute("""
                SELECT COUNT(*) as total
                FROM quiz_sessions qs
                JOIN sessions s ON qs.session_id = s.session_id
                WHERE s.status = 'completed'
                """).fetchone()
            last_quiz = conn.execute("""
                SELECT qs.score_pct
                FROM quiz_sessions qs
                JOIN sessions s ON qs.session_id = s.session_id
                WHERE s.status = 'completed'
                ORDER BY s.completed_at DESC
                LIMIT 1
                """).fetchone()
            if row:
                stats["quiz"]["total_sessions"] = row["total"] or 0
            if last_quiz:
                stats["quiz"]["last_score"] = last_quiz["score_pct"]

            # Speaking stats
            row = conn.execute("""
                SELECT COUNT(*) as total
                FROM speaking_sessions ss
                JOIN sessions s ON ss.session_id = s.session_id
                WHERE s.status = 'completed'
                """).fetchone()
            last_speaking = conn.execute("""
                SELECT ss.final_score
                FROM speaking_sessions ss
                JOIN sessions s ON ss.session_id = s.session_id
                WHERE s.status = 'completed' AND ss.is_graded = 1
                ORDER BY s.completed_at DESC
                LIMIT 1
                """).fetchone()
            if row:
                stats["speaking"]["total_sessions"] = row["total"] or 0
            if last_speaking:
                stats["speaking"]["last_score"] = last_speaking["final_score"]

            # TOEFL stats — tampilkan best estimated score
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       MAX(ts.estimated_score) as best_score
                FROM toefl_sessions ts
                JOIN sessions s ON ts.session_id = s.session_id
                WHERE s.status = 'completed'
                  AND ts.score_status = 'completed'
                """).fetchone()
            if row:
                stats["toefl"]["total_sessions"] = row["total"] or 0
                stats["toefl"]["best_score"] = row["best_score"]

    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="router",
            context=str(e),
            fallback_used="empty_stats",
        )

    return stats


# ===================================================
# Public API
# ===================================================
def get_routing_context() -> RoutingContext:
    """
    Entry point utama Router.

    Flow:
    1. Ambil record user dari DB
    2. Jika kosong → return RoutingContext(needs_onboarding=True)
    3. Jika ada → load user data + mode stats
    4. Return RoutingContext lengkap

    Returns:
        RoutingContext — selalu return objek valid, tidak raise exception
    """
    user = _get_user()

    if not user:
        logger.info("[router] No user record found — onboarding required")
        return RoutingContext(needs_onboarding=True)

    mode_stats = _get_mode_stats()

    logger.info(
        f"[router] User loaded — target={user.get('target_toefl')}, "
        f"level={user.get('grammar_level')}"
    )

    return RoutingContext(
        needs_onboarding=False,
        user_id=user.get("id"),
        target_toefl=user.get("target_toefl"),
        grammar_level=user.get("grammar_level"),
        first_vocab_topic=user.get("first_vocab_topic"),
        mode_stats=mode_stats,
    )


def save_onboarding_data(
    target_toefl: int,
    grammar_level: str,
    first_vocab_topic: str,
) -> bool:
    """
    Simpan data onboarding ke tabel users.
    Dipanggil oleh pages/dashboard.py setelah user menyelesaikan 3 step.

    Validasi ringan dilakukan di sini (bukan di UI) agar konsisten.

    Args:
        target_toefl     : Target skor TOEFL ITP, range 310–677
        grammar_level    : "Pemula" | "Intermediate" | "Advanced"
        first_vocab_topic: Nama topik vocab dari daftar yang tersedia

    Returns:
        True jika berhasil disimpan, False jika gagal
    """
    # Validasi minimal
    if not (310 <= target_toefl <= 677):
        logger.warning(f"[router] Invalid target_toefl={target_toefl}, clamping to range")
        target_toefl = max(310, min(677, target_toefl))

    valid_levels = {"Pemula", "Intermediate", "Advanced"}
    if grammar_level not in valid_levels:
        logger.warning(f"[router] Invalid grammar_level='{grammar_level}', defaulting to Pemula")
        grammar_level = "Pemula"

    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO users (target_toefl, grammar_level, first_vocab_topic)
                VALUES (?, ?, ?)
                """,
                (target_toefl, grammar_level, first_vocab_topic),
            )
        logger.info(
            f"[router] Onboarding saved — target={target_toefl}, "
            f"level={grammar_level}, topic={first_vocab_topic}"
        )
        return True

    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="router",
            context=str(e),
        )
        return False


def update_user_profile(
    target_toefl: Optional[int] = None,
    grammar_level: Optional[str] = None,
    first_vocab_topic: Optional[str] = None,
) -> bool:
    """
    Update profil user yang sudah ada.
    Hanya field yang diberikan (bukan None) yang diupdate.

    Dipanggil dari settings/profile page jika user ingin ubah target.
    """
    user = _get_user()
    if not user:
        logger.warning("[router] update_user_profile called but no user exists")
        return False

    new_target = target_toefl if target_toefl is not None else user["target_toefl"]
    new_level = grammar_level if grammar_level is not None else user["grammar_level"]
    new_topic = first_vocab_topic if first_vocab_topic is not None else user["first_vocab_topic"]

    try:
        with get_db() as conn:
            conn.execute(
                """
                UPDATE users
                SET target_toefl = ?,
                    grammar_level = ?,
                    first_vocab_topic = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (new_target, new_level, new_topic, user["id"]),
            )
        logger.info(f"[router] User profile updated (id={user['id']})")
        return True

    except Exception as e:
        log_error(
            error_type="db_error",
            agent_name="router",
            context=str(e),
        )
        return False
