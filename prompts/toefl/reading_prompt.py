"""
prompts/toefl/reading_prompt.py
---------------------------------
Prompt untuk TOEFL Reading Generator.

Two-step generation:
  Step 1: Generate PASSAGE — 400-450 kata, topik akademik
  Step 2: Generate SOAL berdasarkan passage yang sudah ada

Dipisah karena soal yang dibuat "setelah" passage jauh lebih
natural dan tidak bias dibanding dibuat bersamaan.

6 tipe soal wajib per passage (standar TOEFL ITP Reading):
  1. main_idea          — topik utama passage
  2. factual            — detail tersurat
  3. negative_factual   — "which is NOT mentioned"
  4. inference          — kesimpulan tidak tersurat
  5. vocabulary_in_context — makna kata dalam konteks
  6. pronoun_reference  — kata ganti merujuk ke apa
"""

# ── Step 1: Generate Passage ────────────────────────────────────────────────
READING_PASSAGE_SYSTEM_PROMPT = """You are an expert academic content writer creating \
reading passages for the TOEFL ITP exam.

Generate a single, coherent academic passage following these specifications:

## Passage Requirements
- Length      : 400-450 words (STRICT — count carefully)
- Organization: 3-4 paragraphs with clear topic sentences
- Register    : Academic, formal, but accessible to non-native speakers
- Content     : Factual, informative — no opinions or personal narrative
- Topic variety: Rotate across these domains:
    Natural sciences (biology, geology, astronomy, ecology)
    Social sciences (anthropology, psychology, sociology, economics)
    History & civilization
    Technology & innovation
    Arts & humanities

## Writing Quality Rules
- Each paragraph must have ONE clear main idea
- Include specific details, dates, numbers, names where appropriate
- Use varied sentence structures (simple, compound, complex)
- Avoid jargon that would be unknown to intermediate English learners
- Do NOT include any questions in the passage itself
- Do NOT use bullet points or numbered lists

## Output Format
Respond with valid JSON only:
{
  "title": "string — short descriptive title",
  "topic_domain": "string — e.g. 'Natural Sciences: Marine Biology'",
  "passage": "string — full 400-450 word passage",
  "word_count": integer
}"""


# ── Step 2: Generate Questions ───────────────────────────────────────────────
READING_QUESTIONS_SYSTEM_PROMPT = """You are an expert TOEFL ITP test designer \
creating reading comprehension questions.

You will be given a reading passage and must generate questions that test \
different comprehension skills.

## Required Question Types (ALL must be present)
1. main_idea         — "What is the main topic/purpose of the passage?"
2. factual           — Tests specific detail explicitly stated in passage
3. negative_factual  — "According to the passage, which is NOT/EXCEPT..."
4. inference         — Tests conclusion that can be drawn but isn't stated
5. vocabulary_in_context — "The word X in paragraph Y is closest in meaning to..."
6. pronoun_reference — "The word 'it/they/this' in paragraph Y refers to..."

## Question Quality Rules
- Questions must be answerable ONLY from the given passage
- Each question: exactly 4 options (A/B/C/D), exactly ONE correct
- Distractors must be plausible but clearly wrong to a careful reader
- Vocabulary questions: pick words that are NOT simple common words
- Pronoun questions: pick pronouns with non-obvious referents
- Do NOT ask about information not in the passage

## Output Format
Respond with valid JSON only:
{
  "questions": [
    {
      "question_number": 1,
      "question_type": "main_idea|factual|negative_factual|inference|vocabulary_in_context|pronoun_reference",
      "question_text": "string",
      "options": {"A": "string", "B": "string", "C": "string", "D": "string"},
      "correct_answer": "A|B|C|D",
      "difficulty": "medium|hard"
    }
  ]
}"""


def build_passage_prompt(
    passage_number: int,
    total_passages: int,
    used_domains: list[str],
) -> str:
    """
    Bangun prompt untuk Step 1: generate passage.

    Args:
        passage_number : Nomor passage saat ini (1-based)
        total_passages : Total passage yang akan dibuat dalam sesi ini
        used_domains   : Domain yang sudah dipakai (hindari repetisi)
    """
    avoid_section = ""
    if used_domains:
        avoid_section = (
            f"\nAvoid these domains already used: "
            f"{', '.join(used_domains)}"
        )

    return f"""Generate academic reading passage #{passage_number} of {total_passages}.

Requirements:
- 400-450 words
- Choose a domain different from: {', '.join(used_domains) if used_domains else 'none used yet'}
{avoid_section}

Respond with JSON only."""


def build_questions_prompt(
    passage_title: str,
    passage_text: str,
    questions_per_passage: int,
) -> str:
    """
    Bangun prompt untuk Step 2: generate soal dari passage.

    Passage di-inject penuh ke prompt — LLM harus membuat soal
    berdasarkan isi yang sudah ada, bukan mengarang sendiri.

    Args:
        passage_title         : Judul passage (untuk context)
        passage_text          : Full text passage dari Step 1
        questions_per_passage : Total soal yang dibutuhkan (min 6 untuk cover semua type)
    """
    extra_q = max(0, questions_per_passage - 6)
    extra_section = ""
    if extra_q > 0:
        extra_section = (
            f"\nAfter the 6 required types, add {extra_q} more question(s) "
            f"of any type (prefer factual or inference)."
        )

    return f"""Generate reading comprehension questions for this passage.

## Passage Title
{passage_title}

## Passage Text
{passage_text}

## Requirements
- Generate exactly {questions_per_passage} questions
- Must include ALL 6 required types (main_idea, factual, negative_factual, \
inference, vocabulary_in_context, pronoun_reference)
{extra_section}

Base ALL questions strictly on the passage content above.
Respond with JSON only."""