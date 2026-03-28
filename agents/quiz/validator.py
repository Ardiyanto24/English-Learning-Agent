"""
agents/quiz/validator.py
-------------------------
Quiz Validator Agent.

Cek kesesuaian output Generator dengan instruksi Planner.
Toleransi: match_score >= 0.8 dianggap valid.

Perbedaan dari Vocab Validator:
- Check lebih banyak: jumlah soal, format, difficulty, DAN topik
- Menggunakan Haiku (struktur check saja, tidak butuh reasoning berat)

Flow:
1. Panggil LLM untuk validasi
2. match_score < 0.8 → reject → trigger regenerate (max 3x)
3. Setelah 3x gagal → adjust + flag is_adjusted=True + log
4. Sesi tetap lanjut dengan soal yang sudah di-adjust
"""

import json
import copy
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.quiz.validator_prompt import (
    QUIZ_VALIDATOR_SYSTEM_PROMPT,
    build_validator_prompt,
)
from agents.quiz.generator import run_generator
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import HAIKU_MODEL

load_dotenv()

MAX_REGENERATE_ATTEMPTS = 3
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_validator_response(raw: str) -> dict:
    """Parse dan validasi JSON response dari Validator."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    required = {"is_valid", "match_score", "issues", "adjusted_questions"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Validator response missing fields: {missing}")

    return parsed


@retry_llm
def _call_validator_llm(
    planner_output: dict,
    generator_output: dict,
) -> dict:
    """Panggil Haiku untuk validasi struktur. Di-wrap @retry_llm."""
    user_prompt = build_validator_prompt(planner_output, generator_output)

    client = _get_client()
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        system=QUIZ_VALIDATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_validator_response(raw)


def _apply_adjusted_questions(
    generator_output: dict,
    adjusted_questions: list,
    planner_output: dict,
) -> dict:
    """
    Terapkan adjusted_questions dari Validator ke generator_output.

    Validator mengembalikan soal-soal yang perlu diganti.
    Fungsi ini menggabungkannya dengan soal yang sudah benar.
    """
    if not adjusted_questions:
        return generator_output

    questions = copy.deepcopy(generator_output.get("questions", []))
    target_dist = planner_output.get("format_distribution", {})

    # Hitung distribusi format saat ini per index
    format_indices: dict[str, list] = {}
    for i, q in enumerate(questions):
        fmt = q.get("format", "")
        format_indices.setdefault(fmt, []).append(i)

    # Ganti soal yang formatnya berlebih dengan adjusted_questions
    for adj_q in adjusted_questions:
        adj_fmt = adj_q.get("format", "")
        target_count = target_dist.get(adj_fmt, 0)
        current_count = len(format_indices.get(adj_fmt, []))

        if current_count < target_count:
            # Cari format yang berlebih untuk diganti
            for fmt, indices in format_indices.items():
                expected = target_dist.get(fmt, 0)
                if len(indices) > expected and indices:
                    replace_idx = indices.pop()
                    questions[replace_idx] = adj_q
                    format_indices.setdefault(adj_fmt, []).append(replace_idx)
                    break

    return {"questions": questions}


def run_validator(
    planner_output: dict,
    generator_output: dict,
) -> dict:
    """
    Jalankan Quiz Validator Agent.

    Args:
        planner_output  : Output dari Quiz Planner
        generator_output: Output dari Quiz Generator

    Returns:
        dict: {
            "is_valid"          : bool,
            "match_score"       : float,
            "issues"            : list,
            "final_questions"   : list,   ← soal final siap dipakai
            "is_adjusted"       : bool,
        }
    """
    current_generator_output = generator_output
    last_validation = None

    for attempt in range(MAX_REGENERATE_ATTEMPTS):
        logger.info(
            f"[quiz_validator] Validation attempt "
            f"{attempt + 1}/{MAX_REGENERATE_ATTEMPTS}"
        )

        try:
            validation = _call_validator_llm(
                planner_output, current_generator_output
            )
            last_validation = validation

            score = validation.get("match_score", 0)

            if score >= 0.8:
                # ✅ Valid
                logger.info(
                    f"[quiz_validator] Valid — match_score={score}"
                )
                return {
                    "is_valid": True,
                    "match_score": score,
                    "issues": validation.get("issues", []),
                    "final_questions": current_generator_output.get(
                        "questions", []
                    ),
                    "is_adjusted": False,
                }

            # ❌ Tidak valid — log dan coba regenerate
            logger.warning(
                f"[quiz_validator] Invalid (score={score}) "
                f"— issues: {validation.get('issues', [])}"
            )

            if attempt < MAX_REGENERATE_ATTEMPTS - 1:
                logger.info("[quiz_validator] Triggering regeneration...")
                try:
                    current_generator_output = run_generator(planner_output)
                except RuntimeError:
                    break  # Generator gagal total → langsung fallback

        except Exception as e:
            log_error(
                error_type="llm_timeout",
                agent_name="quiz_validator",
                context={"attempt": attempt + 1, "error": str(e)},
                fallback_used=False,
            )
            if attempt == MAX_REGENERATE_ATTEMPTS - 1:
                break

    # ⚠️ Semua attempt habis → adjust paksa + flag
    logger.warning(
        "[quiz_validator] All attempts failed — forcing adjustment"
    )

    validator_unavailable = last_validation is None

    adjusted_questions = []
    if last_validation:
        adjusted_questions = last_validation.get("adjusted_questions", [])

    final_output = _apply_adjusted_questions(
        current_generator_output, adjusted_questions, planner_output
    )

    log_error(
        error_type="validator_unavailable" if validator_unavailable else "validation_failed",
        agent_name="quiz_validator",
        context={
            "final_score": last_validation.get("match_score", 0) if last_validation else 0,
            "issues": last_validation.get("issues", []) if last_validation else [],
            "validator_unavailable": validator_unavailable,
        },
        fallback_used=True,
    )

    return {
        "is_valid": False,
        "match_score": last_validation.get("match_score", 0) if last_validation else 0,
        "issues": last_validation.get("issues", []) if last_validation else [],
        "final_questions": final_output.get("questions", []),
        "is_adjusted": not validator_unavailable,
        "is_validator_unavailable": validator_unavailable,
    }
