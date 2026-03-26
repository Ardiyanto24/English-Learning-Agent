"""
modules/audio/stt.py
--------------------
Speech-to-Text menggunakan Google Cloud Speech-to-Text API.

Menggantikan OpenAI Whisper. Interface publik tetap sama —
transcribe_audio() dan transcribe_audio_bytes() — sehingga
pages/speaking.py dan pages/toefl.py tidak perlu diubah.

Retry policy: max 3x dengan exponential backoff (1s → 2s → 4s).
Fallback: return None → UI tampilkan text input manual.
"""

import os
import time
from pathlib import Path
from typing import Optional, Union

from google.cloud import speech
from dotenv import load_dotenv

load_dotenv()

LANGUAGE_CODE  = "en-US"
SAMPLE_RATE_HZ = 16000

_client: Optional[speech.SpeechClient] = None


def _get_client() -> speech.SpeechClient:
    global _client
    if _client is None:
        _client = speech.SpeechClient()
    return _client


def transcribe_audio(
    audio_file: Union[str, Path],
    language: str = "en",
    prompt: Optional[str] = None,
) -> Optional[str]:
    audio_path = Path(audio_file)

    if not audio_path.exists():
        print(f"[STT] File tidak ditemukan: {audio_path}")
        return None

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    return transcribe_audio_bytes(
        audio_bytes,
        filename=audio_path.name,
        language=language,
    )


def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    language: str = "en",
) -> Optional[str]:
    lang_code = f"{language}-US" if len(language) == 2 else language

    ext = Path(filename).suffix.lower()
    encoding_map = {
        ".wav":  speech.RecognitionConfig.AudioEncoding.LINEAR16,
        ".flac": speech.RecognitionConfig.AudioEncoding.FLAC,
        ".mp3":  speech.RecognitionConfig.AudioEncoding.MP3,
        ".ogg":  speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
        ".webm": speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
    }
    encoding = encoding_map.get(
        ext,
        speech.RecognitionConfig.AudioEncoding.LINEAR16,
    )

    config = speech.RecognitionConfig(
        encoding=encoding,
        sample_rate_hertz=SAMPLE_RATE_HZ,
        language_code=lang_code,
        enable_automatic_punctuation=True,
    )
    audio  = speech.RecognitionAudio(content=audio_bytes)
    client = _get_client()
    last_error = None

    for attempt in range(3):
        try:
            response   = client.recognize(config=config, audio=audio)
            transcript = " ".join(
                result.alternatives[0].transcript
                for result in response.results
                if result.alternatives
            ).strip()

            if not transcript:
                last_error = "empty_transcript"
                time.sleep(2 ** attempt)
                continue

            return transcript

        except Exception as e:
            last_error = e
            wait = 2 ** attempt
            print(f"[STT] Attempt {attempt+1} gagal: {e}. "
                  f"Retry dalam {wait}s...")
            if attempt < 2:
                time.sleep(wait)

    print(f"[STT] Gagal setelah 3 attempt: {last_error}")
    return None