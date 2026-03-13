"""
app.py
------
Entry point utama Streamlit untuk English Learning AI Agent.

Cara menjalankan:
    streamlit run app.py

Navigasi:
    - Sidebar untuk pindah antar mode
    - Setiap mode adalah halaman terpisah di folder pages/
    - Dashboard adalah halaman default yang juga menangani onboarding
"""

import streamlit as st
from database.connection import init_database

# Inisialisasi DB saat app pertama kali dibuka
init_database()

# ===================================================
# Konfigurasi halaman utama
# ===================================================
st.set_page_config(
    page_title="English Learning AI Agent",
    page_icon="🎓",
    layout="centered",
    initial_sidebar_state="expanded",
)

# ===================================================
# Sidebar navigasi
# ===================================================
with st.sidebar:
    st.title("🎓 English Learning")
    st.caption("AI-powered TOEFL ITP preparation")
    st.markdown("---")

    st.markdown("### Menu")
    page = st.radio(
        "Pilih Mode:",
        options=[
            "🏠 Dashboard",
            "📚 Vocab Agent",
            "📝 Quiz Agent",
            "🎤 Speaking Agent",
            "📊 TOEFL Simulator",
        ],
        label_visibility="collapsed",
        key="sidebar_nav",
    )
    st.markdown("---")
    st.caption("Phase 6 — Dashboard & Orchestrator aktif")

# ===================================================
# Routing ke halaman yang dipilih
# ===================================================
if page == "🏠 Dashboard":
    from pages.dashboard import main as dashboard_main
    dashboard_main()

elif page == "📚 Vocab Agent":
    from pages.vocab import main as vocab_main
    vocab_main()

elif page == "📝 Quiz Agent":
    from pages.quiz import main as quiz_main
    quiz_main()

elif page == "🎤 Speaking Agent":
    from pages.speaking import main as speaking_main
    speaking_main()

elif page == "📊 TOEFL Simulator":
    from pages.toefl import main as toefl_main
    toefl_main()