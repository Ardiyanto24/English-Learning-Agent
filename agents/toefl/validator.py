"""
agents/toefl/validator.py
--------------------------
TOEFL Validator Agent.

Quality gate setelah 3 generator selesai.
Menggunakan Claude Sonnet (bukan Haiku) karena harus menilai:
  - Apakah distractor cukup plausible?
  - Apakah ada ambiguitas dalam soal?
  - Apakah passage kohesif dan akademik?

Flow:
  1. Kirim sample konten ke Sonnet untuk quality check
  2. Jika overall_quality_score >= 0.8 → acceptable, lanjut
  3. Jika < 0.8 → retry regenerate section bermasalah (max 3x)
  4. Jika setelah 3x masih < 0.8 → adjust + flag is_adjusted=True
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.toefl.validator_prompt import (
    TOEFL_VALIDATOR_SYSTEM_PROMPT,
    build_validator_prompt,
)
from agents.toefl.listening_generator import run_generator as regen_listening
from agents.toefl.structure_generator import run_generator as regen_structure
from agents.toefl.reading_generator import run_generator as regen_reading
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL

load_dotenv()

QUALITY_THRESHOLD = 0.8  # 80% toleransi
MAX_REGEN_ATTEMPTS = 3  # Max retry regenerasi per section

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_validation_response(raw: str) -> dict:
    """Parse dan validasi JSON dari Validator."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    # Pastikan field wajib ada
    required = {"overall_quality_score", "is_acceptable", "quality_check"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Validator response missing fields: {missing}")

    # Clamp score ke range valid
    score = float(parsed.get("overall_quality_score", 0))
    parsed["overall_quality_score"] = max(0.0, min(1.0, score))
    parsed["is_acceptable"] = parsed["overall_quality_score"] >= QUALITY_THRESHOLD

    return parsed


@retry_llm
def _call_validator(
    planner_output: dict,
    listening_content: dict,
    structure_content: dict,
    reading_content: dict,
) -> dict:
    """Panggil Claude Sonnet untuk quality check."""
    user_prompt = build_validator_prompt(
        planner_output=planner_output,
        listening_content=listening_content,
        structure_content=structure_content,
        reading_content=reading_content,
    )

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=2048,
        system=TOEFL_VALIDATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    return _parse_validation_response(response.content[0].text)


def _identify_weak_sections(validation_result: dict) -> list[str]:
    """
    Identifikasi section mana yang perlu di-regenerate
    berdasarkan quality check per section.
    """
    weak = []
    qc = validation_result.get("quality_check", {})

    for section in ("listening", "structure", "reading"):
        section_score = qc.get(section, {}).get("score", 1.0)
        if section_score < QUALITY_THRESHOLD:
            weak.append(section)

    return weak


def _adjust_content(
    content: dict,
    section: str,
    validation_result: dict,
) -> dict:
    """
    Fallback jika regenerasi tetap gagal setelah MAX_REGEN_ATTEMPTS.
    Hanya flag konten yang bermasalah — sesi tetap lanjut.
    """
    adjusted = dict(content)
    adjusted["is_adjusted"] = True
    adjusted["adjustment_reason"] = f"Quality check score below threshold after {MAX_REGEN_ATTEMPTS} " f"regeneration attempts. Flags: " + str(
        validation_result.get("quality_check", {}).get(section, {}).get("flags", [])
    )

    log_error(
        error_type="quality_threshold",
        agent_name="toefl_validator",
        context={
            "section": section,
            "final_score": validation_result.get("overall_quality_score"),
            "flags": validation_result.get("quality_check", {}).get(section, {}).get("flags", []),
        },
        fallback_used=True,
    )
    logger.warning(f"[toefl_validator] Section '{section}' adjusted and flagged " f"after {MAX_REGEN_ATTEMPTS} regen attempts")

    return adjusted


def run_validator(
    planner_output: dict,
    listening_content: dict,
    structure_content: dict,
    reading_content: dict,
    session_id: str,
) -> dict:
    """
    Jalankan TOEFL Validator Agent.

    Args:
        planner_output    : Output dari run_planner()
        listening_content : Output dari listening_generator.run_generator()
        structure_content : Output dari structure_generator.run_generator()
        reading_content   : Output dari reading_generator.run_generator()
        session_id        : Untuk keperluan regenerasi audio Listening

    Returns:
        dict: {
            "validation"       : validation result dari LLM,
            "listening"        : konten final (mungkin sudah di-regen/adjust),
            "structure"        : konten final,
            "reading"          : konten final,
            "is_adjusted"      : bool,  ← True jika ada section yang di-adjust
            "adjusted_sections": [],    ← list nama section yang di-adjust
        }
    """
    # State mutable selama proses
    final_listening = listening_content
    final_structure = structure_content
    final_reading = reading_content
    adjusted = []

    # ── Pass pertama: quality check ──────────────────────────────────────
    try:
        validation = _call_validator(
            planner_output=planner_output,
            listening_content=final_listening,
            structure_content=final_structure,
            reading_content=final_reading,
        )
    except Exception as e:
        # Jika validator sendiri gagal → lolos semua, tapi flag eksplisit
        # overall_quality_score=None agar downstream tidak salah baca sebagai lolos
        logger.warning(f"[toefl_validator] Validator LLM failed: {e} — " f"passing all content with validator_unavailable=True")
        log_error(
            error_type="validator_unavailable",
            agent_name="toefl_validator",
            context={"error": str(e)},
            fallback_used=True,
        )
        return {
            "validation": {
                "overall_quality_score": None,
                "is_acceptable": True,
                "validator_unavailable": True,
            },
            "listening": final_listening,
            "structure": final_structure,
            "reading": final_reading,
            "is_adjusted": True,
            "adjusted_sections": ["validator_unavailable"],
        }

    logger.info(f"[toefl_validator] Initial quality score: " f"{validation.get('overall_quality_score', 0):.2f}")

    if validation.get("is_acceptable"):
        return {
            "validation": validation,
            "listening": final_listening,
            "structure": final_structure,
            "reading": final_reading,
            "is_adjusted": False,
            "adjusted_sections": [],
        }

    # ── Regenerasi section yang lemah ────────────────────────────────────
    weak_sections = _identify_weak_sections(validation)
    logger.info(f"[toefl_validator] Weak sections: {weak_sections} — " f"attempting regeneration (max {MAX_REGEN_ATTEMPTS}x per section)")

    regen_map = {
        "listening": (
            regen_listening,
            lambda a: (planner_output["listening"], session_id, a),
        ),
        "structure": (
            regen_structure,
            lambda a: (planner_output["structure"],),
        ),
        "reading": (
            regen_reading,
            lambda a: (planner_output["reading"],),
        ),
    }

    for section in weak_sections:
        regen_fn, args_fn = regen_map[section]
        success = False

        for attempt in range(1, MAX_REGEN_ATTEMPTS + 1):
            logger.info(f"[toefl_validator] Regen {section} attempt {attempt}/{MAX_REGEN_ATTEMPTS}")
            try:
                args = args_fn(attempt)
                new_content = regen_fn(*args)

                # Re-validate section ini
                new_validation = _call_validator(
                    planner_output=planner_output,
                    listening_content=new_content if section == "listening" else final_listening,
                    structure_content=new_content if section == "structure" else final_structure,
                    reading_content=new_content if section == "reading" else final_reading,
                )

                sec_score = new_validation.get("quality_check", {}).get(section, {}).get("score", 0)

                if sec_score >= QUALITY_THRESHOLD:
                    # Terima konten baru
                    if section == "listening":
                        final_listening = new_content
                    elif section == "structure":
                        final_structure = new_content
                    elif section == "reading":
                        final_reading = new_content

                    validation = new_validation
                    logger.info(f"[toefl_validator] Section '{section}' passed " f"on attempt {attempt} (score={sec_score:.2f})")
                    success = True
                    break

            except Exception as e:
                logger.warning(f"[toefl_validator] Regen attempt {attempt} failed " f"for '{section}': {e}")

        if not success:
            # Adjust & flag — sesi tetap lanjut
            if section == "listening":
                final_listening = _adjust_content(final_listening, section, validation)
            elif section == "structure":
                final_structure = _adjust_content(final_structure, section, validation)
            elif section == "reading":
                final_reading = _adjust_content(final_reading, section, validation)
            adjusted.append(section)

    return {
        "validation": validation,
        "listening": final_listening,
        "structure": final_structure,
        "reading": final_reading,
        "is_adjusted": len(adjusted) > 0,
        "adjusted_sections": adjusted,
    }
