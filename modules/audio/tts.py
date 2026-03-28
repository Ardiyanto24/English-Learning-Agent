"""
modules/audio/tts.py
--------------------
Text-to-Speech menggunakan Google Cloud Text-to-Speech API.

Menggantikan OpenAI TTS. Interface publik tetap sama —
generate_speech() dan generate_speech_multivoice() —
sehingga pages/speaking.py dan pages/toefl.py tidak perlu diubah.

Voice mapping:
- SPEAKER_A → en-US-Neural2-D  (netral)
- SPEAKER_B → en-US-Neural2-F  (feminin)
- NARRATOR  → en-US-Neural2-J  (maskulin)

Output: MP3 bytes — sama dengan sebelumnya.
"""

import re
import time
from typing import Optional

from google.cloud import texttospeech
from dotenv import load_dotenv

load_dotenv()

VOICE_MAP = {
    "SPEAKER_A": "en-US-Neural2-D",
    "SPEAKER_B": "en-US-Neural2-F",
    "NARRATOR": "en-US-Neural2-J",
}
DEFAULT_VOICE = "en-US-Neural2-D"

# Mapping voice lama OpenAI → Google Neural2
_LEGACY_VOICE_MAP = {
    "alloy": "en-US-Neural2-D",
    "nova": "en-US-Neural2-F",
    "onyx": "en-US-Neural2-J",
}

_client: Optional[texttospeech.TextToSpeechClient] = None


def _get_client() -> texttospeech.TextToSpeechClient:
    global _client
    if _client is None:
        _client = texttospeech.TextToSpeechClient()
    return _client


def generate_speech(
    text: str,
    voice: str = DEFAULT_VOICE,
) -> Optional[bytes]:
    if not text or not text.strip():
        return None

    # Remap voice lama OpenAI kalau masih dipakai
    voice = _LEGACY_VOICE_MAP.get(voice, voice)

    synthesis_input = texttospeech.SynthesisInput(text=text.strip())
    voice_params = texttospeech.VoiceSelectionParams(
        language_code="en-US",
        name=voice,
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.MP3,
    )

    client = _get_client()
    last_error = None

    for attempt in range(2):
        try:
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

    print(f"[TTS] Gagal setelah 2 attempt: {last_error}")
    return None


def generate_speech_multivoice(script: str) -> Optional[bytes]:
    pattern = r"\[(SPEAKER_A|SPEAKER_B|NARRATOR)\]:\s*" r"(.+?)(?=\[(?:SPEAKER_A|SPEAKER_B|NARRATOR)\]:|$)"
    matches = re.findall(pattern, script, re.DOTALL)

    if not matches:
        return generate_speech(script)

    audio_parts = []
    for speaker_tag, text in matches:
        voice = VOICE_MAP.get(speaker_tag, DEFAULT_VOICE)
        text = text.strip()
        if not text:
            continue
        audio_bytes = generate_speech(text, voice=voice)
        if audio_bytes is None:
            return None
        audio_parts.append(audio_bytes)

    return b"".join(audio_parts) if audio_parts else None
