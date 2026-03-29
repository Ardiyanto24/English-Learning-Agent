"""
modules/audio/stt.py
--------------------
Speech-to-Text menggunakan Google Cloud Speech-to-Text API.

Digunakan oleh:
- Speaking Agent : untuk transkrip jawaban user

Fallback: return None setelah gagal → UI tampilkan text input manual.
"""

import time
from pathlib import Path
from typing import Optional, Union

from dotenv import load_dotenv

load_dotenv()

SUPPORTED_FORMATS = {".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg", ".flac"}

_client = None


def _get_client():
    """Lazy-load Google STT client (singleton)."""
    global _client
    if _client is None:
        from google.cloud import speech
        _client = speech.SpeechClient()
    return _client


def transcribe_audio(
    audio_file: Union[str, Path],
    language: str = "en-US",
    prompt: Optional[str] = None,
) -> Optional[str]:
    """
    Transkrip file audio menjadi teks menggunakan Google Cloud STT.

    Args:
        audio_file : Path ke file audio (WAV, MP3, dll)
        language   : BCP-47 language code (default: "en-US")
        prompt     : Tidak digunakan di Google STT, diabaikan

    Returns:
        String transkrip jika berhasil, None jika gagal.
    """
    audio_path = Path(audio_file)

    if not audio_path.exists():
        print(f"[STT] File tidak ditemukan: {audio_path}")
        return None

    if audio_path.suffix.lower() not in SUPPORTED_FORMATS:
        print(f"[STT] Format tidak didukung: {audio_path.suffix}")
        return None

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    return transcribe_audio_bytes(audio_bytes, audio_path.name, language)


def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    language: str = "en-US",
) -> Optional[str]:
    """
    Transkrip audio dari bytes langsung menggunakan Google Cloud STT.

    Args:
        audio_bytes : Raw audio bytes
        filename    : Nama file (untuk deteksi encoding)
        language    : BCP-47 language code
    """
    from google.cloud import speech

    last_error = None

    for attempt in range(3):
        try:
            client = _get_client()

            audio = speech.RecognitionAudio(content=audio_bytes)
            config = speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code=language,
                enable_automatic_punctuation=True,
            )

            response = client.recognize(config=config, audio=audio)

            if not response.results:
                last_error = "empty_transcript"
                time.sleep(2 ** attempt)
                continue

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
            wait_time = 2 ** attempt
            print(f"[STT] Attempt {attempt + 1} gagal: {e}. "
                  f"Retry dalam {wait_time}s...")
            if attempt < 2:
                time.sleep(wait_time)

    print(f"[STT] Gagal setelah 3 attempt. Error terakhir: {last_error}")
    return None
