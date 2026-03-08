"""
app.py
------
Entry point utama Streamlit untuk English Learning AI Agent.

Cara menjalankan:
    streamlit run app.py

Navigasi:
    - Sidebar untuk pindah antar mode
    - Setiap mode adalah halaman terpisah di folder pages/
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
        options=["🏠 Dashboard", "📚 Vocab Agent", "📝 Quiz Agent",
                 "🎤 Speaking Agent", "📊 TOEFL Simulator"],
        label_visibility="collapsed",
        key="sidebar_nav",
    )
    st.markdown("---")
    st.caption("Phase 2 — Vocab Agent aktif")

# ===================================================
# Routing ke halaman yang dipilih
# ===================================================
if page == "🏠 Dashboard":
    st.title("🎓 English Learning AI Agent")
    st.markdown(
        "Selamat datang! Pilih mode latihan dari sidebar untuk memulai."
    )

    col1, col2 = st.columns(2)
    with col1:
        st.info("📚 **Vocab Agent**\nLatihan kosakata dengan spaced repetition")
        st.warning("📝 **Quiz Agent**\n_Coming soon — Phase 3_")
    with col2:
        st.warning("🎤 **Speaking Agent**\n_Coming soon — Phase 4_")
        st.warning("📊 **TOEFL Simulator**\n_Coming soon — Phase 5_")

elif page == "📚 Vocab Agent":
    # Import dan jalankan halaman vocab
    from pages.vocab import main as vocab_main
    vocab_main()

elif page in ["📝 Quiz Agent", "🎤 Speaking Agent", "📊 TOEFL Simulator"]:
    st.title(page)
    st.info("🚧 Fitur ini sedang dalam pengembangan. Coming soon!")