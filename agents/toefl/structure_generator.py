"""
agents/toefl/structure_generator.py
-------------------------------------
TOEFL Structure Generator Agent.

Menggunakan RAG persis seperti Quiz Generator — retrieve materi
grammar dari knowledge base, inject ke prompt sebagai referensi.

Jika RAG gagal total: fallback inject nama topik grammar langsung
(LLM generate dari pengetahuannya sendiri).

Generate Part A dan Part B dalam satu LLM call untuk efisiensi.
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from prompts.toefl.structure_prompt import (
    STRUCTURE_GENERATOR_SYSTEM_PROMPT,
    build_structure_prompt,
)
from modules.rag.retriever import retrieve, format_context_for_prompt
from utils.logger import log_error, logger
from utils.retry import retry_llm
from config.settings import SONNET_MODEL

load_dotenv()

# Topik grammar yang di-query ke RAG untuk Structure section
_STRUCTURE_GRAMMAR_TOPICS = [
    "Subject-Verb Agreement",
    "Passive Voice",
    "Verb Tense Consistency",
    "Gerund vs Infinitive",
    "Parallel Structure",
    "Conditional Clauses",
    "Noun Clause",
    "Adjective Clause",
]

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _get_rag_context() -> tuple[str, bool]:
    """
    Retrieve materi grammar dari knowledge base.
    Query beberapa topik sekaligus dan gabungkan hasilnya.

    Returns:
        (context_string, is_fallback)
    """
    chunks    = []
    failed    = 0

    for topic in _STRUCTURE_GRAMMAR_TOPICS:
        try:
            result  = retrieve(query=topic, topic=topic)
            context = format_context_for_prompt(result)
            if context:
                chunks.append(f"## {topic}\n{context}")
            else:
                chunks.append(f"## {topic}\n[Grammar topic: {topic}]")
                failed += 1
        except Exception as e:
            logger.warning(
                f"[structure_generator] RAG failed for '{topic}': {e}"
            )
            chunks.append(f"## {topic}\n[Grammar topic: {topic}]")
            failed += 1

    is_fallback = failed == len(_STRUCTURE_GRAMMAR_TOPICS)

    if is_fallback:
        log_error(
            error_type    = "rag_failure",
            agent_name    = "structure_generator",
            context       = {"all_topics_failed": True},
            fallback_used = True,
        )
        logger.warning(
            "[structure_generator] All RAG queries failed — "
            "using topic names as fallback"
        )

    return "\n\n".join(chunks), is_fallback


def _parse_response(raw: str, part_a_count: int, part_b_count: int) -> dict:
    """
    Parse dan validasi JSON dari Structure Generator.

    Raises:
        ValueError jika struktur tidak valid atau jumlah soal tidak sesuai
    """
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text  = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    text = text.strip()

    parsed = json.loads(text)

    if "part_a" not in parsed or "part_b" not in parsed:
        raise ValueError("Response missing 'part_a' or 'part_b'")

    # Validasi jumlah minimum (toleransi -2 soal)
    actual_a = len(parsed["part_a"])
    actual_b = len(parsed["part_b"])
    if actual_a < max(1, part_a_count - 2):
        raise ValueError(
            f"Part A: expected ~{part_a_count}, got {actual_a}"
        )
    if actual_b < max(1, part_b_count - 2):
        raise ValueError(
            f"Part B: expected ~{part_b_count}, got {actual_b}"
        )

    # Validasi field per soal
    required = {"question_text", "options", "correct_answer"}
    for part_key in ("part_a", "part_b"):
        for i, q in enumerate(parsed[part_key]):
            missing = required - set(q.keys())
            if missing:
                raise ValueError(
                    f"{part_key}[{i}] missing fields: {missing}"
                )
            if q.get("correct_answer") not in ("A", "B", "C", "D"):
                raise ValueError(
                    f"{part_key}[{i}] invalid correct_answer: "
                    f"{q.get('correct_answer')}"
                )

    return parsed


@retry_llm
def _call_llm(
    part_a_count: int,
    part_b_count: int,
    rag_context:  str,
) -> dict:
    """Panggil Claude Sonnet untuk generate soal Structure."""
    user_prompt = build_structure_prompt(
        part_a_count = part_a_count,
        part_b_count = part_b_count,
        rag_context  = rag_context,
    )

    client = _get_client()
    response = client.messages.create(
        model      = SONNET_MODEL,
        max_tokens = 4096,
        system     = STRUCTURE_GENERATOR_SYSTEM_PROMPT,
        messages   = [{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_response(raw, part_a_count, part_b_count)


def run_generator(structure_dist: dict) -> dict:
    """
    Jalankan TOEFL Structure Generator Agent.

    Args:
        structure_dist: Output Planner untuk section structure:
                        {total, part_a, part_b}

    Returns:
        dict: {
            "part_a"          : [list of question dicts],
            "part_b"          : [list of question dicts],
            "total_questions" : int,
            "rag_fallback"    : bool,
        }

    Raises:
        RuntimeError jika LLM gagal setelah 3x retry
    """
    part_a_count = structure_dist.get("part_a", 15)
    part_b_count = structure_dist.get("part_b", 25)

    logger.info(
        f"[structure_generator] Generating "
        f"Part A: {part_a_count}, Part B: {part_b_count}"
    )

    # Retrieve RAG context
    rag_context, is_fallback = _get_rag_context()

    try:
        result = _call_llm(part_a_count, part_b_count, rag_context)

        # Trim ke jumlah yang dibutuhkan jika LLM generate lebih
        result["part_a"] = result["part_a"][:part_a_count]
        result["part_b"] = result["part_b"][:part_b_count]

        total = len(result["part_a"]) + len(result["part_b"])
        result["total_questions"] = total
        result["rag_fallback"]    = is_fallback

        logger.info(
            f"[structure_generator] Done — "
            f"A:{len(result['part_a'])} B:{len(result['part_b'])} "
            f"rag_fallback={is_fallback}"
        )
        return result

    except Exception as e:
        log_error(
            error_type    = "llm_timeout",
            agent_name    = "structure_generator",
            context       = {
                "part_a_count": part_a_count,
                "part_b_count": part_b_count,
                "error":        str(e),
            },
            fallback_used = False,
        )
        raise RuntimeError(
            f"Structure Generator gagal setelah 3x retry: {e}"
        ) from e