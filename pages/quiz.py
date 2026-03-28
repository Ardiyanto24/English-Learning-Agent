"""
pages/quiz.py
--------------
Halaman UI Quiz Agent.

State machine (satu state lebih banyak dari Vocab karena Human in the Loop):
  "init"         → Tampilkan rekomendasi topik dari Planner
  "recommending" → User review & konfirmasi/ganti topik
  "loading"      → Generate soal (Generator → Validator)
  "answering"    → User jawab soal satu per satu
  "completed"    → Summary skor + analytics insight
"""

import streamlit as st

from agents.quiz.planner import run_planner
from agents.quiz.generator import run_generator
from agents.quiz.validator import run_validator
from agents.quiz.corrector import run_corrector
from agents.quiz.analytics import run_analytics
from database.repositories.session_repository import (
    create_session,
    update_session_status,
)
from database.repositories.quiz_repository import (
    save_quiz_session,
    update_quiz_session_scores,
    save_quiz_question,
    update_quiz_answer,
    update_topic_tracking,
)
from utils.helpers import generate_session_id, calculate_score_pct
from utils.logger import logger


# ===================================================
# State helpers
# ===================================================
def _get(key, default=None):
    return st.session_state.get(f"quiz_{key}", default)


def _set(key, value):
    st.session_state[f"quiz_{key}"] = value


def _reset():
    keys = [k for k in st.session_state if k.startswith("quiz_")]
    for k in keys:
        del st.session_state[k]


# ===================================================
# Render: soal sesuai format
# ===================================================
def _render_question(q: dict, index: int, total: int) -> str:
    """
    Tampilkan soal sesuai format dan return jawaban user.
    Setiap format punya widget berbeda.
    """
    fmt = q.get("format", "multiple_choice")
    question_text = q.get("question_text", "")
    options = q.get("options", [])

    st.markdown(f"**Soal {index + 1} dari {total}**")
    st.caption(f"Topik: **{q.get('topic')}** | " f"Format: *{fmt.replace('_', ' ').title()}* | " f"Level: *{q.get('difficulty', '').title()}*")
    st.markdown(f"### {question_text}")

    # ── multiple_choice & fill_blank → Radio button ──
    if fmt in ("multiple_choice", "fill_blank"):
        if not options:
            return st.text_input("Jawaban:", key=f"quiz_ans_{index}")

        choice = st.radio(
            "Pilih jawaban:",
            options=options,
            key=f"quiz_radio_{index}",
            index=None,
        )
        # Ekstrak huruf pilihan (A/B/C/D) dari "A. ..."
        if choice:
            return choice.split(".")[0].strip()
        return ""

    # ── error_id → Pilih bagian yang salah ──
    elif fmt == "error_id":
        st.markdown("**Pilih bagian kalimat yang mengandung error:**")
        choice = st.radio("Bagian yang salah:", options=options, key=f"quiz_error_{index}", index=None, help="Pilih salah satu dari (A), (B), (C), (D)")
        if choice:
            return choice.split(".")[0].strip()
        return ""

    # Fallback
    return st.text_input("Jawaban:", key=f"quiz_fallback_{index}")


# ===================================================
# Render: 4 lapisan feedback
# ===================================================
def _render_feedback(correction: dict, question: dict):
    """Tampilkan 4 lapisan feedback dalam expander yang tersusun."""
    is_correct = correction.get("is_correct", False)
    is_graded = correction.get("is_graded", True)
    feedback = correction.get("feedback", {})

    if not is_graded:
        st.warning(f"⚠️ {feedback.get('verdict', 'Soal belum dinilai.')}")
        return

    # Header verdict
    if is_correct:
        st.success(f"✅ **{feedback.get('verdict', 'Benar!')}**")
    else:
        st.error(f"❌ **{feedback.get('verdict', 'Kurang tepat.')}**")
        st.caption(f"Jawaban benar: **{question.get('correct_answer')}**")

    # 3 lapisan dalam expander agar tidak memenuhi layar
    with st.expander("📖 Lihat penjelasan lengkap", expanded=not is_correct):
        st.markdown("**Penjelasan:**")
        st.write(feedback.get("explanation", "-"))

        st.markdown("**Konsep Grammar:**")
        st.info(feedback.get("concept", "-"))

        st.markdown("**Contoh:**")
        examples = feedback.get("example", [])
        for ex in examples:
            if ex.startswith("✓"):
                st.success(ex)
            elif ex.startswith("✗"):
                st.error(ex)
            else:
                st.write(ex)


# ===================================================
# Render: summary akhir sesi
# ===================================================
def _render_summary():
    questions = _get("questions", [])
    results = _get("results", [])
    planner = _get("planner_output", {})
    analytics = _get("analytics")

    correct_count = sum(1 for r in results if r.get("is_correct"))
    total = len(questions)
    score_pct = calculate_score_pct(correct_count, total)

    st.markdown("## 🎉 Sesi Selesai!")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Skor", f"{score_pct:.0f}%")
    with col2:
        st.metric("Benar", f"{correct_count}/{total}")
    with col3:
        st.metric("Topik", ", ".join(planner.get("topics", [])))

    # Detail per soal
    st.markdown("### Detail Jawaban")
    for i, (q, r) in enumerate(zip(questions, results)):
        icon = "✅" if r.get("is_correct") else "❌"
        with st.expander(f"{icon} Soal {i+1}: {q.get('topic')} — {q.get('format')}"):
            st.write(f"**Soal:** {q.get('question_text')}")
            st.write(f"**Jawaban kamu:** {r.get('user_answer', '-')}")
            st.write(f"**Jawaban benar:** {q.get('correct_answer')}")
            fb = r.get("feedback", {})
            if fb.get("explanation"):
                st.caption(fb["explanation"])

    # Analytics insight
    if analytics and analytics.get("insight"):
        st.markdown("---")
        st.markdown("### 💡 Insight dari AI")
        st.info(analytics["insight"])
        if analytics.get("prerequisite_bottleneck"):
            st.warning(f"🔗 **Bottleneck:** {analytics['prerequisite_bottleneck']}")


# ===================================================
# Flow: mulai sesi setelah konfirmasi
# ===================================================
def _start_session(confirmed_topics: list[str], planner_output: dict):
    """Generate soal untuk topik yang sudah dikonfirmasi user."""
    _set("page_state", "loading")

    # Update planner output dengan topik yang dikonfirmasi user
    planner_output["topics"] = confirmed_topics

    try:
        with st.spinner("✍️ Membuat soal grammar..."):
            generator_output = run_generator(planner_output)

        with st.spinner("🔍 Memvalidasi soal..."):
            validator_result = run_validator(planner_output, generator_output)
            final_questions = validator_result.get("final_questions", [])
            is_adjusted = validator_result.get("is_adjusted", False)

        if not final_questions:
            st.error("Gagal membuat soal. Silakan coba lagi.")
            _set("page_state", "init")
            return

        # Buat session di DB
        session_id = generate_session_id()
        create_session(session_id, mode="quiz")
        save_quiz_session(
            session_id=session_id,
            topics=confirmed_topics,
            total_questions=len(final_questions),
        )

        # Simpan soal ke DB (incremental)
        question_ids = []
        for q in final_questions:
            import json

            q_id = save_quiz_question(
                session_id=session_id,
                topic=q["topic"],
                cluster=planner_output.get("cluster", ""),
                format=q["format"],
                difficulty=q["difficulty"],
                question_text=q["question_text"],
                options=json.dumps(q.get("options", [])),
                correct_answer=q["correct_answer"],
            )
            question_ids.append(q_id)

        _set("session_id", session_id)
        _set("planner_output", planner_output)
        _set("questions", final_questions)
        _set("question_ids", question_ids)
        _set("is_adjusted", is_adjusted)
        _set("current_index", 0)
        _set("results", [])
        _set("page_state", "answering")

        if is_adjusted:
            st.warning("⚠️ Soal disesuaikan otomatis karena validasi tidak sempurna.")

        st.rerun()

    except RuntimeError as e:
        st.error("😔 Gagal membuat soal. Silakan coba lagi.")
        logger.error(f"[quiz_page] Session creation failed: {e}")
        _set("page_state", "init")
    except Exception as e:
        st.error(f"Terjadi kesalahan: {e}")
        _set("page_state", "init")


# ===================================================
# Flow: handle jawaban
# ===================================================
def _handle_answer(user_answer: str):
    """Koreksi jawaban, simpan ke DB."""
    index = _get("current_index", 0)
    questions = _get("questions", [])
    question_ids = _get("question_ids", [])
    session_id = _get("session_id")
    results = _get("results", [])

    q = questions[index]
    q_id = question_ids[index]

    with st.spinner("Menilai jawaban..."):
        correction = run_corrector(
            topic=q["topic"],
            format=q["format"],
            question_text=q["question_text"],
            options=q.get("options", []),
            correct_answer=q["correct_answer"],
            user_answer=user_answer,
            session_id=session_id,
        )

    # Simpan ke DB
    update_quiz_answer(
        question_id=q_id,
        user_answer=user_answer,
        is_correct=correction.get("is_correct", False),
        is_graded=correction.get("is_graded", True),
        feedback=correction.get("feedback", {}),
    )

    results.append({**correction, "user_answer": user_answer})
    _set("results", results)
    _set("last_correction", correction)
    _set("last_question", q)


# ===================================================
# Flow: selesaikan sesi
# ===================================================
def _complete_session():
    session_id = _get("session_id")
    questions = _get("questions", [])
    results = _get("results", [])
    planner = _get("planner_output", {})
    is_adjusted = _get("is_adjusted", False)

    correct_count = sum(1 for r in results if r.get("is_correct"))
    score_pct = calculate_score_pct(correct_count, len(questions))

    update_quiz_session_scores(
        session_id=session_id,
        correct_count=correct_count,
        wrong_count=len(questions) - correct_count,
        score_pct=score_pct,
    )

    # Update topic tracking per topik — hitung skor per topik dulu
    topic_stats: dict = {}
    for q, r in zip(questions, results):
        if not r.get("is_graded"):
            continue
        t = q["topic"]
        if t not in topic_stats:
            topic_stats[t] = {
                "cluster": q.get("cluster", planner.get("cluster", "")),
                "total": 0,
                "correct": 0,
            }
        topic_stats[t]["total"] += 1
        topic_stats[t]["correct"] += 1 if r.get("is_correct") else 0

    for topic, stats in topic_stats.items():
        score = calculate_score_pct(stats["correct"], stats["total"])
        update_topic_tracking(
            topic=topic,
            cluster=stats["cluster"],
            score_pct=score,
            total_questions=stats["total"],
            correct_count=stats["correct"],
        )

    update_session_status(
        session_id=session_id,
        status="completed",
        is_adjusted=is_adjusted,
    )

    analytics = run_analytics()
    _set("analytics", analytics)
    _set("page_state", "completed")


# ===================================================
# Main UI
# ===================================================
def main():
    st.title("📝 Quiz Agent")
    st.caption("Latihan grammar bahasa Inggris dengan feedback 4 lapisan")

    page_state = _get("page_state", "init")

    # ── STATE: init — Jalankan Planner, tampilkan rekomendasi ──
    if page_state == "init":
        st.markdown("### Memuat Rekomendasi Topik...")

        with st.spinner("🧠 Menganalisis progress kamu..."):
            planner_output = run_planner()

        _set("planner_output", planner_output)
        _set("page_state", "recommending")
        st.rerun()

    # ── STATE: recommending — Human in the Loop ──
    elif page_state == "recommending":
        planner_output = _get("planner_output", {})
        recommended = planner_output.get("topics", [])
        accessible = planner_output.get("accessible_topics", [])
        is_cold_start = planner_output.get("is_cold_start", False)
        difficulty = planner_output.get("difficulty_target", "medium")

        st.markdown("### 🎯 Rekomendasi Topik")

        if is_cold_start:
            st.info("Selamat datang! Ini sesi pertamamu. Pilih topik untuk memulai.")
        else:
            st.markdown(f"Berdasarkan progress kamu, sistem merekomendasikan topik berikut " f"dengan level **{difficulty}**:")
            for t in recommended:
                new_tag = " 🆕" if t in planner_output.get("new_topics", []) else ""
                st.markdown(f"- **{t}**{new_tag}")

        # User bisa terima rekomendasi atau pilih sendiri
        st.markdown("---")
        use_recommendation = st.radio(
            "Pilihan topik:",
            options=["✅ Gunakan rekomendasi sistem", "🔧 Pilih topik sendiri"],
            key="quiz_topic_choice",
        )

        confirmed_topics = recommended

        if use_recommendation == "🔧 Pilih topik sendiri":
            st.caption("Hanya topik yang prerequisite-nya sudah terpenuhi yang bisa dipilih.")
            selected = st.multiselect(
                "Pilih 1–2 topik:",
                options=accessible,
                default=recommended,
                max_selections=2,
                key="quiz_manual_topics",
            )
            confirmed_topics = selected if selected else recommended

        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button(
                "🚀 Mulai Sesi",
                type="primary",
                use_container_width=True,
                key="quiz_confirm_btn",
                disabled=not confirmed_topics,
            ):
                _start_session(confirmed_topics, planner_output)

    # ── STATE: loading ──
    elif page_state == "loading":
        st.info("Sedang menyiapkan soal...")

    # ── STATE: answering ──
    elif page_state == "answering":
        questions = _get("questions", [])
        current_index = _get("current_index", 0)
        results = _get("results", [])
        total = len(questions)

        # Progress bar
        st.progress(current_index / total, text=f"Soal {current_index + 1} dari {total}")
        st.markdown("---")

        # Feedback soal sebelumnya
        last_correction = _get("last_correction")
        last_question = _get("last_question")
        if last_correction and last_question and current_index > 0:
            _render_feedback(last_correction, last_question)
            st.markdown("---")

        # Soal saat ini
        if current_index < total:
            q = questions[current_index]
            user_answer = _render_question(q, current_index, total)

            col1, col2 = st.columns([1, 4])
            with col1:
                submit = st.button(
                    "Submit ✓",
                    type="primary",
                    use_container_width=True,
                    key=f"quiz_submit_{current_index}",
                    disabled=not user_answer,
                )

            if submit and user_answer:
                _handle_answer(user_answer)
                _set("current_index", current_index + 1)

                if current_index + 1 >= total:
                    # Tampilkan feedback soal terakhir sebelum complete
                    _complete_session()
                else:
                    st.rerun()

        st.markdown("---")
        if st.button("❌ Keluar", type="secondary", key="quiz_exit_btn"):
            session_id = _get("session_id")
            if session_id:
                update_session_status(session_id, status="abandoned")
            _reset()
            st.rerun()

    # ── STATE: completed ──
    elif page_state == "completed":
        _render_summary()
        st.markdown("---")
        if st.button("🔄 Sesi Baru", type="primary", key="quiz_new_btn"):
            _reset()
            st.rerun()


if __name__ == "__main__":
    main()
