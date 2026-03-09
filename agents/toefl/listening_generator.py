"""
agents/toefl/listening_generator.py
-------------------------------------
TOEFL Listening Generator Agent.

Flow per part:
  1. Generate script + soal via Claude Sonnet
  2. Untuk setiap script → panggil TTS multi-voice
  3. Simpan audio ke temp_audio/
  4. Return list item dengan script + audio_path + soal

Dipanggil 3 kali (sekali per part A, B, C) oleh orchestrator TOEFL.

Error handling:
  - LLM gagal    → @retry_llm max 3x → raise RuntimeError (batalkan section)
  - TTS gagal    → audio_path = None, UI fallback ke teks
  - Parse error  → retry termasuk dalam @retry_llm

Audio file naming: toefl_{session_id}_L{part}_{item_id}.wav
Disimpan di temp_audio/ → dibersihkan cleanup_temp_audio() setelah sesi
"""

import json
import time
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.toefl.listening_prompt import (
    LISTENING_GENERATOR_SYSTEM_PROMPT,
    build_listening_prompt,
)
from modules.audio.tts import generate_speech_multivoice
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL

load_dotenv()

TEMP_AUDIO_DIR = Path("temp_audio")

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _ensure_temp_dir():
    TEMP_AUDIO_DIR.mkdir(exist_ok=True)


# ===================================================
# Helpers: soal per item berdasarkan part
# ===================================================
def _questions_per_item(part: str) -> int:
    """Jumlah soal per conversation/talk sesuai standar TOEFL ITP."""
    return {"A": 1, "B": 3, "C": 4}.get(part, 1)


def _item_count_from_distribution(part: str, dist: dict) -> int:
    """
    Hitung jumlah conversation/talk dari distribusi Planner.

    Part A: setiap item = 1 soal → item_count = part_a total
    Part B: setiap item = ~3 soal → item_count = part_b // 3 (min 1)
    Part C: setiap item = ~4 soal → item_count = part_c // 4 (min 1)
    """
    if part == "A":
        return dist.get("part_a", 15)
    elif part == "B":
        count = dist.get("part_b", 8)
        return max(1, count // 3)
    elif part == "C":
        count = dist.get("part_c", 12)
        return max(1, count // 4)
    return 1


# ===================================================
# TTS: generate dan simpan audio
# ===================================================
def _generate_audio(
    script: str,
    session_id: str,
    part: str,
    item_id: int,
) -> Optional[str]:
    """
    Convert script ke audio menggunakan TTS multi-voice.
    Simpan ke temp_audio/ dan return path.

    Returns:
        Path string jika berhasil, None jika TTS gagal.
    """
    _ensure_temp_dir()

    audio_bytes = generate_speech_multivoice(script)

    if not audio_bytes:
        logger.warning(
            f"[listening_generator] TTS failed for "
            f"part={part} item={item_id} — UI will fallback to text"
        )
        log_error(
            error_type    = "tts_failure",
            agent_name    = "listening_generator",
            context       = {"session_id": session_id, "part": part, "item_id": item_id},
            fallback_used = True,
        )
        return None

    filename   = f"toefl_{session_id}_L{part}_{item_id:02d}.mp3"
    audio_path = TEMP_AUDIO_DIR / filename

    try:
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        logger.debug(f"[listening_generator] Audio saved: {audio_path}")
        return str(audio_path)
    except Exception as e:
        logger.error(f"[listening_generator] Failed to save audio: {e}")
        return None


# ===================================================
# Parse LLM response
# ===================================================
def _parse_response(raw: str, part: str) -> list[dict]:
    """
    Parse JSON dari LLM dan validasi struktur.

    Returns:
        List of item dicts dengan "script" dan "questions"

    Raises:
        ValueError jika struktur tidak valid
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    items = parsed.get("items", [])
    if not items:
        raise ValueError(f"No items in Part {part} response")

    # Validasi setiap item
    for i, item in enumerate(items):
        if "script" not in item:
            raise ValueError(f"Item {i} missing 'script'")
        if "questions" not in item or not item["questions"]:
            raise ValueError(f"Item {i} missing 'questions'")

        # Validasi tag speaker ada di script
        script = item["script"]
        if part in ("A", "B"):
            if "[SPEAKER_A]" not in script:
                raise ValueError(
                    f"Item {i} Part {part} script missing [SPEAKER_A] tag"
                )
        elif part == "C":
            if "[NARRATOR]" not in script:
                raise ValueError(
                    f"Item {i} Part C script missing [NARRATOR] tag"
                )

        # Validasi setiap soal
        for j, q in enumerate(item["questions"]):
            required = {"question_text", "options", "correct_answer"}
            missing = required - set(q.keys())
            if missing:
                raise ValueError(
                    f"Item {i} Question {j} missing fields: {missing}"
                )
            if q.get("correct_answer") not in ("A", "B", "C", "D"):
                raise ValueError(
                    f"Item {i} Question {j} invalid correct_answer: "
                    f"{q.get('correct_answer')}"
                )

    return items


# ===================================================
# LLM call
# ===================================================
@retry_llm
def _call_llm(part: str, item_count: int, questions_per_item: int) -> list[dict]:
    """Panggil Claude Sonnet untuk generate script + soal."""
    user_prompt = build_listening_prompt(
        part               = part,
        item_count         = item_count,
        questions_per_item = questions_per_item,
    )

    client = _get_client()
    response = client.messages.create(
        model      = SONNET_MODEL,
        max_tokens = 4096,
        system     = LISTENING_GENERATOR_SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_response(raw, part)


# ===================================================
# Main: run_generator
# ===================================================
def run_generator(
    listening_dist: dict,
    session_id: str,
) -> dict:
    """
    Jalankan TOEFL Listening Generator untuk semua part (A, B, C).

    Args:
        listening_dist: Output dari Planner untuk section listening:
                        {total, part_a, part_b, part_c}
        session_id    : ID sesi untuk naming audio files

    Returns:
        dict: {
            "part_a": [list of items],
            "part_b": [list of items],
            "part_c": [list of items],
            "total_questions": int,
            "tts_available"  : bool,  ← False jika semua TTS gagal
        }

        Setiap item: {
            "item_id"      : int,
            "part"         : "A"|"B"|"C",
            "script"       : str,
            "audio_path"   : str|None,
            "questions"    : [list of question dicts],
        }

    Raises:
        RuntimeError jika LLM gagal untuk satu part setelah 3x retry
    """
    result      = {"part_a": [], "part_b": [], "part_c": []}
    tts_success = 0
    tts_total   = 0
    total_q     = 0

    for part_key, part_label in [("part_a", "A"), ("part_b", "B"), ("part_c", "C")]:
        item_count         = _item_count_from_distribution(part_label, listening_dist)
        questions_per_item = _questions_per_item(part_label)

        logger.info(
            f"[listening_generator] Generating Part {part_label}: "
            f"{item_count} item(s) × {questions_per_item} question(s)"
        )

        try:
            items = _call_llm(part_label, item_count, questions_per_item)
        except Exception as e:
            log_error(
                error_type    = "llm_timeout",
                agent_name    = "listening_generator",
                context       = {
                    "session_id": session_id,
                    "part":       part_label,
                    "error":      str(e),
                },
                fallback_used = False,
            )
            raise RuntimeError(
                f"Listening Generator Part {part_label} gagal setelah 3x retry: {e}"
            ) from e

        # Generate audio untuk setiap item
        enriched_items = []
        for idx, item in enumerate(items):
            item_id    = idx + 1
            tts_total += 1

            audio_path = _generate_audio(
                script     = item["script"],
                session_id = session_id,
                part       = part_label,
                item_id    = item_id,
            )

            if audio_path:
                tts_success += 1

            enriched_items.append({
                "item_id":    item_id,
                "part":       part_label,
                "script":     item["script"],
                "audio_path": audio_path,
                "questions":  item["questions"],
            })

            total_q += len(item["questions"])

        result[part_key] = enriched_items
        logger.info(
            f"[listening_generator] Part {part_label} done: "
            f"{len(enriched_items)} items, {total_q} questions so far"
        )

    result["total_questions"] = total_q
    result["tts_available"]   = tts_success > 0

    logger.info(
        f"[listening_generator] Complete — "
        f"total_q={total_q} "
        f"tts={tts_success}/{tts_total}"
    )

    return result