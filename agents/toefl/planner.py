"""
agents/toefl/planner.py
------------------------
TOEFL Simulator Planner Agent.

Tidak memanggil LLM — distribusi soal hardcoded sesuai standar TOEFL ITP.
Satu-satunya input adalah mode: "50%" | "75%" | "100%".

Standar TOEFL ITP full test (100%):
  Listening : 50 soal  (Part A: 30, Part B: 8, Part C: 12)
  Structure : 40 soal  (Part A: 15, Part B: 25)
  Reading   : 50 soal  (5 passages × ~10 soal)
  Total     : 140 soal

Timer standar full test:
  Listening : 35 menit  (2100 detik)
  Structure : 25 menit  (1500 detik)
  Reading   : 55 menit  (3300 detik)

Mode 50% dan 75% adalah proporsi dari full test.
Timer juga proporsional — kecuali ada pembulatan ke menit penuh.

Output JSON dipakai oleh:
  - Generator agents     : tahu berapa soal yang harus dibuat per part
  - UI timer             : tahu berapa detik per section
  - Score evaluator      : tahu full_test_total untuk extrapolasi skor
"""

from utils.logger import logger

# ===================================================
# Distribusi hardcoded per mode
# ===================================================

# Full test reference (100%) — dasar semua perhitungan
_FULL_TEST = {
    "listening": {
        "total":  50,
        "part_a": 30,   # Short conversations (2 speaker)
        "part_b":  8,   # Longer conversations (2 speaker, 3-4 soal/konversasi)
        "part_c": 12,   # Talks/monologues (1 speaker, 4-5 soal/talk)
    },
    "structure": {
        "total":  40,
        "part_a": 15,   # Sentence completion
        "part_b": 25,   # Error identification
    },
    "reading": {
        "total":    50,
        "passages":  5,
        "per_passage": 10,
    },
}

# Timer full test dalam detik
_FULL_TIMER = {
    "listening": 2100,   # 35 menit
    "structure": 1500,   # 25 menit
    "reading":   3300,   # 55 menit
}

# Distribusi eksplisit per mode (bukan sekadar proporsi, karena
# jumlah passages reading dan soal per konversasi harus bilangan bulat)
_DISTRIBUTIONS = {
    "50%": {
        "listening": {
            "total":  25,
            "part_a": 15,
            "part_b":  4,   # ~2 konversasi × 2 soal
            "part_c":  6,   # ~2 talk × 3 soal
        },
        "structure": {
            "total":  20,
            "part_a":  8,
            "part_b": 12,
        },
        "reading": {
            "total":    25,
            "passages":  3,
            "per_passage": 8,   # 3×8=24, 1 passage 9 soal → total 25
        },
        "timers": {
            "listening": 1050,   # 17.5 menit → dibulatkan ke 17 menit 30 detik
            "structure":  750,   # 12.5 menit
            "reading":   1650,   # 27.5 menit
        },
    },
    "75%": {
        "listening": {
            "total":  38,
            "part_a": 23,
            "part_b":  6,   # ~3 konversasi × 2 soal
            "part_c":  9,   # ~3 talk × 3 soal
        },
        "structure": {
            "total":  30,
            "part_a": 11,
            "part_b": 19,
        },
        "reading": {
            "total":    37,
            "passages":  4,
            "per_passage": 9,   # 4×9=36, 1 passage 10 soal → total 37 (atau 4×9+1)
        },
        "timers": {
            "listening": 1575,   # ~26 menit
            "structure": 1125,   # ~19 menit
            "reading":   2475,   # ~41 menit
        },
    },
    "100%": {
        "listening": {
            "total":  50,
            "part_a": 30,
            "part_b":  8,
            "part_c": 12,
        },
        "structure": {
            "total":  40,
            "part_a": 15,
            "part_b": 25,
        },
        "reading": {
            "total":    50,
            "passages":  5,
            "per_passage": 10,
        },
        "timers": {
            "listening": 2100,
            "structure": 1500,
            "reading":   3300,
        },
    },
}

# Fallback jika Planner gagal — gunakan 100%
_FALLBACK_MODE = "100%"

# ===================================================
# Score conversion reference (dipakai evaluator nanti)
# ===================================================
# Raw score range per section untuk full test:
#   Listening : 0–50 → scaled 31–68
#   Structure : 0–40 → scaled 31–68
#   Reading   : 0–50 → scaled 31–67
# Total: (L + S + R) × 10/3 → range 310–677

SCORE_CONVERSION = {
    "listening": {
        "full_test_total": 50,
        "scaled_min": 31,
        "scaled_max": 68,
    },
    "structure": {
        "full_test_total": 40,
        "scaled_min": 31,
        "scaled_max": 68,
    },
    "reading": {
        "full_test_total": 50,
        "scaled_min": 31,
        "scaled_max": 67,
    },
}


# ===================================================
# Main: run_planner
# ===================================================
def run_planner(mode: str) -> dict:
    """
    Jalankan TOEFL Planner Agent.

    Args:
        mode: "50%" | "75%" | "100%"

    Returns:
        dict: {
            "mode"          : str,
            "listening"     : {total, part_a, part_b, part_c},
            "structure"     : {total, part_a, part_b},
            "reading"       : {total, passages, per_passage},
            "timers"        : {listening, structure, reading},  ← dalam detik
            "total_questions": int,
            "score_conversion": dict,  ← reference untuk evaluator
            "is_fallback"   : bool,
        }

    Raises:
        Tidak ada — selalu return valid output (fallback ke 100% jika mode tidak dikenal)
    """
    is_fallback = False

    if mode not in _DISTRIBUTIONS:
        logger.warning(
            f"[toefl_planner] Unknown mode '{mode}' — "
            f"falling back to {_FALLBACK_MODE}"
        )
        mode        = _FALLBACK_MODE
        is_fallback = True

    dist = _DISTRIBUTIONS[mode]

    total_questions = (
        dist["listening"]["total"]
        + dist["structure"]["total"]
        + dist["reading"]["total"]
    )

    result = {
        "mode":             mode,
        "listening":        dict(dist["listening"]),
        "structure":        dict(dist["structure"]),
        "reading":          dict(dist["reading"]),
        "timers":           dict(dist["timers"]),
        "total_questions":  total_questions,
        "score_conversion": SCORE_CONVERSION,
        "is_fallback":      is_fallback,
    }

    logger.info(
        f"[toefl_planner] Mode={mode} — "
        f"L:{dist['listening']['total']} "
        f"S:{dist['structure']['total']} "
        f"R:{dist['reading']['total']} "
        f"Total:{total_questions} soal"
    )

    return result


# ===================================================
# Utility: format timer ke string MM:SS untuk UI
# ===================================================
def format_timer(seconds: int) -> str:
    """
    Convert detik ke string MM:SS untuk ditampilkan di UI.

    Contoh: 2100 → "35:00", 1575 → "26:15"
    """
    minutes = seconds // 60
    secs    = seconds % 60
    return f"{minutes:02d}:{secs:02d}"


def get_section_info(plan: dict, section: str) -> dict:
    """
    Helper untuk UI — ambil info lengkap satu section.

    Args:
        plan   : Output dari run_planner()
        section: "listening" | "structure" | "reading"

    Returns:
        dict: {total, timer_seconds, timer_str, ...part details}
    """
    dist  = plan.get(section, {})
    timer = plan.get("timers", {}).get(section, 0)

    return {
        **dist,
        "timer_seconds": timer,
        "timer_str":     format_timer(timer),
    }