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
    build_tutor_validator_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm

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
    user_prompt = build_tutor_validator_prompt(planner_output, generator_output)

    response = _get_client().messages.create(
        model=HAIKU_MODEL,
        max_tokens=1024,
        system=TUTOR_VALIDATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_validator_response(raw)
