"""
agents/speaking/follow_up.py
------------------------------
Follow-up Generator Agent.

Dipanggil HANYA ketika Assessor memutuskan "new_subtopic".
Decision "continue" TIDAK memanggil agent ini — session flow
langsung menggunakan `suggested_followup` dari Assessor.

Ketika dipanggil untuk "new_subtopic", ada dua jalur:
- Jika Assessor sudah menyertakan `suggested_followup` (len > 10):
  → return langsung tanpa memanggil LLM (source: "assessor")
- Jika tidak ada saran dari Assessor:
  → panggil Claude Sonnet untuk generate follow-up (source: "llm")

Menggunakan Sonnet karena kualitas follow-up pertanyaan
sangat penting — pertanyaan yang canggung akan merusak
flow conversation.
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL

load_dotenv()

_client: Optional[anthropic.Anthropic] = None

# System prompt untuk Follow-up Generator
# Lebih singkat dari yang lain karena tugasnya sangat spesifik
_FOLLOWUP_SYSTEM_PROMPT = """You are a skilled English conversation facilitator.

Your task is to generate a natural follow-up question or prompt that:
1. Acknowledges what the student just said (brief, 1 sentence)
2. Introduces a NEW angle or sub-topic related to the main theme
3. Is open-ended — cannot be answered with yes/no
4. Sounds natural, like a real conversation partner — not an examiner

The transition must feel smooth, not abrupt. The student should feel
heard before being redirected.

Respond with valid JSON only:
{
  "follow_up_prompt": "string — the complete follow-up including acknowledgment",
  "new_angle": "string — brief description of the new angle introduced"
}"""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _parse_followup_response(raw: str) -> dict:
    """Parse JSON response dari Follow-up Generator."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    if "follow_up_prompt" not in parsed:
        raise ValueError("Follow-up response missing 'follow_up_prompt'")

    return parsed


@retry_llm
def _call_followup_llm(
    main_topic: str,
    latest_user_text: str,
    previous_angles: list[str],
) -> dict:
    """Panggil Claude Sonnet untuk generate follow-up."""
    # Buat daftar angle yang sudah dipakai agar tidak repetitif
    avoid_section = ""
    if previous_angles:
        avoid_section = f"""
Already discussed angles (do NOT revisit):
{chr(10).join(f'- {a}' for a in previous_angles[-4:])}
"""

    user_prompt = f"""Generate a natural follow-up to keep the conversation going.

## Context
Main topic       : {main_topic}
Student just said: "{latest_user_text[:300]}"
{avoid_section}
Generate a follow-up that transitions to a new angle of the same topic.
Respond with JSON only."""

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=256,
        system=_FOLLOWUP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_followup_response(raw)


def run_follow_up(
    main_topic: str,
    latest_user_text: str,
    assessor_suggestion: Optional[str] = None,
    previous_angles: Optional[list[str]] = None,
    session_id: Optional[str] = None,
) -> dict:
    """
    Jalankan Follow-up Generator Agent.

    Optimasi: jika Assessor sudah memberikan `suggested_followup`,
    gunakan langsung tanpa memanggil LLM.

    Args:
        main_topic          : Topik utama sesi
        latest_user_text    : Ucapan user terakhir (untuk konteks)
        assessor_suggestion : Saran follow-up dari Assessor (opsional)
        previous_angles     : Angle yang sudah dibahas (hindari repetisi)
        session_id          : ID sesi untuk logging

    Returns:
        dict: {
            "follow_up_prompt": str,  ← pertanyaan yang siap diucapkan
            "new_angle"       : str,  ← deskripsi angle baru
            "source"          : "assessor" | "llm",
        }
    """
    # Gunakan saran Assessor jika tersedia (hemat API call)
    if assessor_suggestion and len(assessor_suggestion.strip()) > 10:
        logger.info(
            "[speaking_follow_up] Using assessor suggestion "
            "(skipping LLM call)"
        )
        return {
            "follow_up_prompt": assessor_suggestion,
            "new_angle":        "suggested by assessor",
            "source":           "assessor",
        }

    logger.info(
        f"[speaking_follow_up] Generating follow-up for topic='{main_topic}'"
    )

    try:
        result = _call_followup_llm(
            main_topic       = main_topic,
            latest_user_text = latest_user_text,
            previous_angles  = previous_angles or [],
        )

        result["source"] = "llm"
        logger.info(
            f"[speaking_follow_up] Done — "
            f"'{result.get('follow_up_prompt', '')[:60]}...'"
        )
        return result

    except Exception as e:
        log_error(
            error_type   = "llm_timeout",
            agent_name   = "speaking_follow_up",
            session_id   = session_id,
            context      = {"main_topic": main_topic, "error": str(e)},
            fallback_used = True,
        )
        logger.warning(
            "[speaking_follow_up] Failed after 3 retries — using generic fallback"
        )

        # Fallback generik — conversation tetap jalan
        return {
            "follow_up_prompt": (
                "That's a really interesting perspective! "
                "What do you think would be the most significant challenge "
                "related to what you just described?"
            ),
            "new_angle": "challenge/difficulty angle (fallback)",
            "source":    "fallback",
        }