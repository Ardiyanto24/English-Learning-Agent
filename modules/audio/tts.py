"""
modules/audio/tts.py
--------------------
Text-to-Speech menggunakan OpenAI TTS API.

Digunakan oleh:
- Speaking Agent  : untuk suara agent saat conversation
- TOEFL Listening : untuk generate audio dialog/monolog

Voice mapping (sesuai spesifikasi Part 5):
- alloy : SPEAKER_A (suara netral)
- nova  : SPEAKER_B (suara feminine)
- onyx  : NARRATOR  (suara maskulin, berwibawa)

Retry policy: max 1x — kalau 2x gagal, kemungkinan ada masalah
koneksi serius. UI fallback ke tampilkan teks saja.
"""

import os
import time
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Valid voices sesuai OpenAI TTS API
VALID_VOICES = {"alloy", "nova", "onyx", "echo", "fable", "shimmer"}
DEFAULT_VOICE = "alloy"
TTS_MODEL = "tts-1"  # tts-1 untuk speed, tts-1-hd untuk kualitas lebih tinggi

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """Lazy-load OpenAI client (singleton)."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def generate_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
) -> Optional[bytes]:
    """
    Generate audio dari teks menggunakan OpenAI TTS.

    Args:
        text  : Teks yang akan diconvert ke audio
        voice : 'alloy' | 'nova' | 'onyx' (dan lainnya)

    Returns:
        Audio bytes (MP3 format) jika berhasil, None jika gagal.

    Penggunaan di UI Streamlit:
        audio_bytes = generate_speech("Hello, how are you?", voice="nova")
        if audio_bytes:
            st.audio(audio_bytes, format="audio/mp3")
        else:
            st.write("Hello, how are you?")  # fallback teks
    """
    if not text or not text.strip():
        return None

    # Validasi voice, fallback ke default jika tidak valid
    if voice not in VALID_VOICES:
        voice = DEFAULT_VOICE

    client = _get_client()
    last_error = None

    # Retry max 1x (total 2 attempt)
    for attempt in range(2):
        try:
            response = client.audio.speech.create(
                model=TTS_MODEL,
                voice=voice,
                input=text.strip(),
            )
            # response.content adalah audio bytes langsung
            return response.content

        except Exception as e:
            last_error = e
            if attempt == 0:
                # Tunggu sebentar sebelum retry
                time.sleep(1)

    # Kedua attempt gagal
    print(f"[TTS] Failed after 2 attempts: {last_error}")
    return None


def generate_speech_multivoice(script: str) -> Optional[bytes]:
    """
    Generate audio dari script multi-speaker (untuk TOEFL Listening).

    Script menggunakan tag speaker:
        [SPEAKER_A]: Teks dari speaker A
        [SPEAKER_B]: Teks dari speaker B
        [NARRATOR]: Teks dari narrator

    Setiap bagian di-generate terpisah lalu digabung.

    Args:
        script: String dengan tag [SPEAKER_X]: teks

    Returns:
        Gabungan audio bytes, atau None jika gagal
    """
    import re

    # Voice mapping per tag
    voice_map = {
        "SPEAKER_A": "alloy",
        "SPEAKER_B": "nova",
        "NARRATOR": "onyx",
    }

    # Parse script menjadi list (speaker, teks)
    pattern = r'\[(SPEAKER_A|SPEAKER_B|NARRATOR)\]:\s*(.+?)(?=\[(?:SPEAKER_A|SPEAKER_B|NARRATOR)\]:|$)'
    matches = re.findall(pattern, script, re.DOTALL)

    if not matches:
        # Tidak ada tag — generate sebagai satu suara saja
        return generate_speech(script, voice=DEFAULT_VOICE)

    # Generate audio per bagian
    audio_parts = []
    for speaker_tag, text in matches:
        voice = voice_map.get(speaker_tag, DEFAULT_VOICE)
        text = text.strip()
        if not text:
            continue

        audio_bytes = generate_speech(text, voice=voice)
        if audio_bytes is None:
            # Satu bagian gagal — return None agar UI fallback ke teks
            return None
        audio_parts.append(audio_bytes)

    if not audio_parts:
        return None

    # Gabungkan semua audio bytes
    return b"".join(audio_parts)