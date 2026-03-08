"""
modules/audio/stt.py
--------------------
Speech-to-Text menggunakan OpenAI Whisper API.

Digunakan oleh:
- Speaking Agent : untuk transkrip jawaban user

Retry policy: max 3x dengan exponential backoff (1s → 2s → 4s).
Audio upload lebih rentan gagal di tengah jalan karena ukuran file,
sehingga retry lebih banyak dari TTS.

Fallback: return None setelah 3x gagal → UI tampilkan text input manual.
"""

import os
import time
from pathlib import Path
from typing import Optional, Union

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

WHISPER_MODEL = "whisper-1"

# Format audio yang didukung Whisper API
SUPPORTED_FORMATS = {".wav", ".mp3", ".mp4", ".m4a", ".webm", ".ogg", ".flac"}

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """Lazy-load OpenAI client (singleton)."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client


def transcribe_audio(
    audio_file: Union[str, Path],
    language: str = "en",
    prompt: Optional[str] = None,
) -> Optional[str]:
    """
    Transkrip file audio menjadi teks menggunakan Whisper.

    Args:
        audio_file : Path ke file audio (WAV, MP3, dll)
        language   : Kode bahasa ISO 639-1 (default: "en" untuk English)
        prompt     : Hint untuk Whisper agar hasil lebih akurat.
                     Contoh: "TOEFL speaking response about campus life"

    Returns:
        String transkrip jika berhasil, None jika gagal setelah 3x retry.

    Penggunaan:
        transcript = transcribe_audio("temp_audio/response.wav")
        if transcript:
            # proses transcript
        else:
            # tampilkan text input manual di UI
    """
    audio_path = Path(audio_file)

    # Validasi file ada
    if not audio_path.exists():
        print(f"[STT] File tidak ditemukan: {audio_path}")
        return None

    # Validasi format
    if audio_path.suffix.lower() not in SUPPORTED_FORMATS:
        print(f"[STT] Format tidak didukung: {audio_path.suffix}")
        return None

    # Validasi ukuran file (Whisper max 25MB)
    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
    if file_size_mb > 25:
        print(f"[STT] File terlalu besar: {file_size_mb:.1f}MB (max 25MB)")
        return None

    client = _get_client()
    last_error = None

    # Retry max 3x dengan exponential backoff: 1s → 2s → 4s
    for attempt in range(3):
        try:
            with open(audio_path, "rb") as f:
                response = client.audio.transcriptions.create(
                    model=WHISPER_MODEL,
                    file=f,
                    language=language,
                    prompt=prompt,
                )
            transcript = response.text.strip()

            # Validasi hasil tidak kosong
            if not transcript:
                print(f"[STT] Attempt {attempt+1}: transkrip kosong")
                last_error = "empty_transcript"
                time.sleep(2 ** attempt)
                continue

            return transcript

        except Exception as e:
            last_error = e
            wait_time = 2 ** attempt  # 1s, 2s, 4s
            print(f"[STT] Attempt {attempt+1} gagal: {e}. "
                  f"Retry dalam {wait_time}s...")
            if attempt < 2:
                time.sleep(wait_time)

    print(f"[STT] Gagal setelah 3 attempt. Error terakhir: {last_error}")
    return None


def transcribe_audio_bytes(
    audio_bytes: bytes,
    filename: str = "audio.wav",
    language: str = "en",
) -> Optional[str]:
    """
    Transkrip audio dari bytes langsung (tanpa file di disk).
    Berguna untuk Streamlit file_uploader atau audio bytes dari recorder.

    Args:
        audio_bytes : Raw audio bytes
        filename    : Nama file virtual (menentukan format yang diparse Whisper)
        language    : Kode bahasa ISO 639-1
    """
    import io

    client = _get_client()
    last_error = None

    for attempt in range(3):
        try:
            audio_io = io.BytesIO(audio_bytes)
            audio_io.name = filename  # Whisper butuh nama file untuk deteksi format

            response = client.audio.transcriptions.create(
                model=WHISPER_MODEL,
                file=audio_io,
                language=language,
            )
            transcript = response.text.strip()

            if not transcript:
                last_error = "empty_transcript"
                time.sleep(2 ** attempt)
                continue

            return transcript

        except Exception as e:
            last_error = e
            wait_time = 2 ** attempt
            print(f"[STT] Bytes attempt {attempt+1} gagal: {e}. "
                  f"Retry dalam {wait_time}s...")
            if attempt < 2:
                time.sleep(wait_time)

    print(f"[STT] Gagal setelah 3 attempt: {last_error}")
    return None