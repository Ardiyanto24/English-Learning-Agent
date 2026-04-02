"""
pages/dashboard.py
-------------------
Halaman Dashboard — entry point utama setelah app.py.

State machine:
  "onboarding" → User baru: 3-step form, simpan ke DB, redirect ke home
  "home"       → Dashboard utama dengan 3 layer

3 Layer Dashboard:
  Layer 1 — Quick Snapshot  (auto load, no LLM):
    Streak harian, last session, estimasi TOEFL terkini, coverage grammar

  Layer 2 — Per Mode Summary (auto load, no LLM):
    Vocab: kata dikuasai + kata lemah
    Quiz: topik terkuat & terlemah
    Speaking: rata-rata skor 3 sesi terakhir
    TOEFL: trend 5 simulasi terakhir

  Layer 3 — Deep Analysis (on-demand, LLM):
    Tombol "Minta Analisis Tutor AI"
    Panggil Master Analytics Agent
    Tampilkan cross-mode correlations + TOEFL readiness
"""

from datetime import datetime, timedelta
from typing import Optional

import streamlit as st

from agents.orchestrator.router import (
    RoutingContext,
    get_routing_context,
    save_onboarding_data,
    update_user_profile,
)
from database.connection import get_db

# ===================================================
# Konstanta
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
    "sehari_hari": "Kehidupan Sehari-hari",
    "perkenalan": "Perkenalan",
    "keluarga": "Keluarga",
    "pekerjaan": "Pekerjaan & Karir",
    "pendidikan": "Pendidikan",
    "kesehatan": "Kesehatan",
    "teknologi": "Teknologi",
    "lingkungan": "Lingkungan Hidup",
    "budaya": "Budaya & Tradisi",
    "perjalanan": "Perjalanan",
    "makanan": "Makanan & Kuliner",
    "olahraga": "Olahraga",
    "ekonomi": "Ekonomi & Bisnis",
    "politik": "Politik & Sosial",
    "seni": "Seni & Hiburan",
}
GRAMMAR_LEVELS = ["Pemula", "Intermediate", "Advanced"]
GRAMMAR_LEVEL_HINTS = {
    "Pemula": "Baru mulai belajar grammar bahasa Inggris",
    "Intermediate": "Sudah paham dasar, ingin tingkatkan ke level lebih tinggi",
    "Advanced": "Sudah kuat di grammar, fokus ke soal TOEFL level tinggi",
}
TOTAL_GRAMMAR_TOPICS = 46


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
# LAYER 1 — Data queries (no LLM)
# ===================================================
def _query_layer1() -> dict:
    """
    Query semua data untuk Layer 1.
    Semua query ringan dari tabel sessions + toefl_sessions + quiz_topic_tracking.
    """
    data = {
        "streak": 0,
        "last_session": None,  # dict: mode, completed_at, score
        "latest_toefl": None,  # int estimated_score
        "grammar_coverage": 0.0,  # float persen
        "topics_practiced": 0,
    }

    try:
        with get_db() as conn:

            # --- Streak: hitung hari berturut-turut ada completed session ---
            rows = conn.execute("""
                SELECT DATE(completed_at) as day
                FROM sessions
                WHERE status = 'completed' AND completed_at IS NOT NULL
                GROUP BY DATE(completed_at)
                ORDER BY day DESC
                """).fetchall()

            streak = 0
            if rows:
                today = datetime.now().date()
                check_date = today
                for row in rows:
                    row_date = datetime.strptime(row["day"], "%Y-%m-%d").date()
                    # Toleransi: hari ini atau kemarin (untuk user yang baru buka)
                    if row_date == check_date or (streak == 0 and row_date == today - timedelta(days=1)):
                        streak += 1
                        check_date = row_date - timedelta(days=1)
                    else:
                        break
            data["streak"] = streak

            # --- Last session ---
            last = conn.execute("""
                SELECT mode, completed_at, status
                FROM sessions
                WHERE status = 'completed'
                ORDER BY completed_at DESC
                LIMIT 1
                """).fetchone()
            if last:
                last_dict = dict(last)
                # Ambil skor dari tabel mode yang sesuai
                mode = last_dict.get("mode")
                score = None
                if mode == "vocab":
                    row = conn.execute("""SELECT score_pct FROM vocab_sessions
                           WHERE session_id IN (
                               SELECT session_id FROM sessions
                               WHERE status='completed' ORDER BY completed_at DESC LIMIT 1
                           )""").fetchone()
                    if row:
                        score = f"{row['score_pct']:.0f}%"
                elif mode == "quiz":
                    row = conn.execute("""SELECT score_pct FROM quiz_sessions
                           WHERE session_id IN (
                               SELECT session_id FROM sessions
                               WHERE status='completed' ORDER BY completed_at DESC LIMIT 1
                           )""").fetchone()
                    if row:
                        score = f"{row['score_pct']:.0f}%"
                elif mode == "speaking":
                    row = conn.execute("""SELECT final_score FROM speaking_sessions
                           WHERE session_id IN (
                               SELECT session_id FROM sessions
                               WHERE status='completed' ORDER BY completed_at DESC LIMIT 1
                           )""").fetchone()
                    if row and row["final_score"]:
                        score = f"{row['final_score']:.0f}/100"
                elif mode == "toefl":
                    row = conn.execute("""SELECT estimated_score FROM toefl_sessions
                           WHERE session_id IN (
                               SELECT session_id FROM sessions
                               WHERE status='completed' ORDER BY completed_at DESC LIMIT 1
                           )""").fetchone()
                    if row and row["estimated_score"]:
                        score = str(row["estimated_score"])

                # Format waktu
                completed_at = last_dict.get("completed_at", "")
                try:
                    dt = datetime.strptime(completed_at[:19], "%Y-%m-%d %H:%M:%S")
                    time_str = dt.strftime("%-d %b %Y, %H:%M")
                except Exception:
                    time_str = completed_at[:16] if completed_at else "—"

                data["last_session"] = {
                    "mode": mode,
                    "completed_at": time_str,
                    "score": score,
                }

            # --- Latest TOEFL estimated score ---
            toefl_row = conn.execute("""
                SELECT ts.estimated_score
                FROM toefl_sessions ts
                JOIN sessions s ON ts.session_id = s.session_id
                WHERE s.status = 'completed' AND ts.score_status = 'completed'
                ORDER BY s.completed_at DESC
                LIMIT 1
                """).fetchone()
            if toefl_row:
                data["latest_toefl"] = toefl_row["estimated_score"]

            # --- Grammar coverage ---
            cov_row = conn.execute("""
                SELECT COUNT(*) as practiced
                FROM quiz_topic_tracking
                WHERE total_sessions > 0
                """).fetchone()
            practiced = cov_row["practiced"] if cov_row else 0
            data["topics_practiced"] = practiced
            data["grammar_coverage"] = round((practiced / TOTAL_GRAMMAR_TOPICS) * 100, 1)

    except Exception as e:

        st.caption(f"⚠️ Gagal load Layer 1: {e}")

    return data


# ===================================================
# LAYER 2 — Data queries per mode (no LLM)
# ===================================================
def _query_layer2() -> dict:
    """Query data ringkas per mode untuk Layer 2."""
    data = {
        "vocab": None,
        "quiz": None,
        "speaking": None,
        "toefl": None,
    }

    try:
        with get_db() as conn:

            # --- Vocab ---
            mastered_row = conn.execute("SELECT COUNT(*) as cnt FROM vocab_word_tracking WHERE mastery_score >= 80").fetchone()
            weak_rows = conn.execute("""SELECT word, topic, mastery_score
                   FROM vocab_word_tracking
                   WHERE mastery_score < 60
                   ORDER BY mastery_score ASC
                   LIMIT 5""").fetchall()
            total_words = conn.execute("SELECT COUNT(*) as cnt FROM vocab_word_tracking").fetchone()

            data["vocab"] = {
                "total_tracked": total_words["cnt"] if total_words else 0,
                "mastered": mastered_row["cnt"] if mastered_row else 0,
                "weak_words": [dict(r) for r in weak_rows],
            }

            # --- Quiz ---
            strongest = conn.execute("""SELECT topic, avg_score_pct, cluster
                   FROM quiz_topic_tracking
                   WHERE total_sessions > 0
                   ORDER BY avg_score_pct DESC
                   LIMIT 3""").fetchall()
            weakest = conn.execute("""SELECT topic, avg_score_pct, cluster
                   FROM quiz_topic_tracking
                   WHERE total_sessions > 0
                   ORDER BY avg_score_pct ASC
                   LIMIT 3""").fetchall()
            total_quiz_sessions = conn.execute("""SELECT COUNT(*) as cnt FROM sessions
                   WHERE mode='quiz' AND status='completed'""").fetchone()

            data["quiz"] = {
                "total_sessions": total_quiz_sessions["cnt"] if total_quiz_sessions else 0,
                "strongest": [dict(r) for r in strongest],
                "weakest": [dict(r) for r in weakest],
            }

            # --- Speaking: rata-rata 3 sesi terakhir per sub-mode ---
            speaking_rows = conn.execute("""
                SELECT ss.sub_mode,
                       AVG(ss.grammar_score)   as avg_grammar,
                       AVG(ss.relevance_score) as avg_relevance,
                       AVG(ss.final_score)     as avg_final,
                       COUNT(*) as cnt
                FROM speaking_sessions ss
                JOIN sessions s ON ss.session_id = s.session_id
                WHERE s.status = 'completed' AND ss.is_graded = 1
                  AND ss.session_id IN (
                      SELECT session_id FROM sessions
                      WHERE mode = 'speaking' AND status = 'completed'
                      ORDER BY completed_at DESC
                      LIMIT 9
                  )
                GROUP BY ss.sub_mode
                """).fetchall()
            total_speaking = conn.execute("""SELECT COUNT(*) as cnt FROM sessions
                   WHERE mode='speaking' AND status='completed'""").fetchone()

            data["speaking"] = {
                "total_sessions": total_speaking["cnt"] if total_speaking else 0,
                "by_mode": [dict(r) for r in speaking_rows],
            }

            # --- TOEFL: 5 simulasi terakhir ---
            toefl_rows = conn.execute("""
                SELECT ts.mode, ts.estimated_score,
                       ts.listening_scaled, ts.structure_scaled, ts.reading_scaled,
                       s.completed_at
                FROM toefl_sessions ts
                JOIN sessions s ON ts.session_id = s.session_id
                WHERE s.status = 'completed' AND ts.score_status = 'completed'
                ORDER BY s.completed_at DESC
                LIMIT 5
                """).fetchall()
            total_toefl = conn.execute("""SELECT COUNT(*) as cnt FROM sessions
                   WHERE mode='toefl' AND status='completed'""").fetchone()

            data["toefl"] = {
                "total_sessions": total_toefl["cnt"] if total_toefl else 0,
                "recent": list(reversed([dict(r) for r in toefl_rows])),
            }

    except Exception as e:
        st.caption(f"⚠️ Gagal load Layer 2: {e}")

    return data


# ===================================================
# LAYER 1 — Render
# ===================================================
def _render_layer1(d: dict, target_toefl: Optional[int]):
    st.markdown("### 📊 Ringkasan Hari Ini")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        streak = d.get("streak", 0)
        st.metric(
            "🔥 Streak",
            f"{streak} hari",
            help="Hari berturut-turut kamu latihan",
        )

    with col2:
        latest = d.get("latest_toefl")
        if latest and target_toefl:
            gap = target_toefl - latest
            delta = f"-{gap} dari target" if gap > 0 else "✅ Target tercapai!"
            st.metric("📊 Estimasi TOEFL", str(latest), delta=delta, delta_color="inverse" if gap > 0 else "normal")
        elif latest:
            st.metric("📊 Estimasi TOEFL", str(latest))
        else:
            st.metric("📊 Estimasi TOEFL", "—", help="Belum ada simulasi TOEFL")

    with col3:
        coverage = d.get("grammar_coverage", 0.0)
        practiced = d.get("topics_practiced", 0)
        st.metric(
            "📚 Coverage Grammar",
            f"{coverage}%",
            help=f"{practiced}/{TOTAL_GRAMMAR_TOPICS} topik sudah dilatih",
        )

    with col4:
        last = d.get("last_session")
        if last:
            mode_icons = {"vocab": "📚", "quiz": "📝", "speaking": "🎤", "toefl": "📊"}
            icon = mode_icons.get(last["mode"], "📌")
            score_str = f" — {last['score']}" if last["score"] else ""
            st.metric(
                "⏱️ Sesi Terakhir",
                f"{icon} {last['mode'].capitalize()}",
                help=f"{last['completed_at']}{score_str}",
            )
        else:
            st.metric("⏱️ Sesi Terakhir", "—", help="Belum ada sesi")


# ===================================================
# LAYER 2 — Render
# ===================================================
def _render_layer2(d: dict):
    st.markdown("---")
    st.markdown("### 🗂️ Ringkasan Per Mode")

    tab_vocab, tab_quiz, tab_speaking, tab_toefl = st.tabs(["📚 Vocab", "📝 Quiz", "🎤 Speaking", "📊 TOEFL"])

    # ---- Vocab tab ----
    with tab_vocab:
        vocab = d.get("vocab", {}) or {}
        total = vocab.get("total_tracked", 0)
        if total == 0:
            st.info("Belum ada data Vocab.\n\n" "Setelah sesi pertama selesai, kamu akan melihat:\n" "- Jumlah kata yang sudah dikuasai (mastery ≥ 80%)\n" "- Daftar kata lemah yang perlu di-review")
        else:
            mastered = vocab.get("mastered", 0)
            col1, col2 = st.columns(2)
            col1.metric("Kata Terlacak", total)
            col2.metric("Dikuasai (≥80%)", mastered, help="Mastery score ≥ 80% dianggap dikuasai")

            weak = vocab.get("weak_words", [])
            if weak:
                st.markdown("**5 Kata Paling Lemah:**")
                for w in weak:
                    score = w.get("mastery_score", 0)
                    st.markdown(f"- `{w['word']}` — mastery **{score:.0f}%** " f"*(topik: {w.get('topic', '—')})*")
            else:
                st.success("Tidak ada kata dengan mastery di bawah 60%! 🎉")

    # ---- Quiz tab ----
    with tab_quiz:
        quiz = d.get("quiz", {}) or {}
        total = quiz.get("total_sessions", 0)
        if total == 0:
            st.info("Belum ada data Quiz.\n\n" "Setelah sesi pertama selesai, kamu akan melihat:\n" "- Topik grammar terkuat dan terlemah\n" "- Persentase penguasaan per topik")
        else:
            st.metric("Total Sesi Quiz", total)
            col1, col2 = st.columns(2)

            with col1:
                st.markdown("**🏆 Topik Terkuat:**")
                for t in quiz.get("strongest", []):
                    st.markdown(f"- {t['topic']} — **{t['avg_score_pct']:.0f}%**")

            with col2:
                st.markdown("**⚠️ Topik Terlemah:**")
                for t in quiz.get("weakest", []):
                    st.markdown(f"- {t['topic']} — **{t['avg_score_pct']:.0f}%**")

    # ---- Speaking tab ----
    with tab_speaking:
        speaking = d.get("speaking", {}) or {}
        total = speaking.get("total_sessions", 0)
        if total == 0:
            st.info("Belum ada data Speaking.\n\n" "Setelah sesi pertama selesai, kamu akan melihat:\n" "- Rata-rata skor Grammar dan Relevance\n" "- Perbandingan performa per sub-mode")
        else:
            st.metric("Total Sesi Speaking", total)
            by_mode = speaking.get("by_mode", [])
            if by_mode:
                sub_mode_labels = {
                    "prompted_response": "Prompted Response",
                    "conversation_practice": "Conversation Practice",
                    "oral_presentation": "Oral Presentation",
                }
                for row in by_mode:
                    sub = row.get("sub_mode", "")
                    label = sub_mode_labels.get(sub, sub)
                    with st.container(border=True):
                        st.markdown(f"**{label}** — {row.get('cnt', 0)} sesi")
                        c1, c2, c3 = st.columns(3)
                        g = row.get("avg_grammar")
                        r = row.get("avg_relevance")
                        f = row.get("avg_final")
                        c1.metric("Grammar", f"{g:.1f}" if g else "—")
                        c2.metric("Relevance", f"{r:.1f}" if r else "—")
                        c3.metric("Final", f"{f:.1f}" if f else "—")

    # ---- TOEFL tab ----
    with tab_toefl:
        toefl = d.get("toefl", {}) or {}
        total = toefl.get("total_sessions", 0)
        if total == 0:
            st.info(
                "Belum ada data TOEFL Simulator.\n\n"
                "Setelah simulasi pertama selesai, kamu akan melihat:\n"
                "- Trend estimasi skor per simulasi\n"
                "- Breakdown skor per section (Listening, Structure, Reading)"
            )
        else:
            st.metric("Total Simulasi", total)
            recent = toefl.get("recent", [])
            if recent:
                st.markdown("**Trend 5 Simulasi Terakhir:**")
                for i, sim in enumerate(recent, 1):
                    est = sim.get("estimated_score", "—")
                    mode = sim.get("mode", "—")
                    l_sc = sim.get("listening_scaled", "—")
                    s_sc = sim.get("structure_scaled", "—")
                    r_sc = sim.get("reading_scaled", "—")
                    date = (sim.get("completed_at") or "")[:10]
                    st.markdown(f"**Sim {i}** ({date}, mode {mode}) — " f"Estimasi: **{est}** | " f"L: {l_sc} · S: {s_sc} · R: {r_sc}")


# ===================================================
# LAYER 3 — Deep Analysis (on-demand)
# ===================================================
def _render_layer3(target_toefl: int):
    st.markdown("---")
    st.markdown("### 🤖 Analisis Mendalam — Tutor AI")
    st.caption("Layer ini memanggil AI untuk analisis lintas semua mode latihan. " "Membutuhkan beberapa detik.")

    # Tampilkan hasil analisis sebelumnya jika ada di session state
    cached = _get("master_analytics_result")
    if cached:
        _render_master_analytics_result(cached)
        if st.button("🔄 Perbarui Analisis", use_container_width=True):
            _set("master_analytics_result", None)
            st.rerun()
        return

    if st.button(
        "🧠 Minta Analisis Tutor AI",
        type="primary",
        use_container_width=True,
    ):
        with st.spinner("Menganalisis semua data latihan kamu... (10–20 detik)"):
            try:
                from agents.orchestrator.master_analytics import run_master_analytics

                result = run_master_analytics(target_toefl=target_toefl)
                _set("master_analytics_result", result)
                st.rerun()
            except Exception as e:
                st.error(f"Gagal menjalankan analisis: {e}")


def _render_master_analytics_result(result: dict):
    """Render output Master Analytics secara terstruktur."""

    # Overall trend badge
    trend = result.get("overall_trend", "insufficient_data")
    trend_display = {
        "improving": ("📈", "Meningkat", "success"),
        "stable": ("➡️", "Stabil", "info"),
        "declining": ("📉", "Menurun", "warning"),
        "mixed": ("🔀", "Beragam", "info"),
        "insufficient_data": ("📊", "Data Kurang", "info"),
    }
    icon, label, msg_type = trend_display.get(trend, ("📊", trend, "info"))

    getattr(st, msg_type)(f"{icon} Tren Keseluruhan: **{label}**")

    # Insight utama
    insight = result.get("insight")
    if insight:
        st.markdown("#### 💡 Insight Utama")
        st.markdown(insight)

    # Top priority
    priority = result.get("top_priority")
    if priority:
        st.warning(f"🎯 **Prioritas Sekarang:** {priority}")

    # Cross-mode correlations
    correlations = result.get("cross_mode_correlations", [])
    if correlations:
        st.markdown("#### 🔗 Korelasi Lintas Mode")
        for corr in correlations:
            modes = " + ".join(corr.get("modes", []))
            finding = corr.get("finding", "")
            action = corr.get("action", "")
            with st.container(border=True):
                st.markdown(f"**{modes.upper()}**")
                st.markdown(finding)
                if action:
                    st.caption(f"➡️ {action}")
    else:
        st.info("Belum cukup data lintas mode untuk mendeteksi korelasi.")

    # TOEFL readiness
    readiness = result.get("toefl_readiness", {})
    if readiness:
        st.markdown("#### 🎯 TOEFL Readiness")
        target = readiness.get("target_score")
        best = readiness.get("best_estimated_score")
        gap = readiness.get("gap")
        est_weeks = readiness.get("estimated_weeks")
        level = readiness.get("readiness_level", "no_data")
        rec = readiness.get("recommendation", "")

        level_display = {
            "on_track": ("🟢", "On Track"),
            "approaching": ("🟡", "Mendekati Target"),
            "needs_work": ("🔴", "Perlu Kerja Keras"),
            "no_data": ("⚪", "Belum Ada Data"),
        }
        r_icon, r_label = level_display.get(level, ("⚪", level))

        col1, col2, col3 = st.columns(3)
        col1.metric("Target", str(target) if target else "—")
        col2.metric("Estimasi Terbaik", str(best) if best else "—")
        col3.metric("Gap", str(gap) if gap is not None else "—", delta_color="inverse")

        st.markdown(f"{r_icon} Status: **{r_label}**")
        if est_weeks:
            st.caption(f"⏳ Estimasi waktu: {est_weeks}")
        if rec:
            st.info(rec)

    # Modes summary
    modes_with = result.get("modes_with_data", [])
    modes_without = result.get("modes_without_data", [])
    if modes_without:
        st.caption(f"⚠️ Analisis berdasarkan data dari: **{', '.join(modes_with)}**. " f"Mode belum ada data: {', '.join(modes_without)}.")

    st.caption("ℹ️ Analisis ini berdasarkan data latihan yang tersedia dan " "dapat berubah seiring bertambahnya sesi.")


# ===================================================
# Onboarding steps (dari Step 6.1, tidak berubah)
# ===================================================
def _render_onboarding_step1():
    st.title("👋 Selamat Datang!")
    st.markdown("Sebelum mulai, kami perlu tahu sedikit tentang kamu " "agar latihan bisa dipersonalisasi.")
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


def _render_onboarding_step2():
    st.title("👋 Selamat Datang!")
    st.markdown("---")
    st.markdown("### Step 2 dari 3 — Level Grammar Sekarang")
    st.caption("Pilih level yang paling menggambarkan kemampuan grammar kamu saat ini.")

    selected_level = _get("ob_level", GRAMMAR_LEVELS[0])
    for level in GRAMMAR_LEVELS:
        hint = GRAMMAR_LEVEL_HINTS[level]
        is_sel = selected_level == level
        border = "2px solid #1f77b4" if is_sel else "1px solid #ddd"
        st.markdown(
            f"<div style='padding:12px; border-radius:8px; border:{border}; " f"margin-bottom:8px;'><strong>{level}</strong><br/>" f"<small style='color:gray;'>{hint}</small></div>",
            unsafe_allow_html=True,
        )
        if st.button(f"{'✅ ' if is_sel else ''}Pilih {level}", key=f"ob_level_{level}", use_container_width=True):
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


def _render_onboarding_step3():
    st.title("👋 Selamat Datang!")
    st.markdown("---")
    st.markdown("### Step 3 dari 3 — Topik Vocab Pertama")
    st.caption("Pilih topik yang ingin kamu pelajari di sesi Vocab pertama.")

    topic_labels = [VOCAB_TOPIC_LABELS.get(t, t) for t in VOCAB_TOPICS]
    default_idx = VOCAB_TOPICS.index(_get("ob_topic", "sehari_hari"))
    selected_label = st.selectbox(
        label="Pilih Topik:",
        options=topic_labels,
        index=default_idx,
        key="ob_topic_select",
        label_visibility="collapsed",
    )
    selected_topic = VOCAB_TOPICS[topic_labels.index(selected_label)]
    _set("ob_topic", selected_topic)
    st.markdown("")

    target = _get("ob_target", 500)
    level = _get("ob_level", "Pemula")
    st.success(f"**Ringkasan:**\n\n" f"🎯 Target skor: **{target}**\n\n" f"📚 Grammar level: **{level}**\n\n" f"🔤 Topik vocab pertama: **{selected_label}**")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("← Kembali", use_container_width=True):
            _set("ob_step", 2)
            st.rerun()
    with col2:
        if st.button("✅ Mulai Belajar!", type="primary", use_container_width=True):
            success = save_onboarding_data(
                target_toefl=target,
                grammar_level=level,
                first_vocab_topic=selected_topic,
            )
            if success:
                _reset_onboarding()
                _set("state", "home")
                st.balloons()
                st.rerun()
            else:
                st.error("Gagal menyimpan data. Coba lagi atau restart aplikasi.")


# ===================================================
# Dashboard Home — 3 layer
# ===================================================
def _render_home(ctx: RoutingContext):
    st.title("🎓 English Learning AI Agent")

    # Edit profil (collapsible, di paling atas agar mudah diakses)
    with st.expander("⚙️ Edit Profil & Target"):
        _render_profile_editor(ctx)

    st.markdown("---")

    # Load data Layer 1 & 2 sekali, simpan ke session state
    # agar tidak re-query setiap st.rerun() kecil
    l1_data = st.session_state.get("db_l1")
    l2_data = st.session_state.get("db_l2")

    if l1_data is None:
        l1_data = _query_layer1()
        st.session_state["db_l1"] = l1_data
    if l2_data is None:
        l2_data = _query_layer2()
        st.session_state["db_l2"] = l2_data

    # Tombol refresh data
    if st.button("🔄 Refresh Data", help="Muat ulang data dari database"):
        for k in ["db_l1", "db_l2", "db_master_analytics_result"]:
            if k in st.session_state:
                del st.session_state[k]
        st.rerun()

    # --- Layer 1 ---
    _render_layer1(l1_data, ctx.target_toefl)

    # --- Layer 2 ---
    _render_layer2(l2_data)

    # --- Layer 3 ---
    _render_layer3(ctx.target_toefl or 500)

    # Navigasi mode
    st.markdown("---")
    st.markdown("### 🚀 Mulai Latihan")
    col1, col2, col3, col4 = st.columns(4)
    nav_map = {
        "📚 Vocab Agent": col1,
        "📝 Quiz Agent": col2,
        "🎤 Speaking Agent": col3,
        "📊 TOEFL Simulator": col4,
    }
    for mode_key, col in nav_map.items():
        with col:
            if st.button(mode_key, use_container_width=True):
                st.session_state["_nav_request"] = mode_key
                st.rerun()


def _render_profile_editor(ctx: RoutingContext):
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
    topic_labels = [VOCAB_TOPIC_LABELS.get(t, t) for t in VOCAB_TOPICS]
    current_topic = ctx.first_vocab_topic or "sehari_hari"
    default_idx = VOCAB_TOPICS.index(current_topic) if current_topic in VOCAB_TOPICS else 0
    new_topic_label = st.selectbox(
        "Topik Vocab Default",
        options=topic_labels,
        index=default_idx,
        key="profile_topic",
    )
    new_topic = VOCAB_TOPICS[topic_labels.index(new_topic_label)]

    if st.button("💾 Simpan Perubahan", type="primary"):

        success = update_user_profile(
            target_toefl=new_target,
            grammar_level=new_level,
            first_vocab_topic=new_topic,
        )
        if success:
            # Invalidate semua cache
            for k in ["db_ctx", "db_l1", "db_l2"]:
                if k in st.session_state:
                    del st.session_state[k]
            st.success("✅ Profil diperbarui.")
            st.rerun()
        else:
            st.error("Gagal menyimpan. Coba lagi.")


# ===================================================
# Entry point
# ===================================================
def main():
    ctx = st.session_state.get("db_ctx")
    if ctx is None:
        ctx = get_routing_context()
        st.session_state["db_ctx"] = ctx

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
