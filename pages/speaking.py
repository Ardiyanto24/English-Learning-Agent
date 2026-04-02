"""
pages/speaking.py
------------------
Halaman UI Speaking Agent.

State machine:
  "init"        → Pilih sub-mode
  "generating"  → Generator buat prompt pembuka
  "session"     → Loop conversation (berbeda per sub-mode)
  "evaluating"  → Evaluator nilai full transcript
  "completed"   → Tampilkan skor dan feedback

Sub-mode loops:
  prompted_response   : max 3 exchange, assessor bisa stop lebih awal
  conversation_practice: fase 1 (<10) + fase 2 (10-15), force stop di 15
  oral_presentation   : timer 3 menit, user rekam, langsung evaluasi

Gap 3 dari audit diselesaikan di sini:
  _run_conversation_turn() mengkoordinasi Assessor → Follow-up
  dalam satu fungsi terpadu sebelum hasilnya diserahkan ke UI.
"""

import json
import time

import streamlit as st

from agents.speaking.generator import run_generator
from agents.speaking.assessor import run_assessor
from agents.speaking.follow_up import run_follow_up
from agents.speaking.evaluator import run_evaluator
from agents.speaking.analytics import run_analytics
from modules.speaking.audio_pipeline import speaking_turn, user_response
from modules.audio.stt import transcribe_audio_bytes
from modules.audio.recorder import record_audio_streaming
from database.repositories.session_repository import (
    create_session,
    update_session_status,
)
from database.repositories.speaking_repository import (
    save_speaking_session,
    save_exchange,
    update_exchange_transcript,
    update_speaking_scores,
    rebuild_transcript_from_db,
)
from utils.helpers import generate_session_id

# Konstanta
ORAL_PRESENTATION_MAX_SECONDS = 180  # 3 menit


# ===================================================
# State helpers
# ===================================================
def _get(key, default=None):
    return st.session_state.get(f"sp_{key}", default)


def _set(key, value):
    st.session_state[f"sp_{key}"] = value


def _reset():
    keys = [k for k in st.session_state if k.startswith("sp_")]
    for k in keys:
        del st.session_state[k]


# ===================================================
# Gap 3 Fix: Koordinasi Assessor → Follow-up
# ===================================================
def _run_conversation_turn(
    latest_transcript: str,
    sub_mode: str,
    exchange_count: int,
) -> dict:
    """
    Koord inasi satu putaran conversation:
      1. Panggil Assessor dengan sliding window
      2. Jika 'new_subtopic' → panggil Follow-up untuk buat pertanyaan baru
      3. Return satu dict terpadu untuk dikonsumsi UI

    Returns:
        {
          "decision"        : "continue" | "stop" | "new_subtopic",
          "next_prompt"     : str | None,  ← prompt selanjutnya jika continue/new_subtopic
          "reason"          : str,
        }
    """
    full_history = _get("full_history", [])
    main_topic = _get("main_topic", "")
    previous_angles = _get("previous_angles", [])

    # Panggil Assessor
    assessment = run_assessor(
        sub_mode=sub_mode,
        exchange_count=exchange_count,
        full_history=full_history,
        main_topic=main_topic,
        latest_transcript=latest_transcript,
    )

    decision = assessment.get("decision", "continue")

    if decision == "stop":
        return {"decision": "stop", "next_prompt": None, "reason": assessment.get("reason", "")}

    # continue atau new_subtopic → butuh next_prompt
    if decision == "new_subtopic":
        followup = run_follow_up(
            main_topic=main_topic,
            latest_user_text=latest_transcript,
            assessor_suggestion=assessment.get("suggested_followup"),
            previous_angles=previous_angles,
            session_id=_get("session_id"),
        )
        next_prompt = followup.get("follow_up_prompt", "")
        # Catat angle baru agar tidak diulang
        new_angle = followup.get("new_angle", "")
        if new_angle:
            previous_angles.append(new_angle)
            _set("previous_angles", previous_angles)
    else:
        # continue — gunakan suggested_followup dari assessor jika ada
        next_prompt = assessment.get("suggested_followup") or ("Could you elaborate more on that point?")

    return {"decision": decision, "next_prompt": next_prompt, "reason": assessment.get("reason", "")}


# ===================================================
# Flow: selesaikan sesi dan evaluasi
# ===================================================
def _complete_and_evaluate():
    """Panggil evaluator, simpan skor ke DB, pindah state ke evaluating."""
    _set("page_state", "evaluating")

    full_history = _get("full_history", [])
    sub_mode = _get("sub_mode", "")
    main_topic = _get("main_topic", "")
    prompt_text = _get("prompt_text", "")
    session_id = _get("session_id")

    with st.spinner("📊 Mengevaluasi performa kamu..."):
        evaluation = run_evaluator(
            sub_mode=sub_mode,
            main_topic=main_topic,
            prompt_text=prompt_text,
            full_transcript=full_history,
            session_id=session_id,
        )

    # Simpan skor ke DB
    user_turns = [t for t in full_history if t.get("role") == "user"]
    update_speaking_scores(
        session_id=session_id,
        total_exchanges=len(user_turns),
        full_transcript=json.dumps(full_history, ensure_ascii=False),
        grammar_score=evaluation.get("grammar_score", 0),
        relevance_score=evaluation.get("relevance_score", 0),
        final_score=evaluation.get("final_score", 0),
        vocabulary_score=evaluation.get("vocabulary_score"),
        structure_score=evaluation.get("structure_score"),
        is_graded=evaluation.get("is_graded", False),
    )
    update_session_status(session_id, status="completed")

    _set("evaluation", evaluation)
    _set("page_state", "completed")

    # Analytics (tidak block UI jika gagal)
    try:
        analytics = run_analytics()
        _set("analytics", analytics)
    except Exception:
        pass

    st.rerun()


# ===================================================
# Render: summary skor
# ===================================================
def _render_summary():
    evaluation = _get("evaluation", {})
    sub_mode = _get("sub_mode", "")
    main_topic = _get("main_topic", "")
    analytics = _get("analytics")

    st.markdown("## 🎉 Sesi Selesai!")
    st.caption(f"Topik: **{main_topic}** | Mode: *{sub_mode.replace('_', ' ').title()}*")

    is_graded = evaluation.get("is_graded", False)

    if not is_graded:
        st.warning("⚠️ Sesi ini tidak bisa dinilai karena kendala teknis. Transcript tetap tersimpan.")
    else:
        # Tampilkan skor
        def _fmt(val) -> str:
            return f"{val:.1f}/10" if val is not None else "N/A"

        if sub_mode == "oral_presentation":
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Grammar", _fmt(evaluation.get("grammar_score")))
            c2.metric("Relevance", _fmt(evaluation.get("relevance_score")))
            c3.metric("Vocabulary", _fmt(evaluation.get("vocabulary_score")))
            c4.metric("Structure", _fmt(evaluation.get("structure_score")))
            c5.metric("Final", _fmt(evaluation.get("final_score")))
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Grammar", _fmt(evaluation.get("grammar_score")))
            c2.metric("Relevance", _fmt(evaluation.get("relevance_score")))
            c3.metric("Final", _fmt(evaluation.get("final_score")))

        # Feedback per kriteria
        feedback = evaluation.get("feedback", {})
        st.markdown("---")
        st.markdown("### 💬 Feedback")

        with st.expander("Grammar", expanded=True):
            st.write(feedback.get("grammar", "-"))
        with st.expander("Relevance"):
            st.write(feedback.get("relevance", "-"))
        if sub_mode == "oral_presentation":
            with st.expander("Vocabulary"):
                st.write(feedback.get("vocabulary", "-"))
            with st.expander("Structure"):
                st.write(feedback.get("structure", "-"))

        st.info(f"**Overall:** {feedback.get('overall', '-')}")

    # Analytics insight
    if analytics and analytics.get("insight"):
        st.markdown("---")
        st.markdown("### 💡 Insight dari AI Coach")
        st.info(analytics["insight"])

    st.markdown("---")
    if st.button("🔄 Sesi Baru", type="primary", key="sp_new_btn"):
        _reset()
        st.rerun()


# ===================================================
# Render: satu exchange (dipakai prompted + conversation)
# ===================================================
def _render_exchange(exchange_index: int, is_followup: bool = False):
    """
    Render satu putaran tanya-jawab:
    1. Tampilkan prompt AI (TTS + teks)
    2. Terima jawaban user (rekam / teks)
    3. Simpan ke DB dan session state
    4. Return transcript jika selesai, None jika masih menunggu
    """
    full_history = _get("full_history", [])
    session_id = _get("session_id")
    pending_prompt = _get("pending_prompt", "")

    # Tampilkan prompt AI (sudah disimpan di session state)
    if pending_prompt:
        speaking_turn(pending_prompt)

    # Terima jawaban user
    transcript = user_response(
        exchange_index=exchange_index,
        record_seconds=90,
    )

    if not transcript:
        return None  # Masih menunggu input user

    # Ada transcript → simpan ke DB dan history
    exchange_id = save_exchange(
        session_id=session_id,
        exchange_number=exchange_index + 1,
        agent_prompt=pending_prompt,
        user_transcript=transcript,
        is_followup=is_followup,
        assessor_decision=None,  # Diupdate setelah assessor jalan
    )

    # Update full_history di session state
    full_history.append({"role": "user", "text": transcript})
    _set("full_history", full_history)
    _set("last_exchange_id", exchange_id)

    return transcript


# ===================================================
# STATE: generating — buat prompt pembuka
# ===================================================
def _state_generating():
    sub_mode = _get("sub_mode")
    difficulty = _get("difficulty", "medium")

    with st.spinner("🧠 Menyiapkan topik..."):
        try:
            result = run_generator(
                sub_mode=sub_mode,
                difficulty=difficulty,
            )
        except RuntimeError as e:
            st.error(f"😔 Gagal membuat topik: {e}")
            _set("page_state", "init")
            st.rerun()
            return

    main_topic = result.get("topic", "General")
    prompt_text = result.get("prompt_text", "")
    category = result.get("category", "")

    # Buat session di DB
    session_id = generate_session_id()
    create_session(session_id, mode="speaking")
    save_speaking_session(
        session_id=session_id,
        sub_mode=sub_mode,
        topic=main_topic,
        category=category,
    )

    # Simpan ke session state
    _set("session_id", session_id)
    _set("main_topic", main_topic)
    _set("prompt_text", prompt_text)
    _set("pending_prompt", prompt_text)
    _set("full_history", [{"role": "ai", "text": prompt_text}])
    _set("exchange_count", 0)
    _set("previous_angles", [])

    if sub_mode == "oral_presentation":
        _set("page_state", "oral_timer")
    else:
        _set("page_state", "session")

    st.rerun()


# ===================================================
# STATE: session — Prompted Response & Conversation
# ===================================================
def _state_session():
    sub_mode = _get("sub_mode")
    exchange_count = _get("exchange_count", 0)
    main_topic = _get("main_topic", "")

    # Header
    st.markdown(f"### 💬 {main_topic}")
    mode_label = {
        "prompted_response": "Prompted Response",
        "conversation_practice": "Conversation Practice",
    }.get(sub_mode, sub_mode)
    st.caption(f"Mode: *{mode_label}* | Exchange: {exchange_count}")
    st.markdown("---")

    # Render exchange saat ini
    transcript = _render_exchange(
        exchange_index=exchange_count,
        is_followup=exchange_count > 0,
    )

    if not transcript:
        return  # Masih menunggu user

    # Ada transcript → jalankan conversation turn
    new_exchange_count = exchange_count + 1
    _set("exchange_count", new_exchange_count)

    with st.spinner("🤔 Mengevaluasi jawaban..."):
        turn_result = _run_conversation_turn(
            latest_transcript=transcript,
            sub_mode=sub_mode,
            exchange_count=new_exchange_count,
        )

    # Update assessor_decision di DB
    exchange_id = _get("last_exchange_id")
    if exchange_id:
        update_exchange_transcript(
            exchange_id=exchange_id,
            user_transcript=transcript,
            assessor_decision=turn_result["decision"],
        )

    decision = turn_result["decision"]

    if decision == "stop":
        _complete_and_evaluate()
    else:
        # Siapkan prompt berikutnya
        next_prompt = turn_result.get("next_prompt", "")
        full_history = _get("full_history", [])
        full_history.append({"role": "ai", "text": next_prompt})
        _set("full_history", full_history)
        _set("pending_prompt", next_prompt)
        st.rerun()


# ===================================================
# STATE: oral_timer — Oral Presentation
# ===================================================
def _state_oral_timer():
    main_topic = _get("main_topic", "")
    prompt_text = _get("prompt_text", "")

    st.markdown(f"### 🎤 {main_topic}")
    st.markdown("---")

    # Tampilkan topik
    st.info(f"**Topik presentasi kamu:**\n\n{prompt_text}")
    st.markdown("Presentasikan pendapatmu selama **maksimal 3 menit**. " "Kamu bisa berhenti kapan saja atau tunggu timer habis.")

    # Timer logic
    start_key = "sp_oral_start_time"
    recording_key = "sp_oral_recording"

    # Tombol mulai rekam
    if not st.session_state.get(recording_key):
        if st.button("🔴 Mulai Presentasi", type="primary", key="sp_oral_start"):
            st.session_state[start_key] = time.time()
            st.session_state[recording_key] = True
            st.rerun()
        return

    # Sedang merekam — hitung sisa waktu
    elapsed = time.time() - st.session_state.get(start_key, time.time())
    remaining = max(0, ORAL_PRESENTATION_MAX_SECONDS - elapsed)
    pct = elapsed / ORAL_PRESENTATION_MAX_SECONDS

    # Tampilkan timer visual
    mins = int(remaining // 60)
    secs = int(remaining % 60)
    timer_color = "🟢" if remaining > 60 else ("🟡" if remaining > 20 else "🔴")
    st.markdown(f"### {timer_color} Sisa waktu: {mins:02d}:{secs:02d}")
    st.progress(min(pct, 1.0))

    col1, col2 = st.columns([1, 3])
    with col1:
        stop_manual = st.button(
            "⏹️ Selesai",
            type="primary",
            key="sp_oral_stop",
            use_container_width=True,
        )

    # Auto-stop jika waktu habis
    auto_stop = remaining <= 0

    if stop_manual or auto_stop:
        if auto_stop:
            st.warning("⏰ Waktu habis! Memproses rekaman...")
        else:
            st.success("✅ Presentasi selesai!")

        _process_oral_recording(elapsed)
        return

    # Refresh otomatis setiap detik untuk update timer
    time.sleep(1)
    st.rerun()


def _process_oral_recording(duration_seconds: float):
    """
    Rekam presentasi oral dengan durasi yang sudah ditentukan,
    lalu kirim ke STT dan evaluator.
    """
    record_secs = min(int(duration_seconds) + 2, ORAL_PRESENTATION_MAX_SECONDS)

    # Rekam ulang (user sudah bicara selama timer berjalan)
    # Di production, idealnya rekaman sudah berjalan sejak tombol "Mulai"
    # Untuk simplisitas: rekam setelah user klik "Selesai"
    with st.spinner("🔄 Memproses presentasi..."):
        audio_path = record_audio_streaming(
            max_duration_seconds=record_secs,
        )

    if not audio_path:
        st.error("❌ Rekaman gagal. Beralih ke input teks.")
        _set("page_state", "oral_text_fallback")
        st.rerun()
        return

    with st.spinner("📝 Mentranskrip..."):
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            transcript = transcribe_audio_bytes(audio_bytes, "presentation.wav")
        except Exception:
            transcript = None

    if not transcript:
        st.warning("Transkrip gagal. Silakan ketik ringkasan presentasimu.")
        _set("page_state", "oral_text_fallback")
        st.rerun()
        return

    # Simpan ke DB dan history
    session_id = _get("session_id")
    prompt_text = _get("prompt_text", "")
    full_history = _get("full_history", [])
    full_history.append({"role": "user", "text": transcript})

    save_exchange(
        session_id=session_id,
        exchange_number=1,
        agent_prompt=prompt_text,
        user_transcript=transcript,
        is_followup=False,
    )

    _set("full_history", full_history)
    _complete_and_evaluate()


# ===================================================
# STATE: oral_text_fallback
# ===================================================
def _state_oral_text_fallback():
    st.info("⌨️ Ketik ringkasan presentasimu di bawah.")

    text = st.text_area(
        "Presentasi kamu:",
        placeholder="Ketik apa yang ingin kamu sampaikan...",
        height=200,
        key="sp_oral_text_input",
    )

    if st.button("Submit ✓", type="primary", disabled=not text, key="sp_oral_text_submit"):
        session_id = _get("session_id")
        prompt_text = _get("prompt_text", "")
        full_history = _get("full_history", [])
        full_history.append({"role": "user", "text": text.strip()})

        save_exchange(
            session_id=session_id,
            exchange_number=1,
            agent_prompt=prompt_text,
            user_transcript=text.strip(),
        )
        _set("full_history", full_history)
        _complete_and_evaluate()


# ===================================================
# STATE: init — pilih sub-mode
# ===================================================
def _state_init():
    st.markdown("### Pilih Mode Latihan")

    sub_mode = st.radio(
        "Sub-mode:",
        options=[
            "prompted_response",
            "conversation_practice",
            "oral_presentation",
        ],
        format_func=lambda x: {
            "prompted_response": "🗣️ Prompted Response — Jawab 1 pertanyaan (max 3 exchange)",
            "conversation_practice": "💬 Conversation Practice — Dialog natural (10-15 exchange)",
            "oral_presentation": "🎤 Oral Presentation — Presentasi 3 menit",
        }.get(x, x),
        key="sp_mode_radio",
    )

    difficulty = st.select_slider(
        "Tingkat kesulitan:",
        options=["easy", "medium", "hard"],
        value="medium",
        key="sp_diff_slider",
    )

    st.markdown("---")

    # Penjelasan singkat per mode
    desc = {
        "prompted_response": ("AI akan memberikan satu pertanyaan. Jawab dengan lengkap. " "AI akan bertanya lanjutan maksimal 3 kali."),
        "conversation_practice": (
            "Percakapan natural dengan AI. Fase 1 (< 10 exchange): " "AI akan menjaga conversation tetap hidup. " "Fase 2 (10-15 exchange): percakapan bisa berakhir secara natural."
        ),
        "oral_presentation": ("AI berikan topik, kamu presentasikan selama maksimal 3 menit. " "Dinilai dari grammar, relevance, vocabulary, dan struktur."),
    }.get(sub_mode, "")
    st.caption(desc)

    if st.button("🚀 Mulai Sesi", type="primary", key="sp_start_btn"):
        _set("sub_mode", sub_mode)
        _set("difficulty", difficulty)
        _set("page_state", "generating")
        st.rerun()


# ===================================================
# Recovery: browser refresh
# ===================================================
def _try_recover_session():
    """
    Jika ada session_id di state tapi full_history hilang,
    coba rebuild dari DB.
    """
    session_id = _get("session_id")
    full_history = _get("full_history")

    if not session_id or full_history is not None:
        return  # Tidak perlu recovery

    recovered = rebuild_transcript_from_db(session_id)
    if not recovered or not recovered.get("is_recoverable"):
        st.warning("⚠️ Sesi tidak bisa dilanjutkan setelah refresh. " "Silakan mulai sesi baru.")
        _reset()
        st.rerun()
        return

    # Restore session state dari DB
    _set("full_history", recovered["full_history"])
    _set("exchange_count", recovered["exchange_count"])
    _set("previous_angles", recovered["previous_angles"])
    _set("main_topic", recovered["main_topic"])
    _set("prompt_text", recovered["prompt_text"])
    st.info("🔄 Sesi dipulihkan dari penyimpanan.")


# ===================================================
# Main
# ===================================================
def main():
    st.title("🗣️ Speaking Agent")
    st.caption("Latihan berbicara bahasa Inggris dengan AI conversation partner")

    page_state = _get("page_state", "init")

    # Recovery check setiap load
    _try_recover_session()

    if page_state == "init":
        _state_init()

    elif page_state == "generating":
        st.info("⏳ Menyiapkan sesi...")
        _state_generating()

    elif page_state == "session":
        _state_session()

    elif page_state == "oral_timer":
        _state_oral_timer()

    elif page_state == "oral_text_fallback":
        _state_oral_text_fallback()

    elif page_state == "evaluating":
        st.info("📊 Sedang mengevaluasi...")

    elif page_state == "completed":
        _render_summary()

    # Tombol keluar tersedia di semua state kecuali init dan completed
    if page_state not in ("init", "completed", "generating", "evaluating"):
        st.markdown("---")
        if st.button("❌ Keluar", type="secondary", key="sp_exit_btn"):
            session_id = _get("session_id")
            if session_id:
                update_session_status(session_id, status="abandoned")
            _reset()
            st.rerun()


if __name__ == "__main__":
    main()
