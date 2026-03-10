"""
agents/quiz/generator.py
-------------------------
Quiz Generator Agent.

Perbedaan dari Vocab Generator:
1. Menggunakan Claude Sonnet (bukan Haiku)
2. Menerima RAG context sebagai referensi materi
3. RAG failure ditangani dengan fallback ke nama topik

Input  : planner_output (dict) dari Quiz Planner
Output : {"questions": [list soal]}

Error handling:
- @retry_llm: max 3x retry untuk LLM call
- RAG failure: fallback inject nama topik langsung
- JSON parse error: retry dengan instruksi lebih ketat
- Setelah semua retry habis: raise RuntimeError
"""

import json
from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import SONNET_MODEL
from modules.rag.retriever import format_context_for_prompt, retrieve
from prompts.quiz.generator_prompt import (
    QUIZ_GENERATOR_SYSTEM_PROMPT,
    build_generator_prompt,
)
from utils.logger import log_error, logger
from utils.retry import retry_llm

load_dotenv()

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _get_rag_context(topics: list[str]) -> tuple[str, bool]:
    """
    Retrieve RAG context untuk semua topik yang akan di-generate.

    Mencoba retrieve untuk setiap topik dan gabungkan hasilnya.
    Jika RAG gagal total, fallback ke nama topik sebagai konteks.

    Returns:
        (context_string, is_fallback)
        is_fallback=True jika menggunakan fallback
    """
    if not topics:
        return "No topics specified.", True

    all_chunks = []
    rag_failed = False

    for topic in topics:
        try:
            result = retrieve(query=topic, topic=topic)
            chunks = format_context_for_prompt(result)
            if chunks:
                all_chunks.append(f"## {topic}\n{chunks}")
            else:
                all_chunks.append(f"## {topic}\n[Topic: {topic}]")
                rag_failed = True
        except Exception as e:
            logger.warning(
                f"[quiz_generator] RAG retrieve failed for '{topic}': {e} "
                f"— using topic name as fallback"
            )
            all_chunks.append(f"## {topic}\n[Topic: {topic}]")
            rag_failed = True

    if rag_failed:
        log_error(
            error_type="rag_failure",
            agent_name="quiz_generator",
            context={"topics": topics},
            fallback_used=True,
        )

    return "\n\n".join(all_chunks), rag_failed


def _parse_generator_response(raw: str) -> dict:
    """
    Parse JSON response dari Quiz Generator.

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

    if "questions" not in parsed:
        raise ValueError("Response missing 'questions' key")
    if not isinstance(parsed["questions"], list):
        raise ValueError("'questions' must be a list")
    if len(parsed["questions"]) == 0:
        raise ValueError("'questions' list is empty")

    # Validasi field wajib setiap soal
    required = {"topic", "format", "difficulty", "question_text",
                "options", "correct_answer"}
    for i, q in enumerate(parsed["questions"]):
        missing = required - set(q.keys())
        if missing:
            raise ValueError(f"Question[{i}] missing fields: {missing}")

    return parsed


@retry_llm
def _call_generator_llm(planner_output: dict, rag_context: str) -> dict:
    """
    Panggil Claude Sonnet untuk generate soal.
    Di-wrap @retry_llm: max 3x, exponential backoff.
    """
    user_prompt = build_generator_prompt(planner_output, rag_context)

    client = _get_client()
    response = client.messages.create(
        model=SONNET_MODEL,
        max_tokens=4096,   # Sonnet, soal grammar butuh token lebih banyak
        system=QUIZ_GENERATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_generator_response(raw)


def run_generator(planner_output: dict) -> dict:
    """
    Jalankan Quiz Generator Agent.

    Flow:
    1. Retrieve RAG context untuk topik dari planner
    2. Panggil Claude Sonnet dengan context + instruksi planner
    3. Parse dan return hasil

    Args:
        planner_output: Output dari Quiz Planner Agent

    Returns:
        dict: {"questions": [list soal]}

    Raises:
        RuntimeError jika gagal setelah semua retry habis
    """
    topics = planner_output.get("topics", [])
    total  = planner_output.get("total_questions", 10)

    logger.info(
        f"[quiz_generator] Generating {total} questions "
        f"for topics={topics}"
    )

    # Step 1: Retrieve RAG context
    rag_context, is_fallback = _get_rag_context(topics)

    if is_fallback:
        logger.warning(
            "[quiz_generator] Using fallback context "
            "(topic names only, no KB material)"
        )

    # Step 2: Call LLM
    try:
        result = _call_generator_llm(planner_output, rag_context)
        logger.info(
            f"[quiz_generator] Generated {len(result.get('questions', []))} questions"
            f"{' (with RAG fallback)' if is_fallback else ''}"
        )
        return result

    except Exception as e:
        log_error(
            error_type="llm_timeout",
            agent_name="quiz_generator",
            context={
                "topics": topics,
                "total_questions": total,
                "rag_fallback": is_fallback,
                "error": str(e),
            },
            fallback_used=False,
        )
        raise RuntimeError(
            f"Quiz Generator gagal setelah 3x retry: {e}"
        ) from e