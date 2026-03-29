"""
prompts/toefl/structure_prompt.py
-----------------------------------
Prompt untuk TOEFL Structure & Written Expression Generator.

TOEFL ITP Structure terdiri dari 2 part:

Part A — Structure (Sentence Completion)
  Format : Kalimat tidak lengkap dengan 1 blank, pilih jawaban yang tepat
  Contoh : "The committee _____ its decision yesterday."
           A) have announced  B) announced  C) announces  D) announcing
  Difficulty: easy-medium

Part B — Written Expression (Error Identification)
  Format : Kalimat lengkap dengan 4 bagian underlined (A/B/C/D), pilih yang salah
  Contoh : "The students has (A) finished their (B) assignments before (C) the deadline arrive (D)."
  Difficulty: medium-hard

Keduanya di-generate dalam satu LLM call untuk efisiensi.
RAG context di-inject sebagai referensi materi grammar.
"""

import itertools

STRUCTURE_GENERATOR_SYSTEM_PROMPT = """You are an expert TOEFL ITP test content creator \
specializing in the Structure and Written Expression section.

Your task is to generate authentic TOEFL-style grammar questions for both parts.

## Part A — Structure (Sentence Completion)
Format: An incomplete sentence with one blank. Student chooses the correct completion.

Rules:
- Test ONE specific grammar point per question
- All 4 options must be grammatically plausible at first glance
- Only ONE option is grammatically correct in context
- Sentence should be academic or semi-formal in register
- Difficulty: easy to medium (fundamental grammar rules)
- Grammar areas: tense, subject-verb agreement, articles, prepositions,
  clause connectors, verb forms, parallel structure

Example:
{
  "question_text": "The experiment _____ conducted by a team of researchers last year.",
  "options": {"A": "is", "B": "was", "C": "were", "D": "has been"},
  "correct_answer": "B",
  "grammar_focus": "passive voice — simple past",
  "difficulty": "easy"
}

## Part B — Written Expression (Error Identification)
Format: A complete sentence with 4 underlined segments labeled (A)(B)(C)(D).
Student identifies which segment contains an error.

Rules:
- EXACTLY ONE segment contains an error — the others must be correct
- The error must be a real grammar mistake (not punctuation or spelling)
- Label segments clearly: (A), (B), (C), (D) embedded in the sentence
- Difficulty: medium to hard (subtle grammar errors)
- Error types: wrong tense, subject-verb disagreement, wrong word form,
  incorrect preposition, parallel structure violation, article error

Example:
{
  "question_text": "The committee have (A) agreed to postpone (B) their meeting until (C) the new director arrive (D).",
  "options": {"A": "have", "B": "postpone", "C": "until", "D": "arrive"},
  "correct_answer": "A",
  "error_explanation": "Subject-verb agreement: 'The committee' is singular → 'has'",
  "difficulty": "medium"
}

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "part_a": [
    {
      "question_number": 1,
      "question_text": "string with _____ for blank",
      "options": {"A": "string", "B": "string", "C": "string", "D": "string"},
      "correct_answer": "A|B|C|D",
      "grammar_focus": "string",
      "difficulty": "easy|medium"
    }
  ],
  "part_b": [
    {
      "question_number": 1,
      "question_text": "string with (A)(B)(C)(D) segments",
      "options": {"A": "string", "B": "string", "C": "string", "D": "string"},
      "correct_answer": "A|B|C|D",
      "error_explanation": "string",
      "difficulty": "medium|hard"
    }
  ]
}"""


def build_structure_prompt(
    part_a_count: int,
    part_b_count: int,
    rag_context: str,
) -> str:
    """
    Bangun user prompt untuk Structure Generator.

    RAG context di-inject sebagai referensi grammar — LLM didorong
    untuk membuat soal yang relevan dengan materi yang sudah ada di KB.

    Args:
        part_a_count : Jumlah soal Part A yang dibutuhkan
        part_b_count : Jumlah soal Part B yang dibutuhkan
        rag_context  : String dari format_context_for_prompt() atau fallback nama topik

    Returns:
        String user prompt siap dikirim ke LLM
    """
    # Tentukan distribusi grammar focus agar tidak monoton
    grammar_areas_a = [
        "tense and aspect",
        "subject-verb agreement",
        "passive voice",
        "article usage",
        "prepositions",
        "clause connectors",
        "verb forms (gerund/infinitive)",
        "parallel structure",
    ]
    grammar_areas_b = [
        "subject-verb agreement error",
        "wrong tense form",
        "incorrect word form (adjective/adverb)",
        "parallel structure violation",
        "article error",
        "preposition error",
        "pronoun agreement",
        "comparative/superlative form",
    ]

    # Ambil distribusi yang cukup
    cycle_a = list(itertools.islice(itertools.cycle(grammar_areas_a), part_a_count))
    cycle_b = list(itertools.islice(itertools.cycle(grammar_areas_b), part_b_count))

    return f"""Generate TOEFL ITP Structure questions based on the grammar reference below.

## Grammar Reference Material
{rag_context}

## Requirements
- Part A (Structure): {part_a_count} questions
- Part B (Written Expression): {part_b_count} questions

## Grammar Focus Distribution
Part A topics (vary in this order): {', '.join(cycle_a)}
Part B error types (vary in this order): {', '.join(cycle_b)}

## Quality Checklist
- [ ] Every Part A question has exactly one blank (_____)
- [ ] Every Part B question has exactly four labeled segments (A)(B)(C)(D)
- [ ] Only ONE correct answer per question
- [ ] All distractors are plausible but clearly wrong to a grammar expert
- [ ] No repeated grammar focus within the same part

Respond with JSON only."""
