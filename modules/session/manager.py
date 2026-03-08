"""
modules/session/manager.py
--------------------------
Session Manager: jembatan antara Streamlit session state dan database.

Masalah yang diselesaikan:
- Streamlit session_state: cepat, di-memory, HILANG saat refresh/error
- SQLite DB: persisten, tapi lebih lambat
- Solution: setiap aksi penting langsung disimpan ke DB (incremental save)

Flow normal:
    init_session("vocab")
        → buat record di DB
        → init st.session_state["current_session"]
    
    save_progress(session_id, data)
        → update st.session_state (untuk UI responsif)
        → simpan ke DB (untuk persistensi)
    
    complete_session(session_id)
        → update status DB ke "completed"
        → clear session state

Flow error/abandon:
    abandon_session(session_id)
        → update status DB ke "abandoned"
        → clear session state
"""

import streamlit as st
from typing import Optional

from database.repositories.session_repository import (
    create_session,
    get_session,
    update_session_status,
    get_sessions_by_mode,
)
from utils.helpers import generate_session_id
from utils.logger import logger


# Key di Streamlit session_state untuk menyimpan data sesi aktif
_ST_SESSION_KEY = "current_session"


def init_session(mode: str) -> dict:
    """
    Inisialisasi sesi baru: buat di DB + init Streamlit state.

    Args:
        mode: Mode sesi — "vocab" | "quiz" | "speaking" | "toefl"

    Returns:
        dict session yang baru dibuat:
        {session_id, mode, status, created_at, ...}

    Contoh:
        session = init_session("vocab")
        st.session_state["vocab_plan"] = planner_result
    """
    session_id = generate_session_id()

    try:
        session = create_session(session_id, mode)
    except Exception as e:
        logger.error(f"[session_manager] Gagal create session di DB: {e}")
        raise

    # Simpan ke Streamlit state untuk akses cepat di UI
    st.session_state[_ST_SESSION_KEY] = {
        "session_id": session_id,
        "mode": mode,
        "status": "active",
        "data": {},  # Slot untuk data tambahan per mode
    }

    logger.info(f"[session_manager] Session dibuat: {session_id} | mode={mode}")
    return session


def save_progress(session_id: str, data: dict) -> None:
    """
    Incremental save: update Streamlit state + simpan ke DB.

    Dipanggil setelah setiap aksi penting (jawab soal, generate konten, dll).
    Jangan tunggu sampai akhir sesi untuk save pertama kali.

    Args:
        session_id : ID sesi yang sedang berjalan
        data       : Dict data yang ingin disimpan ke session state.
                     Contoh: {"current_question_index": 3, "score": 70}

    Catatan:
        Fungsi ini hanya update session_state dan flag di DB.
        Penyimpanan data spesifik (soal, jawaban, skor) dilakukan
        oleh repository masing-masing agent — bukan di sini.
    """
    # Update Streamlit state
    if _ST_SESSION_KEY in st.session_state:
        st.session_state[_ST_SESSION_KEY]["data"].update(data)
    else:
        # State hilang (misal setelah refresh) — rebuild minimal
        st.session_state[_ST_SESSION_KEY] = {
            "session_id": session_id,
            "data": data,
        }

    logger.debug(f"[session_manager] Progress saved: {session_id} | keys={list(data.keys())}")


def complete_session(session_id: str) -> bool:
    """
    Tandai sesi sebagai selesai (completed) di DB + clear state.

    Args:
        session_id: ID sesi yang selesai

    Returns:
        True jika berhasil update DB, False jika gagal
    """
    try:
        success = update_session_status(session_id, status="completed")
    except Exception as e:
        logger.error(f"[session_manager] Gagal complete session {session_id}: {e}")
        success = False

    # Clear session state regardless
    _clear_session_state()

    if success:
        logger.info(f"[session_manager] Session completed: {session_id}")
    return success


def abandon_session(session_id: str, reason: Optional[str] = None) -> bool:
    """
    Tandai sesi sebagai ditinggalkan (abandoned) di DB + clear state.

    Dipanggil saat:
    - User keluar di tengah sesi
    - Error fatal yang tidak bisa di-recover
    - User pilih mode lain sebelum sesi selesai

    Args:
        session_id : ID sesi yang ditinggalkan
        reason     : Alasan abandon (opsional, untuk flag di DB)

    Returns:
        True jika berhasil update DB
    """
    try:
        success = update_session_status(
            session_id,
            status="abandoned",
            is_flagged=bool(reason),
            flag_reason=reason,
        )
    except Exception as e:
        logger.error(f"[session_manager] Gagal abandon session {session_id}: {e}")
        success = False

    _clear_session_state()

    if reason:
        logger.warning(f"[session_manager] Session abandoned: {session_id} | reason={reason}")
    else:
        logger.info(f"[session_manager] Session abandoned: {session_id}")

    return success


def get_active_session(mode: str) -> Optional[dict]:
    """
    Cek apakah ada sesi aktif untuk mode tertentu.

    Cek dua sumber:
    1. Streamlit session_state (cepat, untuk UI)
    2. DB (akurat, untuk validasi)

    Args:
        mode: Mode yang dicek — "vocab" | "quiz" | "speaking" | "toefl"

    Returns:
        dict session jika ada sesi aktif, None jika tidak ada.

    Penggunaan di halaman Streamlit:
        active = get_active_session("vocab")
        if active:
            st.warning("Kamu masih punya sesi vocab yang belum selesai.")
            # Tawarkan: lanjutkan atau mulai baru
    """
    # Cek session_state dulu (lebih cepat)
    if _ST_SESSION_KEY in st.session_state:
        current = st.session_state[_ST_SESSION_KEY]
        if current.get("mode") == mode and current.get("status") == "active":
            return current

    # Fallback: cek DB untuk sesi active terbaru di mode ini
    try:
        sessions = get_sessions_by_mode(mode, limit=1)
        for session in sessions:
            if session.get("status") == "active":
                return dict(session)
    except Exception as e:
        logger.error(f"[session_manager] Gagal cek active session: {e}")

    return None


def get_current_session_id() -> Optional[str]:
    """
    Shortcut untuk ambil session_id dari Streamlit state.

    Returns:
        session_id string jika ada sesi aktif, None jika tidak.
    """
    if _ST_SESSION_KEY in st.session_state:
        return st.session_state[_ST_SESSION_KEY].get("session_id")
    return None


def _clear_session_state() -> None:
    """Hapus data sesi dari Streamlit state."""
    if _ST_SESSION_KEY in st.session_state:
        del st.session_state[_ST_SESSION_KEY]