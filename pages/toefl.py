"""
pages/toefl.py
--------------
Halaman UI TOEFL Simulator.

State machine:
  "init"          -> Cek sesi paused atau tampilkan pilihan mulai baru
  "mode_select"   -> Pilih mode 50% / 75% / 100%, tampilkan estimasi waktu
  "generating"    -> Panggil ketiga generator + validator
  "listening"     -> Section 1: putar audio + soal + timer
  "pause_screen"  -> Antar section: lanjut sekarang atau simpan & keluar
  "structure"     -> Section 2: soal fill blank & error identification + timer
  "reading"       -> Section 3: passage + soal + timer
  "scoring"       -> Panggil evaluator, hitung estimated score
  "completed"     -> Tampilkan skor per section + estimated score + analytics

Timer:
  Countdown berbasis selisih datetime.now() - section_start_time.
  st.rerun() dipanggil setiap detik via time.sleep(1) di blok timer.
  Saat timer habis -> section di-force submit, lanjut ke state berikutnya.

Pause screen:
  Muncul setelah Listening dan setelah Structure.
  Dua pilihan: "Lanjut Sekarang" atau "Simpan & Keluar".
  "Simpan & Keluar" memanggil toefl_session_manager.pause_session().
"""

import json
import time
from datetime import datetime

import streamlit as st

from agents.toefl.listening_generator import run_generator as run_listening_generator
from agents.toefl.structure_generator import run_generator as run_structure_generator
from agents.toefl.reading_generator import run_generator as run_reading_generator
from agents.toefl.analytics import run_analytics
from modules.scoring.toefl_converter import process_full_score
from modules.session.toefl_session_manager import (
    pause_session,
    resume_session,
    get_paused_session_info,
    cleanup_expired_toefl_sessions,
    SECTION_NAMES,
)
from database.repositories.session_repository import (
    create_session,
    update_session_status,
    get_sessions_by_mode,
)
from database.repositories.toefl_repository import (
    save_toefl_session,
    save_toefl_question,
    update_toefl_answer,
    update_toefl_scores,
    update_current_section,
    get_toefl_session,
)
from utils.helpers import generate_session_id
from utils.logger import logger

# ===================================================
# Konstanta distribusi soal per mode
# ===================================================
MODE_CONFIG = {
    "50%": {
        "listening": {"total": 25, "part_a": 15, "part_b": 4, "part_c": 6},
        "structure": {"total": 20, "part_a": 8, "part_b": 12},
        "reading": {"total": 25, "passages": 3},
        "timers": {"listening": 1050, "structure": 750, "reading": 1650},
        "duration_hint": "±60 menit",
    },
    "75%": {
        "listening": {"total": 38, "part_a": 23, "part_b": 6, "part_c": 9},
        "structure": {"total": 30, "part_a": 11, "part_b": 19},
        "reading": {"total": 37, "passages": 4},
        "timers": {"listening": 1575, "structure": 1125, "reading": 2475},
        "duration_hint": "±90 menit",
    },
    "100%": {
        "listening": {"total": 50, "part_a": 30, "part_b": 8, "part_c": 12},
        "structure": {"total": 40, "part_a": 15, "part_b": 25},
        "reading": {"total": 50, "passages": 5},
        "timers": {"listening": 2100, "structure": 1500, "reading": 3300},
        "duration_hint": "±120 menit",
    },
}

# Mapping section number ke nama state
SECTION_STATE = {1: "listening", 2: "structure", 3: "reading"}


# ===================================================
# Session state helpers — semua key pakai prefix "tf_"
# ===================================================
def _get(key, default=None):
    return st.session_state.get(f"tf_{key}", default)


def _set(key, value):
    st.session_state[f"tf_{key}"] = value


def _reset():
    keys = [k for k in st.session_state if k.startswith("tf_")]
    for k in keys:
        del st.session_state[k]


# ===================================================
# Timer helpers
# ===================================================
def _seconds_remaining(section: str) -> int:
    """
    Hitung sisa detik untuk section yang sedang berjalan.
    Mengembalikan 0 jika sudah habis.
    """
    start_key = f"timer_start_{section}"
    allotted = MODE_CONFIG[_get("mode", "50%")]["timers"][section]
    start_time = _get(start_key)

    if start_time is None:
        return allotted

    elapsed = int((datetime.now() - start_time).total_seconds())
    return max(0, allotted - elapsed)


def _start_timer(section: str):
    """Catat waktu mulai section jika belum dicatat."""
    start_key = f"timer_start_{section}"
    if _get(start_key) is None:
        _set(start_key, datetime.now())


def _format_time(seconds: int) -> str:
    """Format detik ke 'MM:SS'."""
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"


def _render_timer(section: str, placeholder):
    """
    Render countdown timer di placeholder yang diberikan.
    Dipanggil di awal setiap render section untuk update tampilan.
    """
    remaining = _seconds_remaining(section)
    color = "red" if remaining < 120 else "orange" if remaining < 300 else "green"
    placeholder.markdown(f"⏱️ Waktu tersisa: **:{color}[{_format_time(remaining)}]**")
    return remaining


# ===================================================
# State: init
# ===================================================
def _render_init():
    st.title("📊 TOEFL Simulator")
    st.markdown("Simulasi TOEFL ITP dengan estimasi skor resmi.")
    st.markdown("---")

    # Bersihkan sesi expired sebelum tampilkan pilihan
    cleanup_expired_toefl_sessions()

    # Cek apakah ada sesi paused yang bisa di-resume
    paused_sessions = get_sessions_by_mode("toefl", limit=5)
    paused = next((s for s in paused_sessions if s.get("status") == "paused"), None)

    if paused:
        paused_info = get_paused_session_info(paused["session_id"])
        if paused_info:
            st.warning(
                f"📌 **Kamu punya simulasi yang belum selesai.**\n\n"
                f"Mode **{paused_info['mode']}** — "
                f"dilanjutkan dari **{paused_info['next_section_name']}** section.\n\n"
                f"Berlaku hingga: `{paused_info['expires_at']}`"
            )
            col1, col2 = st.columns(2)
            with col1:
                if st.button("▶️ Lanjutkan Simulasi", use_container_width=True, type="primary"):
                    result = resume_session(paused["session_id"])
                    if result.success:
                        _set("session_id", paused["session_id"])
                        _set("mode", paused_info["mode"])
                        _set("resumed", True)
                        _set("resume_state", result.state)
                        # Langsung lompat ke section yang harus dilanjutkan
                        next_sec = result.state.get("current_section", 2)
                        next_state = SECTION_STATE.get(next_sec, "listening")
                        _set("state", next_state)
                        st.rerun()
                    else:
                        st.error(result.reason)
            with col2:
                if st.button("🆕 Mulai Simulasi Baru", use_container_width=True):
                    _set("state", "mode_select")
                    st.rerun()
            return

    if st.button("🚀 Mulai Simulasi Baru", type="primary"):
        _set("state", "mode_select")
        st.rerun()


# ===================================================
# State: mode_select
# ===================================================
def _render_mode_select():
    st.title("📊 TOEFL Simulator")
    st.markdown("### Pilih Mode Simulasi")
    st.markdown("---")

    for mode, cfg in MODE_CONFIG.items():
        total = cfg["listening"]["total"] + cfg["structure"]["total"] + cfg["reading"]["total"]
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**Mode {mode}** — {total} soal, {cfg['duration_hint']}")
                st.caption(
                    f"Listening: {cfg['listening']['total']} soal  |  "
                    f"Structure: {cfg['structure']['total']} soal  |  "
                    f"Reading: {cfg['reading']['total']} soal"
                )
            with col2:
                if st.button(f"Pilih {mode}", key=f"mode_{mode}", use_container_width=True):
                    _set("mode", mode)
                    _set("state", "generating")
                    st.rerun()

    st.markdown("---")
    st.info(
        "💡 **Tips:** Mulai dengan mode **50%** jika ini simulasi pertamamu. "
        "Naikkan mode setelah estimasi skor stabil di atas targetmu."
    )


# ===================================================
# State: generating
# ===================================================
def _render_generating():
    mode = _get("mode", "50%")
    cfg = MODE_CONFIG[mode]

    st.title("📊 TOEFL Simulator")
    st.markdown(f"### Menyiapkan simulasi mode **{mode}**...")

    progress_bar = st.progress(0, text="Memulai...")

    try:
        # Buat session di DB
        session_id = generate_session_id()
        create_session(session_id, "toefl")
        save_toefl_session(session_id, mode)
        _set("session_id", session_id)

        # Generate Listening
        progress_bar.progress(10, text="📻 Membuat soal Listening...")
        listening_data = run_listening_generator(
            part_a_count=cfg["listening"]["part_a"],
            part_b_count=cfg["listening"]["part_b"],
            part_c_count=cfg["listening"]["part_c"],
            session_id=session_id,
        )
        _set("listening_data", listening_data)
        progress_bar.progress(45, text="📝 Membuat soal Structure...")

        # Generate Structure
        structure_data = run_structure_generator(
            part_a_count=cfg["structure"]["part_a"],
            part_b_count=cfg["structure"]["part_b"],
            session_id=session_id,
        )
        _set("structure_data", structure_data)
        progress_bar.progress(75, text="📖 Membuat soal Reading...")

        # Generate Reading
        reading_data = run_reading_generator(
            passage_count=cfg["reading"]["passages"],
            session_id=session_id,
        )
        _set("reading_data", reading_data)
        progress_bar.progress(95, text="✅ Finalisasi...")

        # Simpan semua soal ke DB
        _save_all_questions_to_db(session_id, listening_data, structure_data, reading_data)

        progress_bar.progress(100, text="Siap!")
        time.sleep(0.5)

        _set("state", "listening")
        st.rerun()

    except Exception as e:
        logger.error(f"[toefl_ui] Generating failed: {e}")
        update_session_status(_get("session_id"), "incomplete")
        st.error("❌ Gagal menyiapkan soal simulasi. Silakan coba lagi.\n\n" f"Detail: {str(e)}")
        if st.button("🔄 Coba Lagi"):
            _reset()
            st.rerun()


def _save_all_questions_to_db(
    session_id: str,
    listening_data: dict,
    structure_data: dict,
    reading_data: dict,
):
    """Simpan seluruh soal dari ketiga section ke DB secara incremental."""
    q_num = 1

    # Listening
    for item in listening_data.get("items", []):
        for q in item.get("questions", []):
            qid = save_toefl_question(
                session_id=session_id,
                section="1",
                part=item.get("part", "A"),
                question_number=q_num,
                question_text=q.get("question", ""),
                options=json.dumps(q.get("options", [])),
                correct_answer=q.get("correct_answer", ""),
                difficulty=q.get("difficulty", "medium"),
                audio_script=item.get("script", ""),
            )
            q["_db_id"] = qid
            q_num += 1

    q_num = 1
    # Structure
    for q in structure_data.get("questions", []):
        qid = save_toefl_question(
            session_id=session_id,
            section="2",
            part=q.get("part", "A"),
            question_number=q_num,
            question_text=q.get("question", ""),
            options=json.dumps(q.get("options", [])),
            correct_answer=q.get("correct_answer", ""),
            difficulty=q.get("difficulty", "medium"),
        )
        q["_db_id"] = qid
        q_num += 1

    q_num = 1
    # Reading
    for passage in reading_data.get("passages", []):
        for q in passage.get("questions", []):
            qid = save_toefl_question(
                session_id=session_id,
                section="3",
                part="A",
                question_number=q_num,
                question_text=q.get("question", ""),
                options=json.dumps(q.get("options", [])),
                correct_answer=q.get("correct_answer", ""),
                difficulty=q.get("difficulty", "medium"),
                passage_text=passage.get("text", ""),
            )
            q["_db_id"] = qid
            q_num += 1


# ===================================================
# State: listening (Section 1)
# ===================================================
def _render_listening():
    _start_timer("listening")
    timer_placeholder = st.empty()
    remaining = _render_timer("listening", timer_placeholder)

    st.markdown("## 📻 Section 1 — Listening Comprehension")
    st.markdown("---")

    listening_data = _get("listening_data", {})
    items = listening_data.get("items", [])
    answers = _get("listening_answers", {})

    if not items:
        st.error("Data listening tidak ditemukan. Silakan mulai ulang.")
        return

    # Render soal per item (satu item = satu audio/dialog + beberapa soal)
    for item_idx, item in enumerate(items):
        part = item.get("part", "A")
        script = item.get("script", "")

        with st.expander(f"Part {part} — Item {item_idx + 1}", expanded=True):
            # Tampilkan audio jika tersedia, fallback ke script
            audio_path = item.get("audio_path")
            if audio_path:
                try:
                    with open(audio_path, "rb") as f:
                        st.audio(f.read(), format="audio/wav")
                except Exception:
                    st.info(f"🎧 *[Audio tidak tersedia — baca script berikut]*\n\n{script}")
            else:
                st.info(f"🎧 *[Audio tidak tersedia — baca script berikut]*\n\n{script}")

            # Soal per item
            for q in item.get("questions", []):
                q_key = f"L_{item_idx}_{q.get('_db_id', id(q))}"
                options = q.get("options", [])
                st.markdown(f"**{q.get('question', '')}**")
                answers[q_key] = st.radio(
                    label="Pilih jawaban:",
                    options=options,
                    key=f"radio_{q_key}",
                    index=options.index(answers[q_key]) if answers.get(q_key) in options else 0,
                    label_visibility="collapsed",
                )
            st.markdown("")

    _set("listening_answers", answers)

    # Submit
    col1, col2 = st.columns([2, 1])
    with col1:
        if st.button(
            "✅ Selesai Listening — Lanjut ke Pause Screen",
            use_container_width=True,
            type="primary",
        ):
            _save_section_answers("listening", items, answers)
            _set("state", "pause_screen_1")
            st.rerun()

    # Auto-submit jika timer habis
    if remaining == 0:
        st.warning("⏰ Waktu habis! Jawaban otomatis disimpan.")
        _save_section_answers("listening", items, answers)
        _set("state", "pause_screen_1")
        time.sleep(1)
        st.rerun()

    # Refresh timer setiap detik
    time.sleep(1)
    st.rerun()


def _save_section_answers(section_name: str, items_or_questions, answers: dict):
    """Simpan jawaban section listening ke DB."""
    session_id = _get("session_id")
    if not session_id:
        return

    if section_name == "listening":
        for item_idx, item in enumerate(items_or_questions):
            for q in item.get("questions", []):
                q_key = f"L_{item_idx}_{q.get('_db_id', id(q))}"
                db_id = q.get("_db_id")
                answer = answers.get(q_key, "")
                correct = q.get("correct_answer", "")
                if db_id:
                    update_toefl_answer(db_id, answer, answer == correct)

    elif section_name == "structure":
        for q in items_or_questions:
            q_key = f"S_{q.get('_db_id', id(q))}"
            db_id = q.get("_db_id")
            answer = answers.get(q_key, "")
            correct = q.get("correct_answer", "")
            if db_id:
                update_toefl_answer(db_id, answer, answer == correct)

    elif section_name == "reading":
        for passage in items_or_questions:
            for q in passage.get("questions", []):
                q_key = f"R_{q.get('_db_id', id(q))}"
                db_id = q.get("_db_id")
                answer = answers.get(q_key, "")
                correct = q.get("correct_answer", "")
                if db_id:
                    update_toefl_answer(db_id, answer, answer == correct)


# ===================================================
# State: pause_screen (antar section)
# ===================================================
def _render_pause_screen(after_section: int):
    """
    Pause screen setelah section selesai.
    after_section: 1 = setelah Listening, 2 = setelah Structure
    """
    next_section_name = SECTION_NAMES.get(after_section + 1, "")
    done_name = SECTION_NAMES.get(after_section, "")

    st.title("⏸️ Jeda Antar Section")
    st.markdown("---")
    st.success(f"✅ **{done_name}** selesai!")
    st.markdown(f"Section berikutnya: **{next_section_name}**")
    st.markdown("---")

    col1, col2 = st.columns(2)
    with col1:
        if st.button(f"▶️ Lanjut ke {next_section_name}", use_container_width=True, type="primary"):
            session_id = _get("session_id")
            if session_id:
                update_current_section(session_id, after_section + 1)
            next_state = SECTION_STATE.get(after_section + 1, "scoring")
            _set("state", next_state)
            st.rerun()

    with col2:
        if st.button("💾 Simpan & Keluar", use_container_width=True):
            session_id = _get("session_id")
            mode = _get("mode", "50%")
            result = pause_session(
                session_id=session_id,
                completed_section=after_section,
                mode=mode,
            )
            if result.success:
                st.success(
                    f"✅ Simulasi disimpan. Kamu bisa melanjutkan hingga:\n\n"
                    f"**{result.expires_at}**\n\n"
                    "Tutup halaman ini sekarang."
                )
                _reset()
            else:
                st.error(f"Gagal menyimpan: {result.reason}")


# ===================================================
# State: structure (Section 2)
# ===================================================
def _render_structure():
    _start_timer("structure")
    timer_placeholder = st.empty()
    remaining = _render_timer("structure", timer_placeholder)

    st.markdown("## 📝 Section 2 — Structure & Written Expression")
    st.markdown("---")

    structure_data = _get("structure_data", {})
    questions = structure_data.get("questions", [])
    answers = _get("structure_answers", {})

    if not questions:
        st.error("Data structure tidak ditemukan. Silakan mulai ulang.")
        return

    # Pisahkan Part A (fill blank) dan Part B (error identification)
    part_a = [q for q in questions if q.get("part") == "A"]
    part_b = [q for q in questions if q.get("part") == "B"]

    if part_a:
        st.markdown("### Part A — Structure (Complete the sentence)")
        for q in part_a:
            q_key = f"S_{q.get('_db_id', id(q))}"
            options = q.get("options", [])
            st.markdown(f"**{q.get('question', '')}**")
            answers[q_key] = st.radio(
                label="Pilih jawaban:",
                options=options,
                key=f"radio_{q_key}",
                index=options.index(answers[q_key]) if answers.get(q_key) in options else 0,
                label_visibility="collapsed",
            )
            st.markdown("")

    if part_b:
        st.markdown("### Part B — Written Expression (Identify the error)")
        st.caption("Pilih bagian kalimat yang mengandung kesalahan grammar.")
        for q in part_b:
            q_key = f"S_{q.get('_db_id', id(q))}"
            options = q.get("options", [])
            st.markdown(f"**{q.get('question', '')}**")
            answers[q_key] = st.radio(
                label="Pilih bagian yang salah:",
                options=options,
                key=f"radio_{q_key}",
                index=options.index(answers[q_key]) if answers.get(q_key) in options else 0,
                label_visibility="collapsed",
            )
            st.markdown("")

    _set("structure_answers", answers)

    col1, col2 = st.columns([2, 1])
    with col1:
        if st.button(
            "✅ Selesai Structure — Lanjut ke Pause Screen",
            use_container_width=True,
            type="primary",
        ):
            _save_section_answers("structure", questions, answers)
            _set("state", "pause_screen_2")
            st.rerun()

    if remaining == 0:
        st.warning("⏰ Waktu habis! Jawaban otomatis disimpan.")
        _save_section_answers("structure", questions, answers)
        _set("state", "pause_screen_2")
        time.sleep(1)
        st.rerun()

    time.sleep(1)
    st.rerun()


# ===================================================
# State: reading (Section 3)
# ===================================================
def _render_reading():
    _start_timer("reading")
    timer_placeholder = st.empty()
    remaining = _render_timer("reading", timer_placeholder)

    st.markdown("## 📖 Section 3 — Reading Comprehension")
    st.markdown("---")

    reading_data = _get("reading_data", {})
    passages = reading_data.get("passages", [])
    answers = _get("reading_answers", {})

    if not passages:
        st.error("Data reading tidak ditemukan. Silakan mulai ulang.")
        return

    for p_idx, passage in enumerate(passages):
        with st.expander(f"Passage {p_idx + 1}", expanded=(p_idx == 0)):
            st.markdown(passage.get("text", ""))
            st.markdown("---")
            for q in passage.get("questions", []):
                q_key = f"R_{q.get('_db_id', id(q))}"
                options = q.get("options", [])
                st.markdown(f"**{q.get('question', '')}**")
                answers[q_key] = st.radio(
                    label="Pilih jawaban:",
                    options=options,
                    key=f"radio_{q_key}",
                    index=options.index(answers[q_key]) if answers.get(q_key) in options else 0,
                    label_visibility="collapsed",
                )
                st.markdown("")

    _set("reading_answers", answers)

    if st.button("✅ Selesai Reading — Hitung Skor", use_container_width=True, type="primary"):
        passages_list = reading_data.get("passages", [])
        _save_section_answers("reading", passages_list, answers)
        _set("state", "scoring")
        st.rerun()

    if remaining == 0:
        st.warning("⏰ Waktu habis! Jawaban otomatis disimpan.")
        passages_list = reading_data.get("passages", [])
        _save_section_answers("reading", passages_list, answers)
        _set("state", "scoring")
        time.sleep(1)
        st.rerun()

    time.sleep(1)
    st.rerun()


# ===================================================
# State: scoring
# ===================================================
def _render_scoring():
    st.title("📊 Menghitung Skor...")
    st.markdown("Mohon tunggu, sedang menghitung estimasi skor TOEFL ITP kamu.")

    with st.spinner("Menghitung..."):
        session_id = _get("session_id")
        mode = _get("mode", "50%")
        cfg = MODE_CONFIG[mode]

        # Hitung raw score per section dari DB
        toefl_data = get_toefl_session(session_id)
        questions = toefl_data.get("questions", []) if toefl_data else []

        def _raw(section_num: str) -> int:
            return sum(
                1
                for q in questions
                if q.get("section") == section_num and q.get("is_correct") is True
            )

        l_raw = _raw("1")
        s_raw = _raw("2")
        r_raw = _raw("3")

        # Proses konversi lengkap
        score_result = process_full_score(
            listening_raw=l_raw,
            structure_raw=s_raw,
            reading_raw=r_raw,
            listening_total_mode=cfg["listening"]["total"],
            structure_total_mode=cfg["structure"]["total"],
            reading_total_mode=cfg["reading"]["total"],
        )

        # Simpan ke DB
        update_toefl_scores(
            session_id=session_id,
            listening_raw=l_raw,
            structure_raw=s_raw,
            reading_raw=r_raw,
            listening_extrapolated=score_result["listening_extrapolated"],
            structure_extrapolated=score_result["structure_extrapolated"],
            reading_extrapolated=score_result["reading_extrapolated"],
            listening_scaled=score_result["listening_scaled"],
            structure_scaled=score_result["structure_scaled"],
            reading_scaled=score_result["reading_scaled"],
            estimated_score=score_result["estimated_score"],
        )
        update_session_status(session_id, "completed")

        _set("score_result", score_result)
        _set("state", "completed")
        st.rerun()


# ===================================================
# State: completed
# ===================================================
def _render_completed():
    score = _get("score_result", {})
    mode = _get("mode", "50%")
    cfg = MODE_CONFIG[mode]

    st.title("🎉 Simulasi Selesai!")
    st.markdown("---")

    # Estimated score — headline
    estimated = score.get("estimated_score", 0)
    st.markdown(
        f"<div style='text-align:center; padding: 20px;'>"
        f"<p style='font-size:1.1em; color:gray;'>Estimasi Skor TOEFL ITP</p>"
        f"<p style='font-size:3.5em; font-weight:bold; color:#1f77b4;'>{estimated}</p>"
        f"<p style='font-size:0.9em; color:gray;'>Skala 310–677 | Mode {mode}</p>"
        f"</div>",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown("### Breakdown Per Section")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric(
            label="📻 Listening",
            value=score.get("listening_scaled", 0),
            help=(f"Raw: {score.get('listening_raw', 0)} / " f"{cfg['listening']['total']} soal"),
        )

    with col2:
        st.metric(
            label="📝 Structure",
            value=score.get("structure_scaled", 0),
            help=(f"Raw: {score.get('structure_raw', 0)} / " f"{cfg['structure']['total']} soal"),
        )

    with col3:
        st.metric(
            label="📖 Reading",
            value=score.get("reading_scaled", 0),
            help=(f"Raw: {score.get('reading_raw', 0)} / " f"{cfg['reading']['total']} soal"),
        )

    # Detail teknis (collapsible)
    with st.expander("📊 Detail Konversi Skor"):
        st.markdown(
            f"| Section | Raw | Extrapolated | Scaled |\n"
            f"|---------|-----|--------------|--------|\n"
            f"| Listening  | {score.get('listening_raw')} | {score.get('listening_extrapolated')} | {score.get('listening_scaled')} |\n"
            f"| Structure  | {score.get('structure_raw')} | {score.get('structure_extrapolated')} | {score.get('structure_scaled')} |\n"
            f"| Reading    | {score.get('reading_raw')} | {score.get('reading_extrapolated')} | {score.get('reading_scaled')} |\n"
        )
        st.caption("Formula: Estimated = (L + S + R) × 10 / 3, skala 310–677")

    st.markdown("---")

    # Analytics insight
    st.markdown("### 📈 Analisis Progress")
    with st.spinner("Menganalisis perkembangan skor..."):
        analytics = run_analytics()

    if analytics.get("insight"):
        trend_icon = {
            "improving": "📈",
            "stable": "➡️",
            "declining": "📉",
            "insufficient_data": "📊",
        }.get(analytics.get("score_trend", ""), "📊")

        st.info(f"{trend_icon} {analytics['insight']}")

        if analytics.get("mode_recommendation"):
            st.success(f"💡 {analytics['mode_recommendation']}")

        # Section averages jika ada
        section_avgs = analytics.get("section_averages", {})
        if any(v for v in section_avgs.values()):
            weakest = analytics.get("weakest_section", "").capitalize()
            st.warning(
                f"⚠️ Section paling lemah: **{weakest}** — fokus di sini untuk kenaikan skor terbesar."
            )

    else:
        st.info("📊 Kumpulkan minimal 3 simulasi untuk melihat analisis tren skor.")

    st.markdown("---")
    if st.button("🔄 Mulai Simulasi Baru", type="primary"):
        _reset()
        st.rerun()


# ===================================================
# Entry point
# ===================================================
def main():
    state = _get("state", "init")

    if state == "init":
        _render_init()
    elif state == "mode_select":
        _render_mode_select()
    elif state == "generating":
        _render_generating()
    elif state == "listening":
        _render_listening()
    elif state == "pause_screen_1":
        _render_pause_screen(after_section=1)
    elif state == "structure":
        _render_structure()
    elif state == "pause_screen_2":
        _render_pause_screen(after_section=2)
    elif state == "reading":
        _render_reading()
    elif state == "scoring":
        _render_scoring()
    elif state == "completed":
        _render_completed()
    else:
        st.error(f"State tidak dikenal: {state}")
        if st.button("Reset"):
            _reset()
            st.rerun()
