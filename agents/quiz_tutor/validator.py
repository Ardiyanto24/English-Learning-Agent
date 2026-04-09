"""
agents/quiz_tutor/validator.py
-------------------------------
Grammar Tutor Validator Agent.

Tugas    : Cek struktur dan kepatuhan output Generator terhadap instruksi
           Planner — bukan menilai kualitas soal atau akurasi grammar.

Model    : claude-haiku (HAIKU_MODEL) — validasi struktural tidak
           membutuhkan reasoning berat; Haiku lebih efisien untuk task ini.

Flow     :
  1. Panggil LLM untuk validasi — jika match_score >= 0.8, return valid.
  2. Jika invalid, trigger regenerate via run_generator() (max 3 percobaan).
  3. Jika semua percobaan habis, terapkan forced adjustment dari
     adjusted_questions Validator dan flag is_adjusted=True.
  Sesi tetap lanjut meski validasi tidak sempurna.
"""

import copy
import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import HAIKU_MODEL
from prompts.quiz_tutor.validator_prompt import (
    TUTOR_VALIDATOR_SYSTEM_PROMPT,
    build_validator_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm
from agents.quiz_tutor.generator import run_generator

load_dotenv()

MAX_REGENERATE_ATTEMPTS = 3
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_validator_response(raw: str) -> dict:
    """
    Parse dan validasi JSON response dari Tutor Validator.

    Args:
        raw: String response mentah dari LLM.

    Returns:
        Dict hasil validasi dengan field is_valid, match_score,
        issues, dan adjusted_questions.

    Raises:
        ValueError jika field wajib tidak ada.
    """
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
    """
    Panggil Claude Haiku untuk validasi struktur output Generator.
    Di-wrap @retry_llm: max 3x retry, exponential backoff.

    Args:
        planner_output  : Output dari Tutor Planner sebagai referensi.
        generator_output: Output dari Tutor Generator yang akan divalidasi.

    Returns:
        Dict hasil validasi dengan match_score dan adjusted_questions.
    """
    user_prompt = build_validator_prompt(planner_output, generator_output)

    response = _get_client().messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        system=TUTOR_VALIDATOR_SYSTEM_PROMPT,
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

    Berbeda dari Quiz Validator yang menggunakan format_distribution,
    Tutor Validator menggunakan type_distribution per topik dari
    planner_output["plan"] sebagai acuan distribusi yang benar.

    Jika adjusted_questions kosong, return generator_output langsung
    tanpa modifikasi apapun.

    Args:
        generator_output   : Output Generator yang akan dimodifikasi.
        adjusted_questions : List soal pengganti dari Validator.
        planner_output     : Output Planner sebagai referensi distribusi.

    Returns:
        Dict baru dengan key 'questions' berisi soal yang sudah diadjust.
    """
    if not adjusted_questions:
        return generator_output

    questions = copy.deepcopy(generator_output.get("questions", []))

    # Bangun target type_distribution dari semua plan entry
    # Struktur: {topic: {question_type: count}}
    target_dist: dict[str, dict[str, int]] = {}
    for entry in planner_output.get("plan", []):
        topic = entry.get("topic", "")
        target_dist[topic] = entry.get("type_distribution", {})

    # Hitung distribusi tipe soal aktual per topik per index
    type_indices: dict[str, dict[str, list]] = {}
    for i, q in enumerate(questions):
        topic = q.get("topic", "")
        qtype = q.get("question_type", "")
        if topic not in type_indices:
            type_indices[topic] = {}
        type_indices[topic].setdefault(qtype, []).append(i)

    # Ganti soal yang tipe/topiknya berlebih dengan adjusted_questions
    for adj_q in adjusted_questions:
        adj_topic = adj_q.get("topic", "")
        adj_type = adj_q.get("question_type", "")
        target_count = target_dist.get(adj_topic, {}).get(adj_type, 0)
        current_count = len(type_indices.get(adj_topic, {}).get(adj_type, []))

        if current_count < target_count:
            # Cari tipe yang berlebih di topik yang sama untuk diganti
            for qtype, indices in type_indices.get(adj_topic, {}).items():
                expected = target_dist.get(adj_topic, {}).get(qtype, 0)
                if len(indices) > expected and indices:
                    replace_idx = indices.pop()
                    questions[replace_idx] = adj_q
                    type_indices[adj_topic].setdefault(adj_type, []).append(
                        replace_idx
                    )
                    break

    return {"questions": questions}


def run_validator(
    planner_output: dict,
    generator_output: dict,
) -> dict:
    """
    Jalankan Tutor Validator Agent.

    Validasi output Generator terhadap instruksi Planner. Jika tidak
    valid, trigger regenerate hingga MAX_REGENERATE_ATTEMPTS kali.
    Jika semua attempt habis, terapkan forced adjustment dan lanjutkan.

    Args:
        planner_output  : Output dari Tutor Planner.
        generator_output: Output dari Tutor Generator.

    Returns:
        dict: {
            "is_valid"       : bool,
            "match_score"    : float,
            "issues"         : list,
            "final_questions": list,   ← soal final siap dipakai UI
            "is_adjusted"    : bool,
        }
    """
    current_generator_output = generator_output
    last_validation = None

    for attempt in range(MAX_REGENERATE_ATTEMPTS):
        logger.info(
            f"[tutor_validator] Validation attempt "
            f"{attempt + 1}/{MAX_REGENERATE_ATTEMPTS}"
        )

        try:
            validation = _call_validator_llm(planner_output, current_generator_output)
            last_validation = validation

            score = validation.get("match_score", 0)

            if score >= 0.8:
                # ✅ Valid
                logger.info(f"[tutor_validator] Valid — match_score={score}")
                return {
                    "is_valid": True,
                    "match_score": score,
                    "issues": validation.get("issues", []),
                    "final_questions": current_generator_output.get("questions", []),
                    "is_adjusted": False,
                }

            # ❌ Tidak valid — log dan coba regenerate
            logger.warning(
                f"[tutor_validator] Invalid (score={score}) "
                f"— issues: {validation.get('issues', [])}"
            )

            if attempt < MAX_REGENERATE_ATTEMPTS - 1:
                logger.info("[tutor_validator] Triggering regeneration...")
                try:
                    current_generator_output = run_generator(planner_output)
                except RuntimeError:
                    break  # Generator gagal total → langsung ke forced adjustment

        except Exception as e:
            log_error(
                error_type="llm_timeout",
                agent_name="tutor_validator",
                context={"attempt": attempt + 1, "error": str(e)},
                fallback_used=False,
            )
            if attempt == MAX_REGENERATE_ATTEMPTS - 1:
                break

    # ⚠️ Semua attempt habis → forced adjustment
    logger.warning("[tutor_validator] All attempts failed — forcing adjustment")

    validator_unavailable = last_validation is None

    adjusted_questions = []
    if last_validation:
        adjusted_questions = last_validation.get("adjusted_questions", [])

    final_output = _apply_adjusted_questions(
        current_generator_output, adjusted_questions, planner_output
    )

    log_error(
        error_type="validator_unavailable" if validator_unavailable else "validation_failed",
        agent_name="tutor_validator",
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
