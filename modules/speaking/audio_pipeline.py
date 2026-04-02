"""
modules/speaking/audio_pipeline.py
------------------------------------
Layer integrasi audio untuk Speaking Agent.

Memisahkan urusan audio (TTS, recorder, STT) dari UI dan agent logic.
Dua fungsi utama yang dipakai di pages/speaking.py:

  speaking_turn(agent_prompt, st_container)
    → TTS convert prompt → putar di UI → fallback teks jika gagal

  user_response(exchange_index, st_container)
    → Rekam audio → STT transkrip → fallback text input jika gagal 3x

Kedua fungsi ini TIDAK memanggil agent — mereka hanya menangani
perpindahan antara teks ↔ audio. Logic assessor, follow-up, dan
evaluator tetap di layer agent.

Fallback design:
  TTS gagal  → tampilkan teks (sesi tetap jalan)
  STT gagal  → retry hingga 3x rekam ulang
  Setelah 3x → text input manual (sesi tetap jalan)
"""

import io
from typing import Optional

import streamlit as st

from modules.audio.tts import generate_speech
from modules.audio.stt import transcribe_audio_bytes
from modules.audio.recorder import record_audio
from utils.logger import log_error, logger

# Voice default untuk AI di speaking conversation
AI_VOICE = "nova"

# Durasi rekaman per exchange (detik)
# Cukup untuk jawaban 1-2 menit, tidak terlalu lama agar tidak membebani
DEFAULT_RECORD_SECONDS = 90


# ===================================================
# speaking_turn() — AI berbicara
# ===================================================
def speaking_turn(
    agent_prompt: str,
    container=None,
    voice: str = AI_VOICE,
) -> bool:
    """
    Satu "giliran bicara" AI: convert prompt ke audio dan putar di UI.

    Flow:
      1. Panggil TTS → dapat audio bytes
      2. Render st.audio() di container
      3. Jika TTS gagal (return None) → fallback tampilkan teks

    Args:
        agent_prompt : Teks yang akan diucapkan AI
        container    : st.container() atau None (pakai st langsung)
        voice        : Voice TTS (default "nova")

    Returns:
        True  jika audio berhasil diputar
        False jika fallback ke teks
    """
    ctx = container if container else st

    if not agent_prompt or not agent_prompt.strip():
        return False

    logger.debug(f"[audio_pipeline] TTS: '{agent_prompt[:60]}...'")

    # Tampilkan teks prompt terlebih dahulu — selalu tampil
    # (user bisa baca sambil dengar, atau baca saja jika TTS gagal)
    ctx.markdown(f"🤖 **AI:** {agent_prompt}")

    # Coba generate audio
    audio_bytes = generate_speech(text=agent_prompt, voice=voice)

    if audio_bytes:
        # Wrap bytes ke BytesIO agar st.audio bisa membacanya
        audio_io = io.BytesIO(audio_bytes)
        ctx.audio(audio_io, format="audio/mp3", autoplay=True)
        logger.debug("[audio_pipeline] TTS success — audio played")
        return True
    else:
        # TTS gagal — teks sudah ditampilkan di atas, cukup beri notifikasi
        ctx.caption("⚠️ Audio tidak tersedia. Silakan baca teks di atas.")
        log_error(
            error_type="tts_failure",
            agent_name="audio_pipeline",
            context={"prompt_preview": agent_prompt[:60]},
            fallback_used=True,
        )
        logger.warning("[audio_pipeline] TTS failed — text fallback active")
        return False


# ===================================================
# user_response() — User berbicara
# ===================================================
def user_response(
    exchange_index: int,
    container=None,
    record_seconds: int = DEFAULT_RECORD_SECONDS,
) -> Optional[str]:
    """
    Satu "giliran bicara" user: rekam audio → STT → return transcript.

    Flow:
      1. Tampilkan tombol "Mulai Rekam"
      2. User klik → recorder.record_audio() (max 3x retry internal)
      3. Jika rekaman dapat → kirim ke STT
      4. Jika STT gagal → increment stt_fail counter → coba rekam ulang
      5. Setelah stt_fail >= 3 → tampilkan text input manual

    State management menggunakan Streamlit session state dengan key
    yang unik per exchange agar tidak bentrok antar soal.

    Args:
        exchange_index : Nomor exchange (untuk key unik di session state)
        container      : st.container() atau None
        record_seconds : Durasi rekaman (default 90 detik)

    Returns:
        str  jika transcript berhasil (via audio atau teks manual)
        None jika user belum submit apapun (masih menunggu)
    """
    ctx = container if container else st

    # Key unik per exchange untuk state
    key_mode = f"sp_mode_{exchange_index}"  # "record" | "text_fallback"
    key_stt_fail = f"sp_stt_fail_{exchange_index}"  # counter kegagalan STT
    key_answer = f"sp_answer_{exchange_index}"  # transcript final

    # Jika sudah ada jawaban dari exchange ini, return langsung
    if st.session_state.get(key_answer):
        return st.session_state[key_answer]

    mode = st.session_state.get(key_mode, "record")
    stt_fails = st.session_state.get(key_stt_fail, 0)

    # ── MODE: text_fallback ────────────────────────────────────────
    if mode == "text_fallback":
        _render_text_fallback(ctx, exchange_index, key_answer, stt_fails)
        return st.session_state.get(key_answer)

    # ── MODE: record ───────────────────────────────────────────────
    ctx.markdown("🎙️ **Giliranmu berbicara**")

    if stt_fails > 0:
        ctx.warning(f"⚠️ Percobaan {stt_fails}/3 gagal ditranskrip. " f"Coba bicara lebih keras dan jelas.")

    col1, col2 = ctx.columns([2, 3])
    with col1:
        record_btn = st.button(
            label="🔴 Mulai Rekam",
            key=f"sp_rec_btn_{exchange_index}",
            type="primary",
            use_container_width=True,
        )
    with col2:
        # Tombol skip ke text input tersedia sejak awal
        if st.button(
            label="⌨️ Ketik manual",
            key=f"sp_text_btn_{exchange_index}",
            use_container_width=True,
        ):
            st.session_state[key_mode] = "text_fallback"
            st.rerun()

    if not record_btn:
        return None

    # User klik "Mulai Rekam"
    with ctx.spinner(f"🎙️ Merekam... ({record_seconds} detik)"):
        audio_path = record_audio(duration_seconds=record_seconds)

    if not audio_path:
        # record_audio() sudah retry 3x internal → langsung fallback
        ctx.error("❌ Mikrofon tidak dapat diakses setelah 3x percobaan. " "Beralih ke input teks.")
        log_error(
            error_type="recorder_failure",
            agent_name="audio_pipeline",
            context={"exchange_index": exchange_index},
            fallback_used=True,
        )
        st.session_state[key_mode] = "text_fallback"
        st.rerun()
        return None

    # Rekaman berhasil → kirim ke STT
    with ctx.spinner("🔄 Mentranskrip..."):
        transcript = _transcribe_file(audio_path)

    if transcript:
        logger.info(f"[audio_pipeline] STT success exchange={exchange_index} " f"— '{transcript[:60]}'")
        st.session_state[key_answer] = transcript
        ctx.success("✅ Transkrip berhasil!")
        ctx.markdown(f'*"{transcript}"*')
        return transcript

    # STT gagal
    stt_fails += 1
    st.session_state[key_stt_fail] = stt_fails
    log_error(
        error_type="stt_failure",
        agent_name="audio_pipeline",
        context={"exchange_index": exchange_index, "attempt": stt_fails},
        fallback_used=stt_fails >= 3,
    )

    if stt_fails >= 3:
        ctx.error("❌ Transkrip gagal 3x. Beralih ke input teks manual.")
        st.session_state[key_mode] = "text_fallback"
    else:
        ctx.warning(f"Transkrip gagal (percobaan {stt_fails}/3). " "Klik 'Mulai Rekam' untuk coba lagi.")

    st.rerun()
    return None


# ===================================================
# Helper: render text fallback
# ===================================================
def _render_text_fallback(
    ctx,
    exchange_index: int,
    key_answer: str,
    stt_fails: int,
) -> None:
    """
    Tampilkan text input manual sebagai fallback.
    Dipanggil setelah 3x STT gagal atau user klik 'Ketik manual'.
    """
    if stt_fails >= 3:
        ctx.info("⌨️ **Mode teks aktif** — Audio tidak berhasil ditranskrip " "setelah 3x percobaan. Ketik jawabanmu di bawah.")
    else:
        ctx.info("⌨️ **Mode teks aktif** — Ketik jawabanmu di bawah.")

    text_answer = ctx.text_area(
        label="Jawaban kamu:",
        placeholder="Ketik jawabanmu dalam Bahasa Inggris...",
        height=120,
        key=f"sp_text_area_{exchange_index}",
    )

    if ctx.button(
        label="Submit ✓",
        key=f"sp_text_submit_{exchange_index}",
        type="primary",
        disabled=not text_answer,
    ):
        st.session_state[key_answer] = text_answer.strip()
        st.rerun()


# ===================================================
# Helper: transcribe audio file
# ===================================================
def _transcribe_file(audio_path: str) -> Optional[str]:
    """
    Baca file WAV dan kirim ke STT.
    Return transcript string atau None jika gagal.
    """
    try:
        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        transcript = transcribe_audio_bytes(audio_bytes, filename="recording.wav")
        return transcript if transcript and transcript.strip() else None

    except Exception as e:
        logger.error(f"[audio_pipeline] Transcribe error: {e}")
        return None
