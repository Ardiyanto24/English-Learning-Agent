"""
agents/quiz_tutor/generator.py
-------------------------------
Grammar Tutor Generator Agent.

Model     : claude-sonnet (SONNET_MODEL) — reasoning mendalam dibutuhkan
            untuk membuat soal isian (open-ended) yang menguji pemahaman
            konsep grammar secara eksplisit, bukan sekadar pilihan ganda.

Input     : planner_output (dict) — output dari Tutor Planner, berisi
            plan per topik dengan question_count dan type_distribution.
            rag_context (str) — materi referensi dari ChromaDB.

Output    : {"questions": [list soal]} — setiap soal memiliki field:
            topic, question_type, question_text, reference_answer, input_type.

Error handling:
- @retry_llm     : max 3x retry dengan exponential backoff (2s → 4s → 8s)
- RAG failure    : fallback ke nama topik langsung, is_fallback=True
- JSON parse err : ValueError dilempar, di-catch oleh @retry_llm untuk retry
- Semua retry habis : raise RuntimeError, batalkan sesi, log error
"""

from typing import Optional

import anthropic
from dotenv import load_dotenv

from config.settings import SONNET_MODEL
from modules.rag.retriever import format_context_for_prompt, retrieve
from prompts.quiz_tutor.generator_prompt import (
    TUTOR_GENERATOR_SYSTEM_PROMPT,
    build_tutor_generator_prompt,
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

    Untuk setiap topik, memanggil ChromaDB via retrieve() dan memformat
    hasilnya. Jika retrieve gagal atau menghasilkan chunks kosong,
    fallback ke nama topik sebagai konteks minimal.

    Args:
        topics: List nama topik grammar yang akan di-generate soalnya.

    Returns:
        (context_string, is_fallback)
        is_fallback=True jika satu atau lebih topik menggunakan fallback.
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
                f"[tutor_generator] RAG retrieve failed for '{topic}': {e} "
                f"— using topic name as fallback"
            )
            all_chunks.append(f"## {topic}\n[Topic: {topic}]")
            rag_failed = True

    if rag_failed:
        log_error(
            error_type="rag_failure",
            agent_name="tutor_generator",
            context={"topics": topics},
            fallback_used=True,
        )

    return "\n\n".join(all_chunks), rag_failed


def _parse_generator_response(raw: str) -> dict:
    """
    Parse dan validasi JSON response dari Tutor Generator.

    Berbeda dari Quiz Generator: field wajib per soal adalah
    topic, question_type, question_text, reference_answer, input_type —
    tidak ada options atau correct_answer karena semua soal isian.

    Args:
        raw: String response mentah dari LLM.

    Returns:
        Dict terstruktur dengan key 'questions'.

    Raises:
        ValueError jika struktur tidak valid atau field wajib hilang.
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

    required = {"topic", "question_type", "question_text", "reference_answer", "input_type"}
    for i, q in enumerate(parsed["questions"]):
        missing = required - set(q.keys())
        if missing:
            raise ValueError(f"Question[{i}] missing fields: {missing}")
        # Pastikan tidak ada field wajib yang nilainya kosong
        for field in required:
            if not q.get(field, "").strip():
                raise ValueError(f"Question[{i}] field '{field}' is empty")

    return parsed


@retry_llm
def _call_generator_llm(planner_output: dict, rag_context: str) -> dict:
    """
    Panggil Claude Sonnet untuk generate soal Grammar Tutor.
    Di-wrap @retry_llm: max 3x retry, exponential backoff.

    Args:
        planner_output: Output dari Tutor Planner — berisi plan per topik
                        dengan question_count dan type_distribution.
        rag_context   : Materi referensi dari ChromaDB untuk semua topik.

    Returns:
        Dict dengan key 'questions' berisi list soal tervalidasi.
    """
    user_prompt = build_tutor_generator_prompt(planner_output, rag_context)

    response = _get_client().messages.create(
        model=SONNET_MODEL,
        max_tokens=4096,
        system=TUTOR_GENERATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = response.content[0].text
    return _parse_generator_response(raw)