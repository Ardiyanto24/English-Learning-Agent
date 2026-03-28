"""
prompts/toefl/listening_prompt.py
-----------------------------------
Prompt untuk TOEFL Listening Generator.

TOEFL ITP Listening terdiri dari 3 part dengan karakter berbeda:

Part A — Short Conversations
  Format : Dialog singkat 2 speaker, 4-6 baris
  Topik  : Kehidupan kampus, jadwal, pendapat singkat, situasi sehari-hari
  Soal   : 1 soal per conversation (biasanya "What does the man/woman mean?")
  Difficulty: easy-medium

Part B — Longer Conversations
  Format : Dialog panjang 2 speaker, 10-15 baris
  Topik  : Diskusi akademik, rencana, masalah dan solusi
  Soal   : 3-4 soal per conversation
  Difficulty: medium-hard

Part C — Talks / Monologues
  Format : Monolog 1 speaker (dosen/announcer), 15-20 baris
  Topik  : Kuliah singkat, announcement, campus talk
  Soal   : 4-5 soal per talk
  Difficulty: medium-hard
"""

LISTENING_GENERATOR_SYSTEM_PROMPT = """You are an expert TOEFL ITP test content creator \
specializing in the Listening Comprehension section.

Your task is to generate authentic TOEFL-style listening scripts with accompanying \
multiple-choice questions.

## Speaker Tag Rules (CRITICAL)
Every line of dialogue MUST start with a speaker tag:
  [SPEAKER_A]: text here
  [SPEAKER_B]: text here
  [NARRATOR]: text here (Part C only)

Rules:
- Tags must be EXACT: [SPEAKER_A], [SPEAKER_B], [NARRATOR]
- No tag variations (no [Man], [Woman], [Professor])
- Every spoken line needs a tag — no untagged dialogue
- Part A and B: use only [SPEAKER_A] and [SPEAKER_B]
- Part C: use only [NARRATOR]

## Part A — Short Conversation Guidelines
- 4-6 lines total (2-3 exchanges)
- Natural, casual academic register
- One speaker implies something — the other responds
- Avoid overly simple or overly complex language
- Difficulty: easy-medium
- 1 question per conversation
- Question types: "What does X mean?", "What will X probably do?", "Where does this conversation take place?"

## Part B — Longer Conversation Guidelines
- 10-15 lines total (5-7 exchanges)
- Substantive topic: study plans, campus issues, academic projects
- Clear problem-solution or opinion-exchange structure
- 3-4 questions per conversation
- Difficulty: medium-hard
- Mix question types: main idea, detail, inference

## Part C — Talk Guidelines
- 15-20 lines of continuous narration
- Academic or campus announcement context
- Clear organization: introduction → main points → conclusion
- 4-5 questions per talk
- Difficulty: medium-hard
- Include: main topic question, detail questions, inference question

## Question Quality Rules
- Exactly ONE correct answer per question
- 4 options (A, B, C, D) — all plausible, only one correct
- Distractors must be related to the topic but clearly wrong
- Avoid "all of the above" or "none of the above"
- Questions must be answerable ONLY from the script content

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "part": "A" | "B" | "C",
  "items": [
    {
      "item_id": 1,
      "script": "full script with speaker tags",
      "questions": [
        {
          "question_number": 1,
          "question_text": "string",
          "options": {
            "A": "string",
            "B": "string",
            "C": "string",
            "D": "string"
          },
          "correct_answer": "A" | "B" | "C" | "D",
          "difficulty": "easy" | "medium" | "hard"
        }
      ]
    }
  ]
}"""


def build_listening_prompt(
    part: str,
    item_count: int,
    questions_per_item: int,
) -> str:
    """
    Bangun user prompt untuk Listening Generator.

    Args:
        part               : "A" | "B" | "C"
        item_count         : Jumlah conversation/talk yang dibuat
        questions_per_item : Jumlah soal per conversation/talk

    Returns:
        String user prompt siap dikirim ke LLM
    """
    part_desc = {
        "A": (
            "SHORT CONVERSATIONS (Part A)\n"
            "- Each conversation: 4-6 lines, 2 speakers\n"
            "- 1 question per conversation\n"
            "- Difficulty: easy to medium\n"
            "- Topics: campus life, schedules, opinions, everyday academic situations"
        ),
        "B": (
            "LONGER CONVERSATIONS (Part B)\n"
            "- Each conversation: 10-15 lines, 2 speakers\n"
            "- 3-4 questions per conversation\n"
            "- Difficulty: medium to hard\n"
            "- Topics: academic discussions, campus problems, study plans"
        ),
        "C": (
            "TALKS / MONOLOGUES (Part C)\n"
            "- Each talk: 15-20 lines, single narrator\n"
            "- 4-5 questions per talk\n"
            "- Difficulty: medium to hard\n"
            "- Topics: mini-lectures, campus announcements, academic talks\n"
            "- Use [NARRATOR] tag for all lines"
        ),
    }.get(part, "")

    return f"""Generate {item_count} TOEFL ITP Listening {part_desc}

## Requirements
- Items to generate  : {item_count}
- Questions per item : {questions_per_item}
- Part               : {part}

## Critical Tag Reminder
{"Use [SPEAKER_A] and [SPEAKER_B] alternating naturally." if part in ("A","B") else "Use [NARRATOR] for every line."}

Generate all {item_count} item(s) in a single JSON response.
Vary the topics across items — do not repeat the same subject.
Respond with JSON only."""
