"""
pages/dashboard.py
-------------------
Halaman Dashboard — entry point utama setelah app.py.

Dua state utama:
  "onboarding" → User baru: 3-step form, simpan ke DB, redirect ke home
  "home"       → Dashboard utama: quick snapshot + navigasi ke semua mode

Onboarding 3 step:
  Step 1: Target skor TOEFL ITP (slider 310–677)
  Step 2: Grammar level (Pemula / Intermediate / Advanced)
  Step 3: Topik vocab pertama (dropdown dari daftar topik)

Dashboard home menampilkan:
  - Sapaan + progress ringkas
  - Quick snapshot per mode (total sesi + skor terakhir/terbaik)
  - Tombol navigasi ke setiap mode (diteruskan ke app.py via session state)
  - Jika ada target TOEFL, tampilkan gap antara best score dan target

Onboarding tidak pakai st.form agar bisa multi-step dengan state
yang tersimpan di session_state antar step.
"""

import streamlit as st

from agents.orchestrator.router import (
    RoutingContext,
    get_routing_context,
    save_onboarding_data,
    update_user_profile,
)

# ===================================================
# Daftar topik vocab tersedia (sesuai knowledge_base/)
# ===================================================
VOCAB_TOPICS = [
    "sehari_hari",
    "perkenalan",
    "keluarga",
    "pekerjaan",
    "pendidikan",
    "kesehatan",
    "teknologi",
    "lingkungan",
    "budaya",
    "perjalanan",
    "makanan",
    "olahraga",
    "ekonomi",
    "politik",
    "seni",
]

VOCAB_TOPIC_LABELS = {
    "sehari_hari" : "Kehidupan Sehari-hari",
    "perkenalan"  : "Perkenalan",
    "keluarga"    : "Keluarga",
    "pekerjaan"   : "Pekerjaan & Karir",
    "pendidikan"  : "Pendidikan",
    "kesehatan"   : "Kesehatan",
    "teknologi"   : "Teknologi",
    "lingkungan"  : "Lingkungan Hidup",
    "budaya"      : "Budaya & Tradisi",
    "perjalanan"  : "Perjalanan",
    "makanan"     : "Makanan & Kuliner",
    "olahraga"    : "Olahraga",
    "ekonomi"     : "Ekonomi & Bisnis",
    "politik"     : "Politik & Sosial",
    "seni"        : "Seni & Hiburan",
}

GRAMMAR_LEVELS = ["Pemula", "Intermediate", "Advanced"]

GRAMMAR_LEVEL_HINTS = {
    "Pemula"       : "Baru mulai belajar grammar bahasa Inggris",
    "Intermediate" : "Sudah paham dasar, ingin tingkatkan ke level lebih tinggi",
    "Advanced"     : "Sudah kuat di grammar, fokus ke soal TOEFL level tinggi",
}


# ===================================================
# Session state helpers — prefix "db_"
# ===================================================
def _get(key, default=None):
    return st.session_state.get(f"db_{key}", default)


def _set(key, value):
    st.session_state[f"db_{key}"] = value


def _reset_onboarding():
    keys = [k for k in st.session_state if k.startswith("db_ob_")]
    for k in keys:
        del st.session_state[k]


# ===================================================
# Onboarding: Step 1 — Target TOEFL
# ===================================================
def _render_onboarding_step1():
    st.title("👋 Selamat Datang!")
    st.markdown(
        "Sebelum mulai, kami perlu tahu sedikit tentang kamu "
        "agar latihan bisa dipersonalisasi."
    )
    st.markdown("---")

    st.markdown("### Step 1 dari 3 — Target Skor TOEFL ITP")
    st.caption("Berapa skor TOEFL ITP yang ingin kamu capai?")

    target = st.slider(
        label="Target Skor",
        min_value=310,
        max_value=677,
        value=_get("ob_target", 500),
        step=10,
        key="ob_slider_target",
        label_visibility="collapsed",
    )

    # Hint berdasarkan nilai target
    if target < 450:
        hint = "🟡 Cukup untuk persyaratan umum."
    elif target < 550:
        hint = "🟠 Target rata-rata untuk banyak program S1/S2."
    elif target < 600:
        hint = "🔴 Target kompetitif untuk program bergengsi."
    else:
        hint = "🔥 Target tinggi! Butuh persiapan intensif."

    st.info(f"Target: **{target}** — {hint}")

    st.markdown("")
    if st.button("Lanjut →", type="primary", use_container_width=True):
        _set("ob_target", target)
        _set("ob_step", 2)
        st.rerun()


# ===================================================
# Onboarding: Step 2 — Grammar Level
# ===================================================
def _render_onboarding_step2():
    st.title("👋 Selamat Datang!")
    st.markdown("---")
    st.markdown("### Step 2 dari 3 — Level Grammar Sekarang")
    st.caption("Pilih level yang paling menggambarkan kemampuan grammar kamu saat ini.")

    selected_level = _get("ob_level", GRAMMAR_LEVELS[0])

    for level in GRAMMAR_LEVELS:
        hint    = GRAMMAR_LEVEL_HINTS[level]
        is_sel  = selected_level == level
        border  = "2px solid #1f77b4" if is_sel else "1px solid #ddd"

        st.markdown(
            f"<div style='padding:12px; border-radius:8px; "
            f"border:{border}; margin-bottom:8px;'>"
            f"<strong>{level}</strong><br/>"
            f"<small style='color:gray;'>{hint}</small>"
            f"</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            f"{'✅ ' if is_sel else ''}Pilih {level}",
            key=f"ob_level_{level}",
            use_container_width=True,
        ):
            _set("ob_level", level)
            st.rerun()

    st.markdown("")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Kembali", use_container_width=True):
            _set("ob_step", 1)
            st.rerun()
    with col2:
        if st.button("Lanjut →", type="primary", use_container_width=True):
            if not _get("ob_level"):
                st.warning("Pilih level terlebih dahulu.")
            else:
                _set("ob_step", 3)
                st.rerun()


# ===================================================
# Onboarding: Step 3 — Topik Vocab Pertama
# ===================================================
def _render_onboarding_step3():
    st.title("👋 Selamat Datang!")
    st.markdown("---")
    st.markdown("### Step 3 dari 3 — Topik Vocab Pertama")
    st.caption(
        "Pilih topik yang ingin kamu pelajari di sesi Vocab pertama. "
        "Kamu bisa ganti topik kapan saja nanti."
    )

    topic_options  = VOCAB_TOPICS
    topic_labels   = [VOCAB_TOPIC_LABELS.get(t, t) for t in topic_options]
    default_idx    = topic_options.index(_get("ob_topic", "sehari_hari"))

    selected_label = st.selectbox(
        label="Pilih Topik:",
        options=topic_labels,
        index=default_idx,
        key="ob_topic_select",
        label_visibility="collapsed",
    )
    selected_topic = topic_options[topic_labels.index(selected_label)]
    _set("ob_topic", selected_topic)

    st.markdown("")

    # Ringkasan sebelum submit
    target = _get("ob_target", 500)
    level  = _get("ob_level", "Pemula")
    st.success(
        f"**Ringkasan:**\n\n"
        f"🎯 Target skor: **{target}**\n\n"
        f"📚 Grammar level: **{level}**\n\n"
        f"🔤 Topik vocab pertama: **{selected_label}**"
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Kembali", use_container_width=True):
            _set("ob_step", 2)
            st.rerun()
    with col2:
        if st.button("✅ Mulai Belajar!", type="primary", use_container_width=True):
            success = save_onboarding_data(
                target_toefl      = target,
                grammar_level     = level,
                first_vocab_topic = selected_topic,
            )
            if success:
                _reset_onboarding()
                _set("state", "home")
                st.balloons()
                st.rerun()
            else:
                st.error(
                    "Gagal menyimpan data. Coba lagi atau restart aplikasi."
                )


# ===================================================
# Dashboard: Home
# ===================================================
def _render_home(ctx: RoutingContext):
    st.title("🎓 English Learning AI Agent")

    # Gap indicator jika ada data TOEFL
    best_toefl = ctx.mode_stats.get("toefl", {}).get("best_score")
    if best_toefl and ctx.target_toefl:
        gap = ctx.target_toefl - best_toefl
        if gap > 0:
            st.info(
                f"🎯 Target kamu: **{ctx.target_toefl}** | "
                f"Estimasi terbaik: **{best_toefl}** | "
                f"Gap: **{gap} poin**"
            )
        else:
            st.success(
                f"🏆 Target **{ctx.target_toefl}** sudah tercapai! "
                f"Estimasi terbaik: **{best_toefl}**"
            )

    st.markdown("---")
    st.markdown("### Mode Latihan")

    # Grid 2x2 mode cards
    col1, col2 = st.columns(2)

    with col1:
        _render_mode_card(
            icon="📚",
            title="Vocab Agent",
            description="Latihan kosakata dengan spaced repetition",
            stats=ctx.mode_stats.get("vocab", {}),
            stat_label="Skor terakhir",
            stat_key="last_score",
            stat_suffix="%",
            mode_key="📚 Vocab Agent",
        )

    with col2:
        _render_mode_card(
            icon="📝",
            title="Quiz Agent",
            description="Grammar quiz dengan 4-layer feedback",
            stats=ctx.mode_stats.get("quiz", {}),
            stat_label="Skor terakhir",
            stat_key="last_score",
            stat_suffix="%",
            mode_key="📝 Quiz Agent",
        )

    col3, col4 = st.columns(2)

    with col3:
        _render_mode_card(
            icon="🎤",
            title="Speaking Agent",
            description="Conversation practice & oral presentation",
            stats=ctx.mode_stats.get("speaking", {}),
            stat_label="Skor terakhir",
            stat_key="last_score",
            stat_suffix="/100",
            mode_key="🎤 Speaking Agent",
        )

    with col4:
        _render_mode_card(
            icon="📊",
            title="TOEFL Simulator",
            description="Simulasi TOEFL ITP dengan estimasi skor resmi",
            stats=ctx.mode_stats.get("toefl", {}),
            stat_label="Estimasi terbaik",
            stat_key="best_score",
            stat_suffix="",
            mode_key="📊 TOEFL Simulator",
        )

    st.markdown("---")

    # Edit profil (collapsible)
    with st.expander("⚙️ Edit Profil & Target"):
        _render_profile_editor(ctx)


def _render_mode_card(
    icon: str,
    title: str,
    description: str,
    stats: dict,
    stat_label: str,
    stat_key: str,
    stat_suffix: str,
    mode_key: str,
):
    """Render satu mode card dengan stats dan tombol navigasi."""
    total    = stats.get("total_sessions", 0)
    stat_val = stats.get(stat_key)

    with st.container(border=True):
        st.markdown(f"### {icon} {title}")
        st.caption(description)

        if total > 0:
            val_str = f"{stat_val:.0f}{stat_suffix}" if stat_val is not None else "—"
            st.markdown(
                f"<small style='color:gray;'>"
                f"{total} sesi &nbsp;|&nbsp; {stat_label}: <b>{val_str}</b>"
                f"</small>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<small style='color:gray;'>Belum ada sesi</small>",
                unsafe_allow_html=True,
            )

        st.markdown("")
        if st.button(f"Buka {title}", key=f"nav_{mode_key}", use_container_width=True):
            # Tulis ke session state — app.py akan routing ke sini
            st.session_state["sidebar_nav"] = mode_key
            st.rerun()


def _render_profile_editor(ctx: RoutingContext):
    """Form edit profil user yang sudah onboarding."""
    st.markdown("**Ubah Target & Profil**")

    new_target = st.slider(
        "Target Skor TOEFL ITP",
        min_value=310,
        max_value=677,
        value=ctx.target_toefl or 500,
        step=10,
        key="profile_target",
    )

    new_level = st.selectbox(
        "Grammar Level",
        options=GRAMMAR_LEVELS,
        index=GRAMMAR_LEVELS.index(ctx.grammar_level) if ctx.grammar_level in GRAMMAR_LEVELS else 0,
        key="profile_level",
    )

    topic_labels  = [VOCAB_TOPIC_LABELS.get(t, t) for t in VOCAB_TOPICS]
    current_topic = ctx.first_vocab_topic or "sehari_hari"
    default_idx   = VOCAB_TOPICS.index(current_topic) if current_topic in VOCAB_TOPICS else 0

    new_topic_label = st.selectbox(
        "Topik Vocab Default",
        options=topic_labels,
        index=default_idx,
        key="profile_topic",
    )
    new_topic = VOCAB_TOPICS[topic_labels.index(new_topic_label)]

    if st.button("💾 Simpan Perubahan", type="primary"):
        success = update_user_profile(
            target_toefl      = new_target,
            grammar_level     = new_level,
            first_vocab_topic = new_topic,
        )
        if success:
            st.success("✅ Profil berhasil diperbarui. Halaman akan di-refresh.")
            # Reset routing context cache di session state
            if "db_ctx" in st.session_state:
                del st.session_state["db_ctx"]
            st.rerun()
        else:
            st.error("Gagal menyimpan. Coba lagi.")


# ===================================================
# Entry point
# ===================================================
def main():
    # Load routing context (dengan simple cache di session state)
    ctx = st.session_state.get("db_ctx")
    if ctx is None:
        ctx = get_routing_context()
        st.session_state["db_ctx"] = ctx

    # Tentukan state awal
    if _get("state") is None:
        if ctx.needs_onboarding:
            _set("state", "onboarding")
            _set("ob_step", 1)
        else:
            _set("state", "home")

    state = _get("state")

    if state == "onboarding":
        step = _get("ob_step", 1)
        if step == 1:
            _render_onboarding_step1()
        elif step == 2:
            _render_onboarding_step2()
        elif step == 3:
            _render_onboarding_step3()

    elif state == "home":
        # Refresh context setiap kali home dibuka agar stats terbaru
        ctx = get_routing_context()
        st.session_state["db_ctx"] = ctx
        _render_home(ctx)

    else:
        st.error(f"State tidak dikenal: {state}")
        if st.button("Reset Dashboard"):
            keys = [k for k in st.session_state if k.startswith("db_")]
            for k in keys:
                del st.session_state[k]
            st.rerun()