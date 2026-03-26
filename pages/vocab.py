"""
pages/vocab.py
--------------
Halaman UI Vocab Agent menggunakan Streamlit.

State machine:
  "init"      → Belum ada sesi, tampilkan tombol mulai
  "loading"   → Sedang generate soal (Planner → Generator → Validator)
  "answering" → User menjawab soal satu per satu
  "completed" → Semua soal selesai, tampilkan summary

Kenapa pakai state machine?
Streamlit re-run script dari atas setiap kali ada interaksi.
State di session_state memastikan kita tahu posisi user saat ini.
"""

import streamlit as st

from agents.vocab.analytics import run_analytics
from agents.vocab.evaluator import run_evaluator
from agents.vocab.generator import run_generator
from agents.vocab.planner import run_planner
from agents.vocab.validator import run_validator
from database.repositories.session_repository import (
    create_session,
    update_session_status,
)
from database.repositories.vocab_repository import (
    save_vocab_question,
    save_vocab_session,
    update_vocab_answer,
    update_vocab_session_scores,
    update_word_tracking,
)
from utils.helpers import calculate_score_pct, generate_session_id
from utils.logger import logger


# ===================================================
# Helper: State Management
# ===================================================
def _get_state(key, default=None):
    return st.session_state.get(f"vocab_{key}", default)


def _set_state(key, value):
    st.session_state[f"vocab_{key}"] = value


def _reset_state():
    keys = [k for k in st.session_state if k.startswith("vocab_")]
    for k in keys:
        del st.session_state[k]


# ===================================================
# Helper: Format soal sesuai tipe
# ===================================================
def _render_question(word_obj: dict, question_index: int, total: int):
    """Tampilkan soal dan return jawaban user."""
    fmt = word_obj.get("format", "tebak_arti")
    question_text = word_obj.get("question_text", "")

    st.markdown(f"**Soal {question_index + 1} dari {total}**")
    st.markdown(f"_{fmt.replace('_', ' ').title()}_")
    st.markdown(f"### {question_text}")

    user_answer = st.text_input(
        "Jawaban kamu:",
        key=f"vocab_answer_input_{question_index}",
        placeholder="Ketik jawaban di sini...",
    )
    return user_answer


def _render_feedback(eval_result: dict, word_obj: dict):
    """Tampilkan feedback setelah user submit jawaban."""
    is_correct = eval_result.get("is_correct", False)
    is_graded = eval_result.get("is_graded", True)
    feedback = eval_result.get("feedback", "")

    if not is_graded:
        st.warning("⚠️ " + feedback)
    elif is_correct:
        st.success("✅ **Benar!**")
        if feedback:
            st.caption(feedback)
    else:
        st.error("❌ **Kurang tepat**")
        st.caption(f"Jawaban yang benar: **{word_obj.get('correct_answer')}**")
        if feedback:
            st.caption(feedback)


def _render_summary(session_data: dict):
    """Tampilkan ringkasan skor di akhir sesi."""
    words = session_data.get("words", [])
    results = session_data.get("results", [])
    correct_count = sum(1 for r in results if r.get("is_correct"))
    total = len(words)
    score_pct = calculate_score_pct(correct_count, total)

    st.markdown("---")
    st.markdown("## 🎉 Sesi Selesai!")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Skor", f"{score_pct:.0f}%")
    with col2:
        st.metric("Benar", f"{correct_count}/{total}")
    with col3:
        difficulty = session_data.get("planner_output", {}).get(
            "difficulty_target", "-"
        )
        st.metric("Level", difficulty.title())

    # Tabel hasil per soal
    st.markdown("### Detail Jawaban")
    for i, (word_obj, result) in enumerate(zip(words, results)):
        icon = "✅" if result.get("is_correct") else "❌"
        with st.expander(f"{icon} Soal {i + 1}: {word_obj.get('question_text', '')}"):
            st.write(f"**Jawaban kamu:** {result.get('user_answer', '-')}")
            st.write(f"**Jawaban benar:** {word_obj.get('correct_answer', '-')}")
            if result.get("feedback"):
                st.caption(result["feedback"])

    # Analytics insight jika ada
    analytics = session_data.get("analytics")
    if analytics and analytics.get("insight"):
        st.markdown("---")
        st.markdown("### 💡 Insight dari AI")
        st.info(analytics["insight"])


# ===================================================
# Flow: Inisialisasi sesi baru
# ===================================================
def _start_new_session(topic: str):
    """Jalankan Planner → Generator → Validator dan simpan ke DB."""
    _set_state("page_state", "loading")

    try:
        # Step 1: Planner
        with st.spinner("🧠 Menyiapkan soal untukmu..."):
            planner_output = run_planner(topic=topic)
            logger.info(f"[vocab_page] Planner done: {planner_output}")

        # Step 2: Generator
        with st.spinner("✍️ Membuat soal vocab..."):
            generator_output = run_generator(planner_output)

        # Step 3: Validator
        with st.spinner("🔍 Memvalidasi soal..."):
            validator_result = run_validator(planner_output, generator_output)
            final_words = validator_result.get("final_words", [])
            is_adjusted = validator_result.get("is_adjusted", False)

        if not final_words:
            st.error("Gagal membuat soal. Silakan coba lagi.")
            _set_state("page_state", "init")
            return

        # Step 4: Buat session di DB
        session_id = generate_session_id()
        create_session(session_id, mode="vocab")
        save_vocab_session(
            session_id=session_id,
            topic=planner_output["topic"],
            total_words=len(final_words),
            new_words=planner_output["new_words"],
            review_words=planner_output["review_words"],
        )

        # Step 5: Simpan soal ke DB (incremental)
        question_ids = []
        for word_obj in final_words:
            q_id = save_vocab_question(
                session_id=session_id,
                word=word_obj["word"],
                format=word_obj["format"],
                difficulty=word_obj["difficulty"],
                question_text=word_obj["question_text"],
                correct_answer=word_obj["correct_answer"],
                is_new_word=word_obj["is_new"],
            )
            question_ids.append(q_id)

        # Simpan ke session state
        _set_state("session_id", session_id)
        _set_state("planner_output", planner_output)
        _set_state("words", final_words)
        _set_state("question_ids", question_ids)
        _set_state("is_adjusted", is_adjusted)
        _set_state("current_index", 0)
        _set_state("results", [])
        _set_state("page_state", "answering")

        if is_adjusted:
            st.warning("⚠️ Soal disesuaikan otomatis karena validasi tidak sempurna.")

        st.rerun()

    except RuntimeError as e:
        # Generator gagal total setelah 3x retry
        st.error(
            "😔 Gagal membuat soal setelah beberapa kali percobaan. "
            "Silakan coba lagi dalam beberapa saat."
        )
        logger.error(f"[vocab_page] Session creation failed: {e}")
        _set_state("page_state", "init")

    except Exception as e:
        st.error(f"Terjadi kesalahan: {e}")
        logger.error(f"[vocab_page] Unexpected error: {e}")
        _set_state("page_state", "init")


# ===================================================
# Flow: User menjawab soal
# ===================================================
def _handle_answer_submission(user_answer: str):
    """Evaluasi jawaban, simpan ke DB, lanjut ke soal berikutnya."""
    current_index = _get_state("current_index", 0)
    words = _get_state("words", [])
    question_ids = _get_state("question_ids", [])
    session_id = _get_state("session_id")
    results = _get_state("results", [])

    word_obj = words[current_index]
    q_id = question_ids[current_index]

    # Evaluasi jawaban
    with st.spinner("Menilai jawaban..."):
        eval_result = run_evaluator(
            word=word_obj["word"],
            format=word_obj["format"],
            question_text=word_obj["question_text"],
            correct_answer=word_obj["correct_answer"],
            user_answer=user_answer,
            session_id=session_id,
        )

    # Simpan jawaban ke DB (incremental save)
    update_vocab_answer(
        question_id=q_id,
        user_answer=user_answer,
        is_correct=eval_result.get("is_correct", False),
        is_graded=eval_result.get("is_graded", True),
    )

    # Simpan hasil ke state
    results.append(
        {
            **eval_result,
            "user_answer": user_answer,
        }
    )
    _set_state("results", results)
    _set_state("last_eval", eval_result)
    _set_state("last_word_obj", word_obj)
    _set_state("waiting_next", True)  # Tampilkan feedback dulu


# ===================================================
# Flow: Akhir sesi
# ===================================================
def _complete_session():
    """Update DB, update word tracking, trigger analytics."""
    session_id = _get_state("session_id")
    words = _get_state("words", [])
    results = _get_state("results", [])
    planner_output = _get_state("planner_output", {})
    is_adjusted = _get_state("is_adjusted", False)

    correct_count = sum(1 for r in results if r.get("is_correct"))
    total = len(words)
    score_pct = calculate_score_pct(correct_count, total)

    # Update skor sesi di DB
    update_vocab_session_scores(
        session_id=session_id,
        correct_count=correct_count,
        wrong_count=total - correct_count,
        score_pct=score_pct,
    )

    # Update word tracking (spaced repetition)
    for word_obj, result in zip(words, results):
        if result.get("is_graded"):
            update_word_tracking(
                word=word_obj["word"],
                topic=planner_output.get("topic", "sehari_hari"),
                difficulty=word_obj["difficulty"],
                is_correct=result.get("is_correct", False),
            )

    # Tandai sesi completed di DB
    update_session_status(
        session_id=session_id,
        status="completed",
        is_adjusted=is_adjusted,
    )

    # Trigger analytics (jika data cukup)
    analytics_result = run_analytics()
    _set_state("analytics", analytics_result)
    _set_state("page_state", "completed")


# ===================================================
# Main UI
# ===================================================
def main():
    st.title("📚 Vocab Agent")
    st.caption("Latihan kosakata bahasa Inggris dengan AI")

    page_state = _get_state("page_state", "init")

    # -----------------------------------------------
    # STATE: init — Pilih topik dan mulai
    # -----------------------------------------------
    if page_state == "init":
        st.markdown("### Pilih Topik Sesi")
        st.markdown("AI akan menyesuaikan soal berdasarkan histori belajarmu.")

        topic_options = {
            "sehari_hari": "🏠 Sehari-hari",
            "perkenalan": "👋 Perkenalan",
            "di_rumah": "🏡 Di Rumah",
            "di_kampus": "🎓 Di Kampus",
            "perjalanan": "✈️ Perjalanan",
            "kesehatan": "🏥 Kesehatan",
            "teknologi": "💻 Teknologi",
            "lingkungan": "🌿 Lingkungan",
        }

        selected_topic = st.selectbox(
            "Topik:",
            options=list(topic_options.keys()),
            format_func=lambda x: topic_options[x],
            key="vocab_topic_select",
        )

        col1, col2 = st.columns([1, 3])
        with col1:
            if st.button(
                "🚀 Mulai Sesi",
                type="primary",
                use_container_width=True,
                key="vocab_start_btn",
            ):
                _start_new_session(topic=selected_topic)

    # -----------------------------------------------
    # STATE: loading — Sedang memproses
    # -----------------------------------------------
    elif page_state == "loading":
        st.info("Sedang menyiapkan sesi...")

    # -----------------------------------------------
    # STATE: answering — User menjawab soal
    # -----------------------------------------------
    elif page_state == "answering":
        words = _get_state("words", [])
        current_index = _get_state("current_index", 0)
        results = _get_state("results", [])
        waiting_next = _get_state("waiting_next", False)
        planner_output = _get_state("planner_output", {})

        total = len(words)

        # Progress bar
        progress = current_index / total
        st.progress(progress, text=f"Soal {current_index + 1} dari {total}")
        st.caption(
            f"Topik: **{planner_output.get('topic', '-')}** | "
            f"Level: **{planner_output.get('difficulty_target', '-').title()}**"
        )
        st.markdown("---")

        # Tampilkan feedback soal sebelumnya (jika ada)
        if waiting_next and current_index > 0:
            last_eval = _get_state("last_eval")
            last_word_obj = _get_state("last_word_obj")
            if last_eval and last_word_obj:
                _render_feedback(last_eval, last_word_obj)
                st.markdown("---")

        # Tampilkan soal saat ini atau tombol selesai
        if current_index < total:
            word_obj = words[current_index]
            user_answer = _render_question(word_obj, current_index, total)

            col1, col2 = st.columns([1, 4])
            with col1:
                submit = st.button(
                    "Submit ✓",
                    type="primary",
                    use_container_width=True,
                    disabled=not user_answer.strip(),
                    key=f"vocab_submit_btn_{current_index}",
                )

            if submit and user_answer.strip():
                _handle_answer_submission(user_answer)
                _set_state("current_index", current_index + 1)
                _set_state("waiting_next", True)

                # Cek apakah ini soal terakhir
                if current_index + 1 >= total:
                    _complete_session()
                else:
                    st.rerun()

        # Tombol keluar
        st.markdown("---")
        if st.button("❌ Keluar dari sesi", type="secondary", key="vocab_exit_btn"):
            session_id = _get_state("session_id")
            if session_id:
                update_session_status(session_id, status="abandoned")
            _reset_state()
            st.rerun()

    # -----------------------------------------------
    # STATE: completed — Tampilkan summary
    # -----------------------------------------------
    elif page_state == "completed":
        session_data = {
            "words": _get_state("words", []),
            "results": _get_state("results", []),
            "planner_output": _get_state("planner_output", {}),
            "analytics": _get_state("analytics"),
        }
        _render_summary(session_data)

        st.markdown("---")
        col1, col2 = st.columns(2)
        with col1:
            if st.button(
                "🔄 Sesi Baru",
                type="primary",
                use_container_width=True,
                key="vocab_new_session_btn",
            ):
                _reset_state()
                st.rerun()


# Entry point — hanya dipanggil langsung, bukan saat di-import
if __name__ == "__main__":
    main()
