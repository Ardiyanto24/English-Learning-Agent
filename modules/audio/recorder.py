"""
modules/audio/recorder.py
--------------------------
Perekam audio dari microphone menggunakan PyAudio.

Digunakan oleh:
- Speaking Agent : untuk rekam jawaban user

Output: file WAV dengan spec yang kompatibel dengan Whisper API:
- Sample rate : 16000 Hz (16kHz)
- Channels    : 1 (mono)
- Sample width: 2 bytes (16-bit PCM)

File disimpan di folder temp_audio/ di root project.
Folder ini ada di .gitignore dan tidak di-commit.
"""

import os
import wave
import time
import tempfile
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Spesifikasi audio yang kompatibel dengan Whisper
SAMPLE_RATE = 16000    # 16kHz — standar speech recognition
CHANNELS = 1           # Mono
SAMPLE_WIDTH = 2       # 16-bit PCM
CHUNK_SIZE = 1024      # Jumlah frame per buffer read

# Folder penyimpanan file audio sementara
TEMP_AUDIO_DIR = Path("temp_audio")


def _ensure_temp_dir():
    """Pastikan folder temp_audio ada."""
    TEMP_AUDIO_DIR.mkdir(exist_ok=True)


MAX_RECORD_ATTEMPTS = 3   # Sesuai flow: max 3x rekam ulang


def record_audio(
    duration_seconds: int,
    filename: Optional[str] = None,
    max_attempts: int = MAX_RECORD_ATTEMPTS,
) -> Optional[str]:
    """
    Rekam audio dari microphone selama durasi yang ditentukan.
    Retry max 3x jika gagal — setelah itu return None sebagai
    sinyal UI untuk fallback ke text input manual.

    Args:
        duration_seconds : Durasi rekaman dalam detik
        filename         : Nama file output (opsional, auto-generate jika None)
        max_attempts     : Jumlah percobaan maksimal (default 3)

    Returns:
        Path ke file WAV yang direkam, atau None setelah 3x gagal.

    Penggunaan:
        audio_path = record_audio(duration_seconds=30)
        if audio_path:
            transcript = transcribe_audio(audio_path)
        else:
            # Semua 3 attempt gagal → fallback ke text input manual
    """
    try:
        import pyaudio
    except ImportError:
        print("[Recorder] PyAudio tidak terinstall. "
              "Install dengan: pip install pyaudio")
        return None

    _ensure_temp_dir()

    last_error: Optional[str] = None

    for attempt in range(1, max_attempts + 1):
        # Generate nama file unik per attempt agar tidak konflik
        if filename is None or attempt > 1:
            timestamp = int(time.time())
            attempt_filename = f"recording_{timestamp}_try{attempt}.wav"
        else:
            attempt_filename = filename

        output_path = TEMP_AUDIO_DIR / attempt_filename

        print(f"[Recorder] 🎙️ Attempt {attempt}/{max_attempts} — "
              f"merekam selama {duration_seconds} detik...")

        pa     = pyaudio.PyAudio()
        stream = None
        frames = []
        success = False

        try:
            if pa.get_device_count() == 0:
                raise RuntimeError("Tidak ada audio device yang ditemukan")

            stream = pa.open(
                format=pyaudio.paInt16,
                channels=CHANNELS,
                rate=SAMPLE_RATE,
                input=True,
                frames_per_buffer=CHUNK_SIZE,
            )

            total_chunks = int(SAMPLE_RATE / CHUNK_SIZE * duration_seconds)
            for _ in range(total_chunks):
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
                frames.append(data)

            success = True
            print(f"[Recorder] ✅ Rekaman attempt {attempt} selesai")

        except Exception as e:
            last_error = str(e)
            print(f"[Recorder] ⚠️ Attempt {attempt} gagal: {e}")

        finally:
            if stream:
                stream.stop_stream()
                stream.close()
            pa.terminate()

        if not success:
            if attempt < max_attempts:
                print(f"[Recorder] Mencoba ulang ({attempt + 1}/{max_attempts})...")
                time.sleep(1)  # Jeda 1 detik sebelum retry
            continue

        # Simpan ke file WAV
        try:
            with wave.open(str(output_path), 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(SAMPLE_WIDTH)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(b''.join(frames))

            print(f"[Recorder] 💾 Disimpan: {output_path}")
            return str(output_path)

        except Exception as e:
            last_error = str(e)
            print(f"[Recorder] Error saat simpan file attempt {attempt}: {e}")
            if attempt < max_attempts:
                time.sleep(1)

    # Semua attempt habis
    print(f"[Recorder] ❌ Gagal setelah {max_attempts}x percobaan. "
          f"Error terakhir: {last_error}")
    print("[Recorder] → UI harus fallback ke text input manual")
    return None


def record_audio_streaming(
    max_duration_seconds: int = 180,
    silence_threshold: float = 0.01,
    silence_duration: float = 2.0,
) -> Optional[str]:
    """
    Rekam audio dengan auto-stop berdasarkan deteksi hening (silence).
    Digunakan untuk Oral Presentation yang bisa berhenti sendiri.

    Args:
        max_duration_seconds : Batas maksimal durasi (default 3 menit)
        silence_threshold    : Amplitudo dianggap hening (0.0–1.0)
        silence_duration     : Berapa detik hening sebelum auto-stop

    Returns:
        Path ke file WAV, atau None jika gagal
    """
    try:
        import pyaudio
        import struct
        import math
    except ImportError:
        print("[Recorder] PyAudio tidak terinstall.")
        return None

    _ensure_temp_dir()
    timestamp = int(time.time())
    output_path = TEMP_AUDIO_DIR / f"presentation_{timestamp}.wav"

    pa = pyaudio.PyAudio()
    stream = None

    try:
        stream = pa.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK_SIZE,
        )

        print(f"[Recorder] 🎙️ Merekam (max {max_duration_seconds}s, "
              f"auto-stop jika hening {silence_duration}s)...")

        frames = []
        silent_chunks = 0
        silence_chunks_threshold = int(
            SAMPLE_RATE / CHUNK_SIZE * silence_duration
        )
        max_chunks = int(SAMPLE_RATE / CHUNK_SIZE * max_duration_seconds)

        for i in range(max_chunks):
            data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            frames.append(data)

            # Hitung RMS amplitude untuk deteksi hening
            count = len(data) // 2
            shorts = struct.unpack(f"{count}h", data)
            rms = math.sqrt(sum(s * s for s in shorts) / count) / 32768.0

            if rms < silence_threshold:
                silent_chunks += 1
                if silent_chunks >= silence_chunks_threshold:
                    print("[Recorder] ✅ Auto-stop: hening terdeteksi")
                    break
            else:
                silent_chunks = 0  # Reset counter jika ada suara

        else:
            print(f"[Recorder] ✅ Selesai: mencapai batas {max_duration_seconds}s")

    except Exception as e:
        print(f"[Recorder] Error: {e}")
        return None

    finally:
        if stream:
            stream.stop_stream()
            stream.close()
        pa.terminate()

    # Simpan ke WAV
    try:
        with wave.open(str(output_path), 'wb') as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(SAMPLE_WIDTH)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b''.join(frames))
        return str(output_path)
    except Exception as e:
        print(f"[Recorder] Error simpan: {e}")
        return None


def cleanup_temp_audio(max_age_hours: int = 24):
    """
    Hapus file audio sementara yang sudah lebih dari max_age_hours.
    Dipanggil saat app startup untuk jaga disk usage.
    """
    if not TEMP_AUDIO_DIR.exists():
        return

    now = time.time()
    deleted = 0

    for audio_file in TEMP_AUDIO_DIR.glob("*.wav"):
        age_hours = (now - audio_file.stat().st_mtime) / 3600
        if age_hours > max_age_hours:
            audio_file.unlink()
            deleted += 1

    if deleted > 0:
        print(f"[Recorder] 🗑️ Cleaned {deleted} old audio files")