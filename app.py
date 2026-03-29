"""
app.py
------
Entry point utama Streamlit untuk English Learning AI Agent.

Cara menjalankan:
    streamlit run app.py

Arsitektur navigasi:
    1. DB di-init sekali saat pertama kali app dibuka
    2. RoutingContext di-load setiap navigasi, di-cache di session_state
    3. Sidebar di-render dengan index sinkron ke session_state["sidebar_nav"]
    4. Router Guard — setiap mode page dicek: jika user belum onboarding,
       redirect ke Dashboard sebelum halaman mode dibuka
    5. Setiap page di-import dan dipanggil main() di routing branch-nya

Router Guard penting sebagai safety net:
    User baru yang entah bagaimana langsung klik mode page
    (misal via URL atau shortcut) akan di-redirect ke onboarding
    di Dashboard, bukan menemui error.

Context passing:
    RoutingContext tersedia di st.session_state["routing_ctx"].
    Setiap page yang butuh data user (target_toefl, grammar_level, dll.)
    bisa ambil dari sini tanpa perlu query DB sendiri.
"""

import streamlit as st

from database.connection import init_database
from agents.orchestrator.router import RoutingContext, get_routing_context

# ===================================================
# Konfigurasi halaman — HARUS paling pertama
# ===================================================
st.set_page_config(
    page_title="English Learning AI Agent",
    page_icon="🎓",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ===================================================
# Hide Streamlit auto-generated multi-page nav
# (muncul karena ada banyak .py di folder yang sama)
# Streamlit 1.44 sudah deprecated hideSidebarNav di config.toml
# sehingga solusinya inject CSS langsung
# ===================================================
st.markdown(
    """
    <style>
    [data-testid="stSidebarNav"] { display: none; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ===================================================
# Init DB — sekali saja saat app start
# ===================================================

init_database()

# ===================================================
# Load RoutingContext — simpan di session state
# Di-refresh setiap navigasi agar stats per mode up-to-date.
# DB query ringan (bukan LLM), aman dipanggil setiap rerun.
# ===================================================

ctx: RoutingContext = get_routing_context()
st.session_state["routing_ctx"] = ctx

# ===================================================
# Daftar halaman
# ===================================================
PAGES = [
    "🏠 Dashboard",
    "📚 Vocab Agent",
    "📝 Quiz Agent",
    "🎤 Speaking Agent",
    "📊 TOEFL Simulator",
]

# ===================================================
# Sidebar navigasi
# ===================================================
with st.sidebar:
    st.title("🎓 English Learning")
    st.caption("AI-powered TOEFL ITP preparation")
    st.markdown("---")

    # Profil ringkas — konteks cepat untuk user yang sudah onboarding
    if not ctx.needs_onboarding:
        level = ctx.grammar_level or "—"
        target = ctx.target_toefl or "—"
        st.markdown(
            f"<div style='background:#1e2a3a; padding:10px; border-radius:8px; " f"margin-bottom:12px; font-size:0.85em;'>" f"📚 <b>{level}</b> &nbsp;|&nbsp; 🎯 Target: <b>{target}</b>" f"</div>",
            unsafe_allow_html=True,
        )

    # Radio dengan index sinkron ke session_state["sidebar_nav"].
    # Ini memastikan tombol navigasi di Dashboard (yang menulis ke
    # session_state["sidebar_nav"]) langsung berdampak ke radio.
    # Proses nav request dari Dashboard sebelum widget di-render
    if "_nav_request" in st.session_state:
        st.session_state["sidebar_nav"] = st.session_state.pop("_nav_request")

    current_nav = st.session_state.get("sidebar_nav", "🏠 Dashboard")
    current_idx = PAGES.index(current_nav) if current_nav in PAGES else 0

    page = st.radio(
        "Pilih Mode:",
        options=PAGES,
        index=current_idx,
        label_visibility="collapsed",
        key="sidebar_nav",
    )

    st.markdown("---")

    # Quick stats ringkas di sidebar — tampil setelah onboarding
    if not ctx.needs_onboarding:
        stats = ctx.mode_stats
        mode_icons = {
            "vocab": "📚",
            "quiz": "📝",
            "speaking": "🎤",
            "toefl": "📊",
        }
        lines = []
        for mode, icon in mode_icons.items():
            total = stats.get(mode, {}).get("total_sessions", 0)
            if total > 0:
                lines.append(f"{icon} {total}")
        if lines:
            st.caption("Sesi: " + "  ·  ".join(lines))
        else:
            st.caption("Belum ada sesi latihan")

    st.markdown("---")
    st.caption("Phase 6 — Orchestrator & Dashboard aktif")


# ===================================================
# Router Guard
# ===================================================
def _require_onboarding() -> bool:
    """
    Cek apakah user sudah onboarding.

    Jika belum:
      - Tampilkan pesan informatif
      - Set sidebar_nav ke Dashboard
      - Set dashboard state ke onboarding
      - Panggil st.rerun() agar sidebar sync
      - Return False (kode routing di bawah tidak jalan)

    Jika sudah:
      - Return True, page boleh di-render

    Catatan: st.stop() tidak dipanggil karena st.rerun()
    sudah menghentikan eksekusi saat ini.
    """
    if ctx.needs_onboarding:
        st.info("👋 **Selamat datang!** Selesaikan setup profil singkat " "di Dashboard sebelum mulai latihan.")
        st.caption("Kamu akan diarahkan ke Dashboard secara otomatis.")

        # Redirect sidebar ke Dashboard
        st.session_state["sidebar_nav"] = "🏠 Dashboard"

        # Pastikan dashboard membuka state onboarding
        st.session_state["db_state"] = "onboarding"
        st.session_state["db_ob_step"] = 1

        st.rerun()
        return False  # Tidak pernah tercapai, tapi untuk kejelasan flow

    return True


# ===================================================
# Routing ke halaman yang dipilih
# ===================================================
if page == "🏠 Dashboard":
    # Dashboard menangani onboarding sendiri — tidak perlu guard di sini.
    # Invalidate cache Layer 1 & 2 setiap kali user kembali ke Dashboard
    # agar data selalu fresh setelah sesi di mode lain selesai.
    prev = st.session_state.get("_prev_page", "")
    if prev != "🏠 Dashboard":
        st.session_state.pop("db_l1", None)
        st.session_state.pop("db_l2", None)
        st.session_state.pop("db_ctx", None)
    st.session_state["_prev_page"] = "🏠 Dashboard"

    from pages.dashboard import main as dashboard_main

    dashboard_main()

elif page == "📚 Vocab Agent":
    if _require_onboarding():
        st.session_state["_prev_page"] = "📚 Vocab Agent"
        from pages.vocab import main as vocab_main

        vocab_main()

elif page == "📝 Quiz Agent":
    if _require_onboarding():
        st.session_state["_prev_page"] = "📝 Quiz Agent"
        from pages.quiz import main as quiz_main

        quiz_main()

elif page == "🎤 Speaking Agent":
    if _require_onboarding():
        st.session_state["_prev_page"] = "🎤 Speaking Agent"
        from pages.speaking import main as speaking_main

        speaking_main()

elif page == "📊 TOEFL Simulator":
    if _require_onboarding():
        st.session_state["_prev_page"] = "📊 TOEFL Simulator"
        from pages.toefl import main as toefl_main

        toefl_main()
