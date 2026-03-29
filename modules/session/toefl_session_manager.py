"""
modules/session/toefl_session_manager.py
-----------------------------------------
Orchestrator session management khusus TOEFL Simulator.

Masalah yang diselesaikan:
- TOEFL adalah satu-satunya mode yang mendukung pause/resume lintas sesi
- Pause hanya valid ANTAR section — tidak boleh di tengah section
- Resume harus cek expiry 7 hari sebelum lanjut
- Sesi expired → abandoned → dikecualikan dari analytics

Kenapa dipisah dari manager.py umum?
- Logic pause/resume TOEFL terlalu spesifik untuk dicampur ke manager umum
- pages/toefl.py cukup import modul ini tanpa perlu tahu detail DB

Flow pause:
    user klik "Pause" setelah selesai Listening
        → is_section_complete() → True
        → pause_session() → simpan ke DB, set expires_at
        → return PauseResult(success=True, expires_at=...)

Flow resume:
    user buka app, ada sesi paused
        → resume_session(session_id)
        → check_and_resume_toefl_session() di repository
        → expired? → return ResumeResult(success=False, reason='expired')
        → valid?   → return ResumeResult(success=True, state=...)

Konstanta:
    PAUSE_EXPIRY_DAYS = 7   — window resume sebelum sesi abandoned
    SECTION_ORDER     = [1, 2, 3]   — 1=Listening, 2=Structure, 3=Reading
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from database.repositories.session_repository import (
    check_and_resume_toefl_session,
    pause_toefl_session,
)
from database.repositories.toefl_repository import (
    get_toefl_session,
)
from utils.logger import log_error, logger

# ===================================================
# Konstanta
# ===================================================
PAUSE_EXPIRY_DAYS = 7

# Urutan section: 1=Listening, 2=Structure, 3=Reading
SECTION_ORDER = [1, 2, 3]
SECTION_NAMES = {
    1: "Listening",
    2: "Structure",
    3: "Reading",
}

# Jumlah soal yang diharapkan per section per mode
# Dipakai is_section_complete() untuk validasi
SECTION_TOTALS = {
    "50%": {1: 25, 2: 20, 3: 25},
    "75%": {1: 38, 2: 30, 3: 37},
    "100%": {1: 50, 2: 40, 3: 50},
}


# ===================================================
# Result dataclasses — menghindari return tuple
# ===================================================
@dataclass
class PauseResult:
    success: bool
    expires_at: Optional[str] = None
    reason: Optional[str] = None  # diisi jika success=False


@dataclass
class ResumeResult:
    success: bool
    state: Optional[dict] = None  # diisi jika success=True
    reason: Optional[str] = None  # diisi jika success=False
    expires_at: Optional[str] = None  # untuk tampilkan di UI saat success


# ===================================================
# Helper internal
# ===================================================
def _now_str() -> str:
    """Return timestamp sekarang sebagai string 'YYYY-MM-DD HH:MM:SS'."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _expires_str(from_dt: datetime, days: int = PAUSE_EXPIRY_DAYS) -> str:
    """Hitung expires_at = from_dt + days hari, return sebagai string."""
    return (from_dt + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


# ===================================================
# Fungsi utama
# ===================================================
def is_section_complete(session_id: str, section: int, mode: str) -> bool:
    """
    Validasi apakah sebuah section sudah benar-benar selesai dikerjakan.

    Cara kerja:
    - Ambil semua soal section ini dari DB
    - Hitung yang sudah ada user_answer-nya
    - Bandingkan dengan total soal yang diharapkan untuk mode ini

    Args:
        session_id: UUID sesi TOEFL
        section   : Nomor section yang dicek (1=Listening, 2=Structure, 3=Reading)
        mode      : Mode simulasi ('50%', '75%', '100%')

    Returns:
        True jika semua soal section sudah dijawab, False jika belum
    """
    toefl_data = get_toefl_session(session_id)
    if not toefl_data:
        return False

    # Hitung soal section ini yang sudah dijawab
    answered = sum(1 for q in toefl_data.get("questions", []) if q.get("section") == str(section) and q.get("user_answer") is not None)

    expected = SECTION_TOTALS.get(mode, {}).get(section, 0)

    is_complete = answered >= expected

    if not is_complete:
        logger.warning(f"[toefl_session_manager] Section {section} belum selesai: " f"{answered}/{expected} soal dijawab (session={session_id})")

    return is_complete


def pause_session(session_id: str, completed_section: int, mode: str) -> PauseResult:
    """
    Pause sesi TOEFL setelah selesai mengerjakan satu section.

    Validasi:
    1. Hanya boleh pause setelah Listening (section 1) atau Structure (section 2)
       — pause setelah Reading tidak berguna karena itu section terakhir
    2. Section yang diklaim selesai harus benar-benar selesai (semua soal terjawab)

    Args:
        session_id        : UUID sesi
        completed_section : Section yang baru selesai (1 atau 2)
        mode              : Mode simulasi ('50%', '75%', '100%')

    Returns:
        PauseResult dengan success=True dan expires_at jika berhasil
    """
    # Validasi: pause hanya valid setelah section 1 atau 2
    if completed_section not in [1, 2]:
        return PauseResult(
            success=False,
            reason=("Pause hanya bisa dilakukan setelah selesai Listening " "atau Structure section."),
        )

    # Validasi: section benar-benar sudah selesai
    if not is_section_complete(session_id, completed_section, mode):
        section_name = SECTION_NAMES.get(completed_section, f"Section {completed_section}")
        return PauseResult(
            success=False,
            reason=(f"{section_name} belum selesai. Selesaikan semua soal " f"sebelum melakukan pause."),
        )

    now_dt = datetime.now()
    paused_at = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    expires_at = _expires_str(now_dt)

    # Section berikutnya yang akan dilanjutkan saat resume
    next_section = completed_section + 1

    try:
        success = pause_toefl_session(
            session_id=session_id,
            current_section=next_section,
            paused_at=paused_at,
            expires_at=expires_at,
        )

        if success:
            section_name = SECTION_NAMES.get(completed_section)
            logger.info(f"[toefl_session_manager] Sesi di-pause setelah {section_name}. " f"Lanjut dari section {next_section} saat resume. " f"Expires: {expires_at} (session={session_id})")
            return PauseResult(success=True, expires_at=expires_at)

        return PauseResult(success=False, reason="Gagal menyimpan ke database.")

    except Exception as e:
        log_error(
            error_type="pause_failed",
            agent_name="toefl_session_manager",
            session_id=session_id,
            context=str(e),
        )
        return PauseResult(success=False, reason="Terjadi error saat pause.")


def resume_session(session_id: str) -> ResumeResult:
    """
    Resume sesi TOEFL yang di-pause.

    Flow:
    1. Panggil check_and_resume_toefl_session() — repository yang handle
       logika expired vs valid, dan update status di DB
    2. Jika None → sesi expired atau tidak valid
    3. Jika ada data → return state lengkap untuk di-load ke UI

    Args:
        session_id: UUID sesi yang ingin di-resume

    Returns:
        ResumeResult dengan success=True dan state jika masih valid,
        atau success=False dengan reason jika tidak bisa dilanjutkan
    """
    now = _now_str()

    try:
        state = check_and_resume_toefl_session(session_id=session_id, now=now)

        if state is None:
            logger.info(f"[toefl_session_manager] Resume gagal — sesi expired " f"atau tidak valid (session={session_id})")
            return ResumeResult(
                success=False,
                reason=("Sesi tidak dapat dilanjutkan. " "Kemungkinan sudah melewati batas 7 hari atau " "tidak dalam kondisi pause."),
            )

        logger.info(f"[toefl_session_manager] Resume berhasil. " f"Lanjut dari section {state.get('current_section')} " f"(session={session_id})")

        return ResumeResult(
            success=True,
            state=state,
            expires_at=state.get("expires_at"),
        )

    except Exception as e:
        log_error(
            error_type="resume_failed",
            agent_name="toefl_session_manager",
            session_id=session_id,
            context=str(e),
        )
        return ResumeResult(
            success=False,
            reason="Terjadi error saat mencoba resume sesi.",
        )


def get_paused_session_info(session_id: str) -> Optional[dict]:
    """
    Ambil info ringkas sesi yang di-pause untuk ditampilkan di UI.

    Dipakai pages/toefl.py untuk render banner "Anda punya sesi yang belum selesai"
    sebelum user memutuskan apakah akan resume atau mulai baru.

    Returns:
        dict berisi: session_id, mode, current_section, expires_at
        None jika tidak ditemukan atau sudah tidak paused
    """
    toefl_data = get_toefl_session(session_id)
    if not toefl_data:
        return None

    from database.repositories.session_repository import get_session

    session = get_session(session_id)

    if not session or session.get("status") != "paused":
        return None

    current_section = toefl_data.get("current_section", 1)

    return {
        "session_id": session_id,
        "mode": toefl_data.get("mode"),
        "current_section": current_section,
        "next_section_name": SECTION_NAMES.get(current_section, "Unknown"),
        "expires_at": session.get("expires_at"),
        # Sections yang sudah selesai
        "completed_sections": [SECTION_NAMES[s] for s in SECTION_ORDER if s < current_section],
    }


def is_session_abandoned(session_id: str) -> bool:
    """
    Cek apakah sesi sudah di-mark abandoned.
    Shortcut utility untuk pages/toefl.py.
    """
    from database.repositories.session_repository import get_session

    session = get_session(session_id)
    return session is not None and session.get("status") == "abandoned"
