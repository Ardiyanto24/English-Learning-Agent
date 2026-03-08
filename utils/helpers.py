"""
utils/helpers.py
----------------
Fungsi utilitas umum yang dipakai di seluruh codebase.

Semua fungsi di sini bersifat pure (tidak ada side effects),
tidak ada DB call, tidak ada LLM call.
"""

import uuid
from typing import Optional, Any


def generate_session_id() -> str:
    """
    Generate UUID v4 unik sebagai session ID.

    Returns:
        String UUID, contoh: "550e8400-e29b-41d4-a716-446655440000"

    Penggunaan:
        session_id = generate_session_id()
        create_session(session_id, mode="vocab")
    """
    return str(uuid.uuid4())


def calculate_score_pct(correct: int, total: int) -> float:
    """
    Hitung persentase skor dengan aman (handle division by zero).

    Args:
        correct : Jumlah jawaban benar
        total   : Total jumlah soal

    Returns:
        Float 0.0–100.0, dibulatkan 2 desimal.
        Return 0.0 jika total = 0.

    Contoh:
        calculate_score_pct(7, 10)  → 70.0
        calculate_score_pct(0, 10)  → 0.0
        calculate_score_pct(5, 0)   → 0.0  (safe, tidak error)
    """
    if total <= 0:
        return 0.0
    return round((correct / total) * 100, 2)


def is_cold_start(db_data: Any) -> bool:
    """
    Cek apakah user baru pertama kali (cold start) berdasarkan data DB.

    Cold start = data DB kosong/None/list kosong/dict kosong.
    Digunakan oleh Planner Agent untuk tentukan default config.

    Args:
        db_data: Data dari DB (bisa list, dict, None, atau apapun)

    Returns:
        True  = cold start (belum ada data history)
        False = sudah ada data history

    Contoh:
        history = get_word_tracking(topic)
        if is_cold_start(history):
            # Pakai default config
        else:
            # Pakai data dari history
    """
    if db_data is None:
        return True
    if isinstance(db_data, (list, dict, str)) and len(db_data) == 0:
        return True
    return False


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Pembagian aman — return default jika denominator nol.

    Contoh:
        safe_divide(10, 3)    → 3.333...
        safe_divide(10, 0)    → 0.0
        safe_divide(10, 0, -1) → -1
    """
    if denominator == 0:
        return default
    return numerator / denominator


def clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Batasi nilai dalam rentang [min_val, max_val].

    Digunakan untuk memastikan skor tidak keluar dari range valid.

    Contoh:
        clamp(105.0, 0.0, 100.0)  → 100.0
        clamp(-5.0, 0.0, 100.0)   → 0.0
        clamp(75.0, 0.0, 100.0)   → 75.0
    """
    return max(min_val, min(max_val, value))


def truncate_text(text: str, max_chars: int = 500, suffix: str = "...") -> str:
    """
    Potong teks panjang untuk log atau preview.

    Contoh:
        truncate_text("Hello world this is long", max_chars=10) → "Hello worl..."
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + suffix


def format_duration(seconds: int) -> str:
    """
    Format durasi dalam detik menjadi string yang readable.

    Contoh:
        format_duration(90)   → "1m 30s"
        format_duration(3661) → "1h 1m 1s"
        format_duration(45)   → "45s"
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)