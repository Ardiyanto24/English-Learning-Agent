"""
agents/toefl/evaluator.py
--------------------------
TOEFL Evaluator Agent.

Tidak ada LLM di sini — murni kalkulasi matematika menggunakan
tabel konversi resmi TOEFL ITP dari toefl_converter.py.

Flow:
  1. Baca jawaban user per section dari DB
  2. Hitung raw score (jumlah benar)
  3. Extrapolate ke full-test scale (untuk mode 50%/75%)
  4. Convert ke scaled score via tabel konversi ITP
  5. Hitung estimated score: (L + S + R) × 10/3

Error handling:
  - Jika DB tidak bisa dibaca → raise RuntimeError
  - Jika konversi gagal → simpan raw score saja + flag score_pending
"""

from modules.scoring.toefl_converter import process_full_score
from utils.logger import log_error, logger


def _calculate_raw_score(
    user_answers: dict[int, str],
    answer_key: dict[int, str],
) -> int:
    """
    Hitung raw score dari jawaban user vs kunci jawaban.

    Args:
        user_answers : {question_number: user_answer_choice}
        answer_key   : {question_number: correct_answer}

    Returns:
        Integer jumlah jawaban benar
    """
    correct = 0
    for q_num, correct_ans in answer_key.items():
        if user_answers.get(q_num, "").upper() == correct_ans.upper():
            correct += 1
    return correct


def _build_answer_key(section_content: dict, section: str) -> dict[int, str]:
    """
    Bangun kunci jawaban {question_number: correct_answer}
    dari konten generator.

    Args:
        section_content : Dict konten section (output generator)
        section         : "listening" | "structure" | "reading"
    """
    answer_key: dict[int, str] = {}
    q_num = 1

    if section == "listening":
        for part_key in ("part_a", "part_b", "part_c"):
            for item in section_content.get(part_key, []):
                for q in item.get("questions", []):
                    answer_key[q_num] = q["correct_answer"]
                    q_num += 1

    elif section == "structure":
        for part_key in ("part_a", "part_b"):
            for q in section_content.get(part_key, []):
                answer_key[q_num] = q["correct_answer"]
                q_num += 1

    elif section == "reading":
        for passage in section_content.get("passages", []):
            for q in passage.get("questions", []):
                answer_key[q_num] = q["correct_answer"]
                q_num += 1

    return answer_key


def run_evaluator(
    session_id: str,
    user_answers: dict,
    listening_content: dict,
    structure_content: dict,
    reading_content: dict,
    planner_output: dict,
) -> dict:
    """
    Jalankan TOEFL Evaluator.

    Args:
        session_id        : ID sesi (untuk logging)
        user_answers      : {
                              "listening": {q_num: answer},
                              "structure": {q_num: answer},
                              "reading"  : {q_num: answer},
                            }
        listening_content : Output generator Listening (post-validator)
        structure_content : Output generator Structure (post-validator)
        reading_content   : Output generator Reading (post-validator)
        planner_output    : Output run_planner() — untuk total soal per mode

    Returns:
        dict: {
            # Raw scores
            "listening_raw"           : int,
            "structure_raw"           : int,
            "reading_raw"             : int,

            # Extrapolated (ke full-test scale)
            "listening_extrapolated"  : int,
            "structure_extrapolated"  : int,
            "reading_extrapolated"    : int,

            # Scaled scores (tabel konversi ITP)
            "listening_scaled"        : int,
            "structure_scaled"        : int,
            "reading_scaled"          : int,

            # Final
            "estimated_score"         : int,   ← 310–677

            # Metadata
            "mode"                    : str,   ← "50%"|"75%"|"100%"
            "is_graded"               : bool,
            "score_pending"           : bool,  ← True jika konversi gagal
            "total_answered"          : int,
            "section_totals"          : dict,  ← total soal per section di mode ini
            "performance_breakdown"   : dict,  ← pct benar per section
        }
    """
    mode = planner_output.get("mode", "100%")
    logger.info(f"[toefl_evaluator] Evaluating session={session_id} mode={mode}")

    # ── Cek apakah ada konten yang di-adjust oleh Validator ──────────────
    content_adjusted = any(
        [
            listening_content.get("is_adjusted", False),
            structure_content.get("is_adjusted", False),
            reading_content.get("is_adjusted", False),
        ]
    )
    if content_adjusted:
        logger.warning("[toefl_evaluator] One or more sections contain adjusted content — " "score reliability may be reduced")

    # ── Bangun kunci jawaban dari konten generator ────────────────────────
    listening_key = _build_answer_key(listening_content, "listening")
    structure_key = _build_answer_key(structure_content, "structure")
    reading_key = _build_answer_key(reading_content, "reading")

    # ── Hitung raw score ──────────────────────────────────────────────────
    l_answers = user_answers.get("listening", {})
    s_answers = user_answers.get("structure", {})
    r_answers = user_answers.get("reading", {})

    l_raw = _calculate_raw_score(l_answers, listening_key)
    s_raw = _calculate_raw_score(s_answers, structure_key)
    r_raw = _calculate_raw_score(r_answers, reading_key)

    # ── Total soal di mode ini (untuk extrapolasi) ────────────────────────
    l_total = listening_content.get("total_questions", 0)
    s_total = structure_content.get("total_questions", 0)
    r_total = reading_content.get("total_questions", 0)

    # Full-test totals (dari score_conversion di planner)
    score_conv = planner_output.get("score_conversion", {})
    l_full = score_conv.get("listening", {}).get("full_test_total", 50)
    s_full = score_conv.get("structure", {}).get("full_test_total", 40)
    r_full = score_conv.get("reading", {}).get("full_test_total", 50)

    total_answered = len(l_answers) + len(s_answers) + len(r_answers)

    logger.info(f"[toefl_evaluator] Raw scores — " f"L:{l_raw}/{l_total} S:{s_raw}/{s_total} R:{r_raw}/{r_total}")

    # ── Konversi skor ─────────────────────────────────────────────────────
    try:
        score_result = process_full_score(
            listening_raw=l_raw,
            structure_raw=s_raw,
            reading_raw=r_raw,
            listening_total_mode=l_total,
            structure_total_mode=s_total,
            reading_total_mode=r_total,
            listening_total_full=l_full,
            structure_total_full=s_full,
            reading_total_full=r_full,
        )
        score_pending = False

    except Exception as e:
        log_error(
            error_type="score_conversion_failed",
            agent_name="toefl_evaluator",
            context={
                "session_id": session_id,
                "l_raw": l_raw,
                "s_raw": s_raw,
                "r_raw": r_raw,
                "error": str(e),
            },
            fallback_used=True,
        )
        logger.error(f"[toefl_evaluator] Score conversion failed: {e} — saving raw scores only")
        # Fallback: simpan raw saja tanpa konversi
        score_result = {
            "listening_raw": l_raw,
            "structure_raw": s_raw,
            "reading_raw": r_raw,
            "listening_extrapolated": 0,
            "structure_extrapolated": 0,
            "reading_extrapolated": 0,
            "listening_scaled": 0,
            "structure_scaled": 0,
            "reading_scaled": 0,
            "estimated_score": 0,
        }
        score_pending = True

    # ── Performance breakdown (pct benar per section) ─────────────────────
    def _pct(raw: int, total: int) -> float:
        return round(raw / total * 100, 1) if total > 0 else 0.0

    performance_breakdown = {
        "listening_pct": _pct(l_raw, l_total),
        "structure_pct": _pct(s_raw, s_total),
        "reading_pct": _pct(r_raw, r_total),
        "overall_pct": _pct(l_raw + s_raw + r_raw, l_total + s_total + r_total),
    }

    logger.info(
        f"[toefl_evaluator] Score — "
        f"estimated={score_result.get('estimated_score')} "
        f"L_scaled={score_result.get('listening_scaled')} "
        f"S_scaled={score_result.get('structure_scaled')} "
        f"R_scaled={score_result.get('reading_scaled')} "
        f"score_pending={score_pending}"
    )

    return {
        **score_result,
        "mode": mode,
        "is_graded": not score_pending,
        "score_pending": score_pending,
        "content_adjusted": content_adjusted,
        "total_answered": total_answered,
        "section_totals": {
            "listening": l_total,
            "structure": s_total,
            "reading": r_total,
        },
        "performance_breakdown": performance_breakdown,
    }
