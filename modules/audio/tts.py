"""
modules/audio/tts.py
--------------------
Text-to-Speech menggunakan Google Cloud Text-to-Speech API.

Digunakan oleh:
- Speaking Agent  : untuk suara agent saat conversation
- TOEFL Listening : untuk generate audio dialog/monolog

Voice mapping:
- SPEAKER_A : en-US-Neural2-D (male)
- SPEAKER_B : en-US-Neural2-F (female)
- NARRATOR  : en-US-Neural2-J (male, authoritative)
"""

import os
import time
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

DEFAULT_VOICE  = "en-US-Neural2-D"
VALID_VOICES   = {
    "alloy"    : "en-US-Neural2-D",
    "nova"     : "en-US-Neural2-F",
    "onyx"     : "en-US-Neural2-J",
    "SPEAKER_A": "en-US-Neural2-D",
    "SPEAKER_B": "en-US-Neural2-F",
    "NARRATOR" : "en-US-Neural2-J",
}

_client = None


def _get_client():
    """Lazy-load Google TTS client (singleton)."""
    global _client
    if _client is None:
        from google.cloud import texttospeech
        _client = texttospeech.TextToSpeechClient()
    return _client


def generate_speech(
    text: str,
    voice: str = "alloy",
) -> Optional[bytes]:
    """
    Generate audio dari teks menggunakan Google Cloud TTS.

    Args:
        text  : Teks yang akan diconvert ke audio
        voice : 'alloy' | 'nova' | 'onyx' (mapping ke Google voices)

    Returns:
        Audio bytes (MP3 format) jika berhasil, None jika gagal.
    """
    from google.cloud import texttospeech

    if not text or not text.strip():
        return None

    google_voice = VALID_VOICES.get(voice, DEFAULT_VOICE)
    last_error   = None

    for attempt in range(2):
        try:
            client = _get_client()

            synthesis_input = texttospeech.SynthesisInput(text=text.strip())
            voice_params    = texttospeech.VoiceSelectionParams(
                language_code = "en-US",
                name          = google_voice,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            )

            response = client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
            return response.audio_content

        except Exception as e:
            last_error = e
            if attempt == 0:
                time.sleep(1)

    print(f"[TTS] Failed after 2 attempts: {last_error}")
    return None


def generate_speech_multivoice(script: str) -> Optional[bytes]:
    """
    Generate audio dari script multi-speaker (untuk TOEFL Listening).

    Script menggunakan tag speaker:
        [SPEAKER_A]: Teks dari speaker A
        [SPEAKER_B]: Teks dari speaker B
        [NARRATOR]: Teks dari narrator
    """
    import re

    voice_map = {
        "SPEAKER_A": "alloy",
        "SPEAKER_B": "nova",
        "NARRATOR" : "onyx",
    }

    pattern = (
        r'\[(SPEAKER_A|SPEAKER_B|NARRATOR)\]:\s*'
        r'(.+?)(?=\[(?:SPEAKER_A|SPEAKER_B|NARRATOR)\]:|$)'
    )
    matches = re.findall(pattern, script, re.DOTALL)

    if not matches:
        return generate_speech(script, voice="alloy")

    audio_parts = []
    for speaker_tag, text in matches:
        voice = voice_map.get(speaker_tag, "alloy")
        text  = text.strip()
        if not text:
            continue

        audio_bytes = generate_speech(text, voice=voice)
        if audio_bytes is None:
            return None
        audio_parts.append(audio_bytes)

    if not audio_parts:
        return None

    return b"".join(audio_parts)
