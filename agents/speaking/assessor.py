"""
agents/speaking/assessor.py
-----------------------------
Conversation Assessor Agent.

Memutuskan alur conversation setelah setiap giliran user bicara.
Menggunakan Haiku karena tugasnya keputusan binary — tidak butuh
reasoning berat, tapi harus cepat agar conversation tidak terputus.

SLIDING WINDOW: hanya 5 exchange terakhir yang dikirim ke LLM,
bukan full transcript. Jika entry pertama window adalah giliran user,
window diperluas 1 entry agar setelah trim tetap 5 entry.
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.speaking.assessor_prompt import (
    SPEAKING_ASSESSOR_SYSTEM_PROMPT,
    build_assessor_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import HAIKU_MODEL

load_dotenv()

# Batas exchange per sub-mode
PROMPTED_RESPONSE_MAX = 3
CONVERSATION_PHASE2_MIN = 10
CONVERSATION_HARD_STOP = 15

# Window size untuk sliding window
WINDOW_SIZE = 5

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _build_sliding_window(
    full_history: list[dict],
) -> list[dict]:
    """
    Ambil 5 exchange terakhir dari full history.

    Jika entry pertama window adalah giliran user, ambil 1 entry
    ekstra dari awal agar setelah trim tetap 5 entry — konteks
    tidak berkurang akibat pemotongan role.
    """
    if len(full_history) <= WINDOW_SIZE:
        return full_history

    # Ambil WINDOW_SIZE + 1 agar ada cadangan jika entry pertama di-trim
    window = full_history[-(WINDOW_SIZE + 1) :]

    # Pastikan window mulai dari giliran AI (bukan user)
    if window and window[0].get("role") == "user":
        window = window[1:]

    return window


def _check_hard_limits(
    sub_mode: str,
    exchange_count: int,
) -> Optional[dict]:
    """
    Cek apakah hard limit exchange sudah tercapai.
    Jika ya, return stop decision langsung tanpa memanggil LLM.

    Returns:
        dict jika hard limit tercapai, None jika belum
    """
    if sub_mode == "prompted_response" and exchange_count >= PROMPTED_RESPONSE_MAX:
        logger.info(f"[speaking_assessor] Hard stop: prompted_response " f"reached {exchange_count} exchanges")
        return {
            "decision": "stop",
            "reason": f"Batas maksimum {PROMPTED_RESPONSE_MAX}x exchange untuk Prompted Response telah tercapai.",
            "suggested_followup": None,
        }

    if sub_mode == "conversation_practice" and exchange_count >= CONVERSATION_HARD_STOP:
        logger.info(f"[speaking_assessor] Hard stop: conversation_practice " f"reached {exchange_count} exchanges")
        return {
            "decision": "stop",
            "reason": f"Batas maksimum {CONVERSATION_HARD_STOP}x exchange untuk Conversation Practice telah tercapai.",
            "suggested_followup": None,
        }

    return None


def _parse_assessor_response(raw: str) -> dict:
    """Parse JSON response dari Assessor."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    required = {"decision", "reason"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Assessor response missing fields: {missing}")

    valid_decisions = {"continue", "stop", "new_subtopic"}
    if parsed.get("decision") not in valid_decisions:
        raise ValueError(f"Invalid decision: {parsed.get('decision')}")

    # Pastikan suggested_followup ada (bisa None)
    if "suggested_followup" not in parsed:
        parsed["suggested_followup"] = None

    return parsed


@retry_llm
def _call_assessor_llm(
    sub_mode: str,
    exchange_count: int,
    conversation_window: list[dict],
    main_topic: str,
    latest_transcript: str,
) -> dict:
    """Panggil Claude Haiku untuk assess conversation."""
    user_prompt = build_assessor_prompt(
        sub_mode=sub_mode,
        exchange_count=exchange_count,
        conversation_window=conversation_window,
        main_topic=main_topic,
        latest_transcript=latest_transcript,
    )

    client = _get_client()
    response = client.messages.create(
        model=HAIKU_MODEL,
        max_tokens=256,
        system=SPEAKING_ASSESSOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_assessor_response(raw)


def run_assessor(
    sub_mode: str,
    exchange_count: int,
    full_history: list[dict],
    main_topic: str,
    latest_transcript: str,
) -> dict:
    """
    Jalankan Conversation Assessor Agent.

    Args:
        sub_mode           : "prompted_response" | "conversation_practice"
        exchange_count     : Total exchange yang sudah terjadi (termasuk yang ini)
        full_history       : Seluruh history conversation:
                             [{"role": "ai"|"user", "text": str}, ...]
        main_topic         : Topik utama sesi
        latest_transcript  : Transkrip jawaban user terbaru (sudah ada di full_history
                             tapi dikirim terpisah untuk emphasis)

    Returns:
        dict: {
            "decision"          : "continue" | "stop" | "new_subtopic",
            "reason"            : str,
            "suggested_followup": str | None,
        }
    """
    logger.info(f"[speaking_assessor] Assessing — " f"sub_mode={sub_mode} exchange={exchange_count}")

    # 1. Cek hard limit dulu (tanpa memanggil LLM)
    hard_stop = _check_hard_limits(sub_mode, exchange_count)
    if hard_stop:
        return hard_stop

    # 2. Bangun sliding window
    window = _build_sliding_window(full_history)
    logger.debug(f"[speaking_assessor] Sliding window: " f"{len(full_history)} total → {len(window)} sent")

    # 3. Panggil LLM
    try:
        result = _call_assessor_llm(
            sub_mode=sub_mode,
            exchange_count=exchange_count,
            conversation_window=window,
            main_topic=main_topic,
            latest_transcript=latest_transcript,
        )

        logger.info(f"[speaking_assessor] Decision: {result.get('decision')} " f"— {result.get('reason', '')[:60]}")

        # Fase 1 guard: conversation_practice < 10 exchange → tidak boleh stop
        if sub_mode == "conversation_practice" and exchange_count < CONVERSATION_PHASE2_MIN and result.get("decision") == "stop":
            logger.warning("[speaking_assessor] Overriding 'stop' in Phase 1 " "→ changing to 'new_subtopic'")
            result["decision"] = "new_subtopic"
            result["reason"] += " [Override: Phase 1 belum selesai]"
            result["suggested_followup"] = result.get("suggested_followup") or (
                "That's a great point! Let me ask you something related — " "what do you think would be the most important factor in this situation?"
            )

        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="speaking_assessor",
            context={
                "sub_mode": sub_mode,
                "exchange_count": exchange_count,
                "error": str(e),
            },
            fallback_used=True,
        )
        logger.warning("[speaking_assessor] Failed — using fallback decision")

        # Fallback: Fase 1 → new_subtopic, Fase 2 → stop, prompted_response → continue
        if sub_mode == "conversation_practice":
            if exchange_count < CONVERSATION_PHASE2_MIN:
                # Fase 1 — belum boleh stop
                return {
                    "decision": "new_subtopic",
                    "reason": "Assessor tidak tersedia — lanjut dengan sub-topik baru.",
                    "suggested_followup": "That's interesting! What about approaching this from a different angle — how do you think this topic affects people in everyday life?",
                }
            else:
                # Fase 2 — aman untuk stop
                return {
                    "decision": "stop",
                    "reason": "Assessor tidak tersedia — sesi dihentikan.",
                    "suggested_followup": None,
                }
        else:
            # prompted_response — exchange belum mencapai hard limit, lanjut
            return {
                "decision": "continue",
                "reason": "Assessor tidak tersedia — lanjut conversation.",
                "suggested_followup": None,
            }
