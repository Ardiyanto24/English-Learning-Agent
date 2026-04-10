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

import json

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

# ── Tutor imports ─────────────────────────────────────────────────────────
from agents.quiz_tutor.planner import run_planner as run_tutor_planner
from agents.quiz_tutor.generator import run_generator as run_tutor_generator
from agents.quiz_tutor.validator import run_validator as run_tutor_validator
from agents.quiz_tutor.corrector import run_corrector as run_tutor_corrector
from agents.quiz_tutor.analytics import run_analytics as run_tutor_analytics
from database.repositories.tutor_repository import (
    save_tutor_session,
    update_tutor_session_scores,
    save_tutor_question,
    update_tutor_question_answer,
    upsert_tutor_topic_tracking,
)


# ===================================================
# State helpers — TOEFL Quiz (prefix: quiz_)
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
# State helpers — Grammar Tutor (prefix: tutor_)
# ===================================================
def _tget(key, default=None):
    return st.session_state.get(f"tutor_{key}", default)


def _tset(key, value):
    st.session_state[f"tutor_{key}"] = value


def _treset():
    keys = [k for k in st.session_state if k.startswith("tutor_")]
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
    st.caption(
        f"Topik: **{q.get('topic')}** | "
        f"Format: *{fmt.replace('_', ' ').title()}* | "
        f"Level: *{q.get('difficulty', '').title()}*"
    )
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
        choice = st.radio(
            "Bagian yang salah:",
            options=options,
            key=f"quiz_error_{index}",
            index=None,
            help="Pilih salah satu dari (A), (B), (C), (D)",
        )
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
        with st.expander(f"{icon} Soal {i + 1}: {q.get('topic')} — {q.get('format')}"):
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
            topics=json.dumps(confirmed_topics),
            total_questions=len(final_questions),
        )

        # Simpan soal ke DB (incremental)
        question_ids = []
        for q in final_questions:

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
    feedback = correction.get("feedback", {})

    update_quiz_answer(
        question_id=q_id,
        user_answer=user_answer,
        is_correct=correction.get("is_correct", False),
        is_graded=correction.get("is_graded", True),
        feedback_example=str(feedback.get("example", [])),
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
# Grammar Tutor — Render: konfigurasi sesi
# ===================================================
def _render_tutor_config():
    """
    Tampilkan UI konfigurasi sesi Grammar Tutor.

    State yang diset saat konfirmasi:
      tutor_selected_topics : list[str] topik yang dipilih user
      tutor_total_questions : int jumlah soal yang dipilih
      tutor_page_state      : "loading"
    """
    from agents.quiz_tutor.planner import PREREQUISITE_RULES

    st.markdown("### 🎓 Grammar Tutor — Konfigurasi Sesi")

    # ── Daftar topik ──────────────────────────────────────────────
    all_topics = list(PREREQUISITE_RULES.keys()) if PREREQUISITE_RULES else []

    st.caption(
        "Pilih 1–3 topik grammar yang ingin dilatih. "
        "Topik yang prerequisite-nya belum terpenuhi akan diblok otomatis oleh sistem."
    )
    selected_topics = st.multiselect(
        "Topik Grammar:",
        options=all_topics,
        max_selections=3,
        key="tutor_topic_multiselect",
    )

    # ── Jumlah soal ───────────────────────────────────────────────
    total_questions = st.radio(
        "Jumlah soal:",
        options=[5, 10, 15, 20],
        index=1,
        horizontal=True,
        key="tutor_question_count_radio",
    )

    st.markdown("")

    # ── Tombol mulai ──────────────────────────────────────────────
    if st.button(
        "🚀 Mulai Sesi",
        type="primary",
        disabled=not selected_topics,
        key="tutor_start_btn",
    ):
        _tset("selected_topics", selected_topics)
        _tset("total_questions", total_questions)
        _tset("page_state", "loading")
        st.rerun()


# ===================================================
# Grammar Tutor — Render: prerequisite block
# ===================================================
def _render_prerequisite_block(blocked_topics: list):
    """
    Tampilkan pesan hard block ketika ada topik yang prerequisite-nya
    belum terpenuhi. User tidak bisa melanjutkan sesi sampai prerequisite
    diselesaikan di TOEFL Quiz atau Grammar Tutor dengan skor >= 60%.

    Args:
        blocked_topics: list dict, setiap dict berisi:
            - topic              : str nama topik yang diblok
            - missing_prerequisites: list[str] topik prereq yang belum dikuasai
    """
    st.error(
        "🚫 Sesi tidak bisa dimulai karena ada topik yang prerequisite-nya "
        "belum dikuasai. Selesaikan topik prerequisite terlebih dahulu di "
        "TOEFL Quiz atau Grammar Tutor dengan skor minimal **60%**."
    )

    st.markdown("#### Topik yang Diblok:")
    for item in blocked_topics:
        topic = item.get("topic", "")
        missing = item.get("missing_prerequisites", [])
        with st.container(border=True):
            st.markdown(f"**{topic}**")
            st.markdown("Prerequisite yang harus diselesaikan dulu:")
            for prereq in missing:
                st.markdown(f"- {prereq}")

    st.markdown("")
    if st.button("← Kembali", key="tutor_prereq_back_btn"):
        _tset("page_state", "config")
        st.rerun()


# ===================================================
# Grammar Tutor — Loading: Planner → Generator → Validator
# ===================================================
def _run_tutor_loading():
    """
    Jalankan tiga agent secara berurutan untuk menyiapkan sesi:
      1. Planner  — cek prerequisite + susun distribusi soal
      2. Generator — generate soal berdasarkan planner output
      3. Validator — validasi dan adjust soal jika perlu

    Jika Planner memblok → routing ke state "blocked".
    Jika Generator/Validator gagal → kembali ke state "config".
    Jika semua berhasil → simpan ke DB, routing ke state "answering".
    """
    selected_topics = _tget("selected_topics", [])
    total_questions = _tget("total_questions", 10)

    # ── Langkah 1: Planner ────────────────────────────────────────
    with st.spinner("🧠 Menganalisis topik dan menyusun rencana soal..."):
        planner_output = run_tutor_planner(
            selected_topics=selected_topics,
            total_questions=total_questions,
        )

    if planner_output.get("status") == "blocked":
        _tset("blocked_topics", planner_output.get("blocked_topics", []))
        _tset("page_state", "blocked")
        st.rerun()
        return

    # ── Langkah 2: Generator ──────────────────────────────────────
    try:
        with st.spinner("✍️ Membuat soal grammar..."):
            generator_output = run_tutor_generator(planner_output)
    except RuntimeError as e:
        st.error(f"😔 Gagal membuat soal. Silakan coba lagi. ({e})")
        logger.error(f"[tutor_loading] Generator failed: {e}")
        _tset("page_state", "config")
        st.rerun()
        return

    # ── Langkah 3: Validator ──────────────────────────────────────
    with st.spinner("🔍 Memvalidasi soal..."):
        validator_result = run_tutor_validator(planner_output, generator_output)

    final_questions = validator_result.get("final_questions", [])
    if not final_questions:
        st.error("😔 Gagal menyiapkan soal yang valid. Silakan coba lagi.")
        logger.error("[tutor_loading] Validator returned empty final_questions")
        _tset("page_state", "config")
        st.rerun()
        return

    is_adjusted = validator_result.get("is_adjusted", False)

    # ── Simpan ke DB ──────────────────────────────────────────────
    session_id = generate_session_id()
    create_session(session_id, mode="tutor")
    save_tutor_session(
        session_id=session_id,
        topics=json.dumps(selected_topics),
        total_questions=len(final_questions),
    )

    question_ids = []
    for q in final_questions:
        q_id = save_tutor_question(
            session_id=session_id,
            topic=q["topic"],
            question_type=q["question_type"],
            question_text=q["question_text"],
            reference_answer=q["reference_answer"],
        )
        question_ids.append(q_id)

    # ── Simpan ke tutor state ─────────────────────────────────────
    _tset("session_id", session_id)
    _tset("questions", final_questions)
    _tset("question_ids", question_ids)
    _tset("planner_output", planner_output)
    _tset("is_adjusted", is_adjusted)
    _tset("current_index", 0)
    _tset("page_state", "answering")

    if is_adjusted:
        st.warning("⚠️ Soal disesuaikan otomatis karena validasi tidak sempurna.")

    st.rerun()


# ===================================================
# Grammar Tutor — Render: satu soal tutor
# ===================================================
def _render_tutor_question(q: dict, index: int) -> str:
    """
    Tampilkan satu soal Grammar Tutor dan return jawaban user.

    Semua soal bertipe isian (open-ended) — tidak ada pilihan ganda.
    Widget input dipilih berdasarkan field `input_type` di soal:
      - text_input : st.text_input  (Tipe 1, 2, 3, 5 — jawaban pendek)
      - text_area  : st.text_area   (Tipe 4, 6 — jawaban panjang/transformasi)

    Key widget tutor_ans_{index} memastikan jawaban tetap tersimpan
    di session state saat user navigasi Previous/Next.

    Args:
        q    : dict soal dari Generator (topic, question_type,
               question_text, reference_answer, input_type)
        index: posisi soal dalam list (0-based)

    Returns:
        String jawaban user saat ini, bisa kosong jika belum diisi.
    """
    question_type = q.get("question_type", "")
    input_type = q.get("input_type", "text_input")

    st.caption(
        f"Soal {index + 1} | "
        f"Topik: **{q.get('topic', '-')}** | "
        f"Tipe: *{question_type.replace('_', ' ').title()}*"
    )
    st.markdown(f"### {q.get('question_text', '')}")

    if input_type == "text_area":
        answer = st.text_area(
            "Jawaban kamu:",
            key=f"tutor_ans_{index}",
            height=120,
            placeholder="Tulis jawaban lengkap kamu di sini...",
        )
    else:
        answer = st.text_input(
            "Jawaban kamu:",
            key=f"tutor_ans_{index}",
            placeholder="Ketik jawaban kamu di sini...",
        )

    return answer or ""


# ===================================================
# Grammar Tutor — Answering: navigasi + Submit All
# ===================================================
def _run_tutor_answering():
    """
    State menjawab soal Grammar Tutor.

    User bisa bebas navigasi Previous/Next antar soal.
    Jawaban tersimpan otomatis di session state via widget key tutor_ans_{i}.
    Submit All hanya aktif ketika semua soal sudah memiliki jawaban non-kosong.
    Corrector dipanggil batch di _run_tutor_grading() setelah Submit All.
    """
    questions = _tget("questions", [])
    current_index = _tget("current_index", 0)
    total = len(questions)

    # ── Progress bar ──────────────────────────────────────────────
    st.progress(
        (current_index + 1) / total,
        text=f"Soal {current_index + 1} dari {total}",
    )
    st.markdown("---")

    # ── Soal saat ini ─────────────────────────────────────────────
    q = questions[current_index]
    _render_tutor_question(q, current_index)

    st.markdown("")

    # ── Cek semua jawaban sudah terisi ────────────────────────────
    all_answered = all(st.session_state.get(f"tutor_ans_{i}", "").strip() for i in range(total))

    # ── Tiga tombol navigasi dalam satu baris ─────────────────────
    col_prev, col_next, col_submit = st.columns([1, 1, 2])

    with col_prev:
        if st.button(
            "← Sebelumnya",
            disabled=(current_index == 0),
            use_container_width=True,
            key="tutor_prev_btn",
        ):
            _tset("current_index", current_index - 1)
            st.rerun()

    with col_next:
        if st.button(
            "Berikutnya →",
            disabled=(current_index == total - 1),
            use_container_width=True,
            key="tutor_next_btn",
        ):
            _tset("current_index", current_index + 1)
            st.rerun()

    with col_submit:
        if st.button(
            "✅ Submit All",
            type="primary",
            disabled=not all_answered,
            use_container_width=True,
            key="tutor_submit_all_btn",
        ):
            _run_tutor_grading()

    # ── Tombol keluar ─────────────────────────────────────────────
    st.markdown("---")
    if st.button("❌ Keluar", type="secondary", key="tutor_exit_btn"):
        session_id = _tget("session_id")
        if session_id:
            update_session_status(session_id, status="abandoned")
        _treset()
        st.rerun()


# ===================================================
# TOEFL Quiz Flow (existing — tidak dimodifikasi)
# ===================================================
def _run_toefl_quiz_flow():
    st.title("📝 Quiz Agent — TOEFL Style")
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
            st.markdown(
                f"Berdasarkan progress kamu, sistem merekomendasikan topik berikut "
                f"dengan level **{difficulty}**:"
            )
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


# ===================================================
# Grammar Tutor — Grading: batch Corrector
# ===================================================
def _run_tutor_grading():
    """
    Panggil Corrector untuk semua soal sekaligus setelah Submit All.

    Jawaban diambil dari session state key tutor_ans_{i} yang tersimpan
    otomatis oleh widget saat user mengetik. Semua hasil koreksi dikumpulkan
    ke list corrections lalu diteruskan ke _complete_tutor_session().

    Corrector fallback is_graded=False memastikan sesi tidak terputus
    meski LLM gagal untuk soal tertentu.
    """
    questions = _tget("questions", [])
    session_id = _tget("session_id")

    corrections = []

    with st.spinner(f"🔍 Menilai {len(questions)} jawaban..."):
        for i, q in enumerate(questions):
            user_answer = st.session_state.get(f"tutor_ans_{i}", "").strip()

            correction = run_tutor_corrector(
                topic=q["topic"],
                question_type=q["question_type"],
                question_text=q["question_text"],
                reference_answer=q["reference_answer"],
                user_answer=user_answer,
                session_id=session_id,
            )
            corrections.append(correction)

    _complete_tutor_session(corrections)


# ===================================================
# Grammar Tutor — Complete session: DB save + state
# ===================================================
def _complete_tutor_session(corrections: list):
    """
    Simpan semua hasil sesi ke DB dan set state ke "completed".

    Langkah:
      1. Update setiap soal di DB dengan jawaban + feedback Corrector
      2. Hitung score_pct dan breakdown kredit
      3. Update tutor_sessions dengan skor akhir
      4. Upsert tutor_topic_tracking per topik unik
      5. Update status sesi induk ke "completed"
      6. Simpan data summary ke tutor state → rerun

    Args:
        corrections: list dict hasil run_tutor_corrector, satu per soal.
                     Setiap dict berisi credit_level, score, is_graded,
                     dan feedback (verdict, concept_rule, feedback_tip).
    """
    questions = _tget("questions", [])
    question_ids = _tget("question_ids", [])
    session_id = _tget("session_id")
    is_adjusted = _tget("is_adjusted", False)
    total_questions = len(questions)

    # ── Langkah 1: Update setiap soal di DB ───────────────────────
    for i, (q_id, q, correction) in enumerate(zip(question_ids, questions, corrections)):
        user_answer = st.session_state.get(f"tutor_ans_{i}", "").strip()
        feedback = correction.get("feedback", {})
        update_tutor_question_answer(
            question_id=q_id,
            user_answer=user_answer,
            credit_level=correction.get("credit_level", "no_credit"),
            score=correction.get("score", 0.0),
            is_graded=correction.get("is_graded", True),
            feedback_verdict=feedback.get("verdict"),
            feedback_concept=feedback.get("concept_rule"),
            feedback_tip=feedback.get("feedback_tip"),
        )

    # ── Langkah 2: Hitung skor sesi ───────────────────────────────
    total_score = sum(c.get("score", 0.0) for c in corrections)
    score_pct = round((total_score / total_questions) * 100, 1) if total_questions else 0.0

    full_credit_count = sum(1 for c in corrections if c.get("credit_level") == "full_credit")
    partial_credit_count = sum(1 for c in corrections if c.get("credit_level") == "partial_credit")
    no_credit_count = sum(1 for c in corrections if c.get("credit_level") == "no_credit")

    # ── Langkah 3: Update tutor_sessions ──────────────────────────
    update_tutor_session_scores(
        session_id=session_id,
        full_credit_count=full_credit_count,
        partial_credit_count=partial_credit_count,
        no_credit_count=no_credit_count,
        score_pct=score_pct,
    )

    # ── Langkah 4: Upsert topic tracking per topik unik ───────────
    topic_stats: dict = {}
    for q, correction in zip(questions, corrections):
        topic = q["topic"]
        if topic not in topic_stats:
            topic_stats[topic] = {
                "full_credit": 0,
                "partial_credit": 0,
                "no_credit": 0,
                "total_score": 0.0,
                "question_count": 0,
            }
        stats = topic_stats[topic]
        stats["question_count"] += 1
        stats["total_score"] += correction.get("score", 0.0)
        level = correction.get("credit_level", "no_credit")
        if level == "full_credit":
            stats["full_credit"] += 1
        elif level == "partial_credit":
            stats["partial_credit"] += 1
        else:
            stats["no_credit"] += 1

    for topic, stats in topic_stats.items():
        qcount = stats["question_count"]
        topic_score_pct = round((stats["total_score"] / qcount) * 100, 1) if qcount else 0.0
        upsert_tutor_topic_tracking(
            topic=topic,
            session_score_pct=topic_score_pct,
            full_credit=stats["full_credit"],
            partial_credit=stats["partial_credit"],
            no_credit=stats["no_credit"],
            question_count=qcount,
        )

    # ── Langkah 5: Update status sesi induk ───────────────────────
    update_session_status(
        session_id=session_id,
        status="completed",
        is_adjusted=is_adjusted,
    )

    # ── Langkah 6: Simpan ke tutor state → completed ──────────────
    _tset("corrections", corrections)
    _tset("score_pct", score_pct)
    _tset("full_credit_count", full_credit_count)
    _tset("partial_credit_count", partial_credit_count)
    _tset("no_credit_count", no_credit_count)
    _tset("page_state", "completed")
    st.rerun()


# ===================================================
# Grammar Tutor — Render: summary akhir sesi
# ===================================================
def _render_tutor_summary():
    """
    Tampilkan ringkasan hasil sesi Grammar Tutor.

    Tiga bagian:
      1. Metrik skor (score_pct, breakdown kredit, topik)
      2. Detail per soal dengan feedback 3 layer dari Corrector
      3. Panel analytics on-demand (tombol jika belum ada, hasil jika sudah)
    """
    questions = _tget("questions", [])
    corrections = _tget("corrections", [])
    score_pct = _tget("score_pct", 0.0)
    full_credit_count = _tget("full_credit_count", 0)
    partial_credit_count = _tget("partial_credit_count", 0)
    no_credit_count = _tget("no_credit_count", 0)
    planner_output = _tget("planner_output", {})
    topics = planner_output.get("selected_topics", [])

    # ── Header + 3 metrik ─────────────────────────────────────────
    st.markdown("## 🎉 Sesi Grammar Tutor Selesai!")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Skor", f"{score_pct:.1f}%")
    with col2:
        st.metric(
            "Breakdown Kredit",
            f"{full_credit_count} full / {partial_credit_count} partial "
            f"/ {no_credit_count} no credit",
        )
    with col3:
        st.metric("Topik", ", ".join(topics) if topics else "—")

    # ── Detail per soal ───────────────────────────────────────────
    st.markdown("### Detail Jawaban")

    credit_icons = {
        "full_credit": "✅",
        "partial_credit": "🔶",
        "no_credit": "❌",
    }

    for i, (q, correction) in enumerate(zip(questions, corrections)):
        credit_level = correction.get("credit_level", "no_credit")
        icon = credit_icons.get(credit_level, "❌")
        user_answer = st.session_state.get(f"tutor_ans_{i}", "—").strip()
        feedback = correction.get("feedback", {})

        with st.expander(
            f"{icon} Soal {i + 1} — {q.get('topic', '-')}",
            expanded=(credit_level != "full_credit"),
        ):
            st.markdown(f"**Soal:** {q.get('question_text', '-')}")
            st.markdown(f"**Jawaban kamu:** {user_answer or '*(kosong)*'}")
            st.markdown(f"**Jawaban acuan:** {q.get('reference_answer', '-')}")

            if not correction.get("is_graded", True):
                st.warning("⚠️ Soal ini tidak berhasil dinilai karena kendala teknis.")
            else:
                st.markdown(f"**Verdict:** {feedback.get('verdict', '-')}")
                st.info(f"📖 **Konsep:** {feedback.get('concept_rule', '-')}")
                st.caption(f"💡 Tip: {feedback.get('feedback_tip', '-')}")

    # ── Panel analytics ───────────────────────────────────────────
    st.markdown("---")
    analytics = _tget("analytics")

    if analytics:
        st.markdown("### 💡 Insight dari AI")

        overall = analytics.get("overall_insight")
        if overall:
            st.info(overall)

        weak_topics = analytics.get("weak_topics", [])
        if weak_topics:
            st.markdown("**Topik yang perlu diperkuat:**")
            for t in weak_topics[:3]:
                st.markdown(f"- {t}")

        recommendations = analytics.get("recommendations", [])
        if recommendations:
            st.markdown("**Rekomendasi:**")
            for r in recommendations:
                st.markdown(f"- {r}")

    else:
        if st.button(
            "💡 Minta Analisis AI",
            type="secondary",
            key="tutor_analytics_btn",
        ):
            with st.spinner("Menganalisis progress Grammar Tutor kamu..."):
                analytics_result = run_tutor_analytics()
            _tset("analytics", analytics_result)
            st.rerun()

        st.caption(
            "Analisis AI memberikan insight tentang topik lemah dan "
            "rekomendasi latihan. Tersedia setelah minimal 3 sesi selesai."
        )

    # ── Tombol sesi baru ──────────────────────────────────────────
    st.markdown("---")
    if st.button("🔄 Sesi Baru", type="primary", key="tutor_new_session_btn"):
        _treset()
        st.rerun()


# ===================================================
# Grammar Tutor — State machine utama
# ===================================================
def _run_tutor_flow():
    """
    State machine Grammar Tutor. Routing berdasarkan tutor_page_state:
      "config"    → _render_tutor_config()
      "loading"   → _run_tutor_loading()
      "blocked"   → _render_prerequisite_block()
      "answering" → _run_tutor_answering()
      "completed" → _render_tutor_summary()
    """
    page_state = _tget("page_state", "config")

    if page_state == "config":
        _render_tutor_config()
    elif page_state == "loading":
        _run_tutor_loading()
    elif page_state == "blocked":
        _render_prerequisite_block(_tget("blocked_topics", []))
    elif page_state == "answering":
        _run_tutor_answering()
    elif page_state == "completed":
        _render_tutor_summary()


# ===================================================
# Entry point
# ===================================================
def main():
    """
    Entry point halaman Quiz Agent.

    Menampilkan mode selector di bagian atas, lalu routing
    ke flow yang sesuai berdasarkan pilihan user.

    Key quiz_prev_mode disimpan tanpa prefix agar tidak ikut
    terhapus oleh _reset() (TOEFL) atau _treset() (Tutor)
    ketika user berpindah mode.
    """
    st.title("📝 Quiz Agent")
    st.caption(
        "Pilih mode latihan: TOEFL Style untuk soal bergaya ujian, "
        "Grammar Tutor untuk membangun pemahaman konsep grammar."
    )

    mode = st.radio(
        "Pilih mode latihan:",
        options=["📝 TOEFL Style", "🎓 Grammar Tutor"],
        key="quiz_mode_selection",
        horizontal=True,
    )

    # ── Deteksi perpindahan mode, reset state mode sebelumnya ──
    prev_mode = st.session_state.get("quiz_prev_mode")
    if prev_mode is not None and prev_mode != mode:
        _reset()
        _treset()

    st.session_state["quiz_prev_mode"] = mode

    st.markdown("---")

    # ── Routing ──
    if mode == "📝 TOEFL Style":
        _run_toefl_quiz_flow()
    else:
        _run_tutor_flow()


if __name__ == "__main__":
    main()
