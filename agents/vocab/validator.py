"""
agents/vocab/validator.py
--------------------------
Vocab Validator Agent.

Tugas: Cek kesesuaian output Generator dengan instruksi Planner.
Toleransi: match_score >= 0.8 dianggap valid.

Flow validasi:
1. Panggil LLM Validator
2. Jika match_score < 0.8 → reject → trigger regenerate (max 3x)
3. Jika setelah 3x masih gagal → adjust output + flag is_adjusted=True + log
4. Sesi tetap lanjut dengan data yang sudah di-adjust

Input  : planner_output (dict), generator_output (dict)
Output : dict dengan key: is_valid, match_score, issues,
         adjusted_words, final_words, is_adjusted
"""

import json
import copy
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.vocab.validator_prompt import (
    VALIDATOR_SYSTEM_PROMPT,
    build_validator_prompt,
)
from agents.vocab.generator import run_generator
from utils.logger import log_error, logger
from utils.retry import retry_llm

load_dotenv()

MODEL = "claude-haiku-4-5-20251001"
MAX_REGENERATE_ATTEMPTS = 3
_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_validator_response(raw: str) -> dict:
    """Parse dan validasi JSON response dari LLM Validator."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    required = {"is_valid", "match_score", "issues", "adjusted_words"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Validator response missing fields: {missing}")

    return parsed


@retry_llm
def _call_validator_llm(planner_output: dict, generator_output: dict) -> dict:
    """Panggil Claude Haiku untuk validasi. Di-wrap @retry_llm."""
    user_prompt = build_validator_prompt(planner_output, generator_output)

    client = _get_client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=VALIDATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_validator_response(raw)


def _apply_adjustments(
    generator_output: dict,
    adjusted_words: list,
    planner_output: dict,
) -> dict:
    """
    Terapkan adjusted_words dari Validator ke generator_output.

    Validator mengembalikan hanya kata-kata yang perlu diubah.
    Fungsi ini menggabungkannya dengan kata-kata yang sudah benar.
    """
    if not adjusted_words:
        return generator_output

    words = copy.deepcopy(generator_output.get("words", []))
    target_dist = planner_output.get("format_distribution", {})

    # Hitung distribusi format saat ini
    current_dist: dict[str, list] = {}
    for i, w in enumerate(words):
        fmt = w.get("format", "")
        current_dist.setdefault(fmt, []).append(i)

    # Ganti kata yang formatnya berlebih dengan adjusted_words
    for adj_word in adjusted_words:
        adj_fmt = adj_word.get("format", "")
        target_count = target_dist.get(adj_fmt, 0)
        current_count = len(current_dist.get(adj_fmt, []))

        if current_count < target_count:
            # Cari format yang berlebih untuk diganti
            for fmt, indices in current_dist.items():
                expected = target_dist.get(fmt, 0)
                if len(indices) > expected and indices:
                    replace_idx = indices.pop()
                    words[replace_idx] = adj_word
                    current_dist.setdefault(adj_fmt, []).append(replace_idx)
                    break
        # Jika tidak ada yang perlu diganti, lewati

    return {"words": words}


def run_validator(
    planner_output: dict,
    generator_output: dict,
) -> dict:
    """
    Jalankan Vocab Validator Agent.

    Args:
        planner_output  : Output dari Planner Agent
        generator_output: Output dari Generator Agent

    Returns:
        dict: {
            "is_valid"     : bool,
            "match_score"  : float,
            "issues"       : list,
            "final_words"  : list,   ← kata final yang siap dipakai
            "is_adjusted"  : bool,   ← True jika ada penyesuaian paksa
        }
    """
    current_generator_output = generator_output
    last_validation = None

    for attempt in range(MAX_REGENERATE_ATTEMPTS):
        logger.info(
            f"[vocab_validator] Validation attempt {attempt + 1}/{MAX_REGENERATE_ATTEMPTS}"
        )

        try:
            validation = _call_validator_llm(planner_output, current_generator_output)
            last_validation = validation

            if validation.get("match_score", 0) >= 0.8:
                # ✅ Valid
                logger.info(
                    f"[vocab_validator] Valid — match_score={validation['match_score']}"
                )
                return {
                    "is_valid": True,
                    "match_score": validation["match_score"],
                    "issues": validation.get("issues", []),
                    "final_words": current_generator_output.get("words", []),
                    "is_adjusted": False,
                }

            # ❌ Tidak valid — log issues dan coba regenerate
            logger.warning(
                f"[vocab_validator] Invalid (score={validation['match_score']}) "
                f"— issues: {validation.get('issues', [])}"
            )

            if attempt < MAX_REGENERATE_ATTEMPTS - 1:
                # Regenerate menggunakan generator
                logger.info("[vocab_validator] Triggering regeneration...")
                try:
                    current_generator_output = run_generator(planner_output)
                except RuntimeError:
                    # Generator gagal total — langsung ke fallback
                    break

        except Exception as e:
            log_error(
                error_type="llm_timeout",
                agent_name="vocab_validator",
                context={"attempt": attempt + 1, "error": str(e)},
                fallback_used=False,
            )
            if attempt == MAX_REGENERATE_ATTEMPTS - 1:
                break

    # ⚠️ Semua attempt habis → adjust paksa + flag
    logger.warning(
        "[vocab_validator] All attempts failed — forcing adjustment and flagging"
    )

    adjusted_words = []
    if last_validation:
        adjusted_words = last_validation.get("adjusted_words", [])

    final_output = _apply_adjustments(
        current_generator_output, adjusted_words, planner_output
    )

    log_error(
        error_type="validation_failed",
        agent_name="vocab_validator",
        context={
            "final_score": last_validation.get("match_score") if last_validation else 0,
            "issues": last_validation.get("issues", []) if last_validation else [],
        },
        fallback_used=True,
    )

    return {
        "is_valid": False,
        "match_score": last_validation.get("match_score", 0) if last_validation else 0,
        "issues": last_validation.get("issues", []) if last_validation else [],
        "final_words": final_output.get("words", []),
        "is_adjusted": True,
    }