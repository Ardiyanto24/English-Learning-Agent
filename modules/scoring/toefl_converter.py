"""
modules/scoring/toefl_converter.py
------------------------------------
Konversi skor TOEFL ITP dari raw score ke estimated score.

Alur konversi (sesuai TOEFL ITP official scoring):
1. raw_score      : Jumlah jawaban benar di mode yang dimainkan
2. extrapolated   : Proyeksi ke soal penuh (kalau main mode 50%)
3. scaled_score   : Konversi ke skala ITP per section (tabel resmi)
4. estimated_score: Gabungan 3 section → skor akhir 310–677

Formula estimated:
    estimated = round((L_scaled + S_scaled + R_scaled) * 10 / 3)
    Range valid: 310–677

Tabel konversi berdasarkan TOEFL ITP Score Conversion Table resmi.
"""

from utils.helpers import clamp

# ===================================================
# Tabel konversi resmi TOEFL ITP
# Key   = raw score
# Value = scaled score
# ===================================================

# Listening Comprehension: raw 0–50 → scaled 31–68
LISTENING_CONVERSION: dict[int, int] = {
    50: 68,
    49: 67,
    48: 66,
    47: 65,
    46: 64,
    45: 63,
    44: 62,
    43: 61,
    42: 60,
    41: 59,
    40: 58,
    39: 57,
    38: 56,
    37: 55,
    36: 54,
    35: 53,
    34: 52,
    33: 51,
    32: 50,
    31: 49,
    30: 48,
    29: 47,
    28: 46,
    27: 45,
    26: 44,
    25: 43,
    24: 42,
    23: 41,
    22: 40,
    21: 39,
    20: 38,
    19: 37,
    18: 36,
    17: 35,
    16: 34,
    15: 33,
    14: 32,
    13: 32,
    12: 31,
    11: 31,
    10: 31,
    9: 31,
    8: 31,
    7: 31,
    6: 31,
    5: 31,
    4: 31,
    3: 31,
    2: 31,
    1: 31,
    0: 31,
}

# Structure & Written Expression: raw 0–40 → scaled 31–68
STRUCTURE_CONVERSION: dict[int, int] = {
    40: 68,
    39: 67,
    38: 66,
    37: 65,
    36: 64,
    35: 63,
    34: 62,
    33: 61,
    32: 60,
    31: 59,
    30: 58,
    29: 57,
    28: 56,
    27: 55,
    26: 54,
    25: 52,
    24: 51,
    23: 50,
    22: 48,
    21: 47,
    20: 46,
    19: 44,
    18: 43,
    17: 42,
    16: 40,
    15: 39,
    14: 38,
    13: 36,
    12: 35,
    11: 34,
    10: 32,
    9: 31,
    8: 31,
    7: 31,
    6: 31,
    5: 31,
    4: 31,
    3: 31,
    2: 31,
    1: 31,
    0: 31,
}

# Reading Comprehension: raw 0–50 → scaled 31–67
READING_CONVERSION: dict[int, int] = {
    50: 67,
    49: 66,
    48: 65,
    47: 64,
    46: 63,
    45: 62,
    44: 61,
    43: 60,
    42: 59,
    41: 58,
    40: 57,
    39: 56,
    38: 55,
    37: 54,
    36: 53,
    35: 52,
    34: 51,
    33: 50,
    32: 49,
    31: 48,
    30: 47,
    29: 46,
    28: 45,
    27: 44,
    26: 43,
    25: 42,
    24: 41,
    23: 40,
    22: 39,
    21: 38,
    20: 37,
    19: 36,
    18: 35,
    17: 34,
    16: 33,
    15: 32,
    14: 31,
    13: 31,
    12: 31,
    11: 31,
    10: 31,
    9: 31,
    8: 31,
    7: 31,
    6: 31,
    5: 31,
    4: 31,
    3: 31,
    2: 31,
    1: 31,
    0: 31,
}

_CONVERSION_TABLES = {
    "listening": LISTENING_CONVERSION,
    "structure": STRUCTURE_CONVERSION,
    "reading": READING_CONVERSION,
}

# Batas maksimal raw score per section (untuk full test)
_MAX_RAW = {
    "listening": 50,
    "structure": 40,
    "reading": 50,
}


def extrapolate_score(
    raw: int,
    total_mode: int,
    total_full: int,
) -> int:
    """
    Proyeksikan raw score dari mode parsial ke skala full test.

    Contoh:
        User main mode 50% (25 soal listening dari 50).
        Jawab benar 18 dari 25.
        → extrapolate_score(18, 25, 50) = round(18/25 * 50) = 36

    Args:
        raw        : Jumlah jawaban benar di mode yang dimainkan
        total_mode : Total soal di mode yang dimainkan
        total_full : Total soal di full test (50/40/50)

    Returns:
        Integer raw score yang diproyeksikan ke full test
    """
    if total_mode <= 0:
        return 0
    extrapolated = round((raw / total_mode) * total_full)
    return int(clamp(extrapolated, 0, total_full))


def convert_to_scaled(raw_score: int, section: str) -> int:
    """
    Konversi raw score ke scaled score menggunakan tabel konversi ITP.

    Args:
        raw_score : Raw score (sudah diextrapolate jika perlu)
        section   : "listening" | "structure" | "reading"

    Returns:
        Scaled score sesuai tabel ITP resmi

    Raises:
        ValueError jika section tidak valid
    """
    section = section.lower()
    if section not in _CONVERSION_TABLES:
        raise ValueError(
            f"Section tidak valid: '{section}'. "
            f"Pilihan valid: {list(_CONVERSION_TABLES.keys())}"
        )

    table = _CONVERSION_TABLES[section]
    max_raw = _MAX_RAW[section]

    # Clamp raw score ke range valid
    raw_clamped = int(clamp(raw_score, 0, max_raw))

    # Lookup tabel — fallback ke nilai minimum jika key tidak ada
    return table.get(raw_clamped, 31)


def calculate_estimated_toefl(
    l_scaled: int,
    s_scaled: int,
    r_scaled: int,
) -> int:
    """
    Hitung estimated TOEFL ITP score dari 3 scaled score.

    Formula resmi:
        estimated = round((L + S + R) * 10 / 3)
        Range: 310–677

    Args:
        l_scaled : Listening scaled score (31–68)
        s_scaled : Structure scaled score (31–68)
        r_scaled : Reading scaled score (31–67)

    Returns:
        Integer estimated score, range 310–677
    """
    estimated = round((l_scaled + s_scaled + r_scaled) * 10 / 3)
    return int(clamp(estimated, 310, 677))


def process_full_score(
    listening_raw: int,
    structure_raw: int,
    reading_raw: int,
    listening_total_mode: int,
    structure_total_mode: int,
    reading_total_mode: int,
    listening_total_full: int = 50,
    structure_total_full: int = 40,
    reading_total_full: int = 50,
) -> dict:
    """
    Proses lengkap dari raw score ke estimated score dalam satu call.
    Ini yang dipanggil oleh TOEFL Evaluator Agent.

    Returns:
        dict dengan semua intermediate values:
        {
            listening_raw, structure_raw, reading_raw,
            listening_extrapolated, structure_extrapolated, reading_extrapolated,
            listening_scaled, structure_scaled, reading_scaled,
            estimated_score
        }
    """
    # Step 1: Extrapolate ke full test scale
    l_extrap = extrapolate_score(listening_raw, listening_total_mode, listening_total_full)
    s_extrap = extrapolate_score(structure_raw, structure_total_mode, structure_total_full)
    r_extrap = extrapolate_score(reading_raw, reading_total_mode, reading_total_full)

    # Step 2: Convert ke scaled score
    l_scaled = convert_to_scaled(l_extrap, "listening")
    s_scaled = convert_to_scaled(s_extrap, "structure")
    r_scaled = convert_to_scaled(r_extrap, "reading")

    # Step 3: Hitung estimated score
    estimated = calculate_estimated_toefl(l_scaled, s_scaled, r_scaled)

    return {
        "listening_raw": listening_raw,
        "structure_raw": structure_raw,
        "reading_raw": reading_raw,
        "listening_extrapolated": l_extrap,
        "structure_extrapolated": s_extrap,
        "reading_extrapolated": r_extrap,
        "listening_scaled": l_scaled,
        "structure_scaled": s_scaled,
        "reading_scaled": r_scaled,
        "estimated_score": estimated,
    }
