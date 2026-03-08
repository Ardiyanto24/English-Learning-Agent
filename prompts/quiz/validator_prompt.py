"""
prompts/quiz/validator_prompt.py
----------------------------------
Prompt untuk Quiz Validator Agent.

Tugasnya lebih sederhana dari Generator — hanya cek struktur dan
kesesuaian instruksi Planner, bukan menilai kualitas soal.

Pertanyaan yang dijawab Validator:
1. Apakah jumlah soal sesuai?
2. Apakah distribusi format sesuai?
3. Apakah difficulty sesuai target?
4. Apakah semua field wajib ada?
"""

QUIZ_VALIDATOR_SYSTEM_PROMPT = """You are a quality checker for TOEFL grammar questions.

Your task is to verify that the generated questions match the planner's instructions.
You check STRUCTURE and COMPLIANCE only — not content quality or grammar accuracy.

## What You Check
1. Total questions count matches the instruction
2. Format distribution matches (multiple_choice, error_id, fill_blank counts)
3. Difficulty distribution is close to the target
4. All required fields are present in every question
5. Topics used are from the approved topic list

## Scoring
- match_score: 0.0 to 1.0
- Calculate as: (checks_passed / total_checks)
- 5 checks total: count, format_dist, difficulty, fields, topics
- Score >= 0.8 = valid (4 out of 5 checks passed is acceptable)

## adjusted_questions
If match_score < 0.8, provide adjusted_questions — a list of question objects
that would fix the specific issues found. Only include questions that need changing.
If valid, return empty list.

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "is_valid": true | false,
  "match_score": float,
  "issues": ["string — describe each failed check"],
  "adjusted_questions": []
}"""


def build_validator_prompt(planner_output: dict, generator_output: dict) -> str:
    """
    Bangun user prompt untuk Quiz Validator.

    Validator menghitung distribusi aktual dari generator_output
    dan membandingkan dengan instruksi planner_output.

    Args:
        planner_output   : Output dari Quiz Planner Agent
        generator_output : Output dari Quiz Generator Agent

    Returns:
        String user prompt siap dikirim ke LLM
    """
    import json

    questions = generator_output.get("questions", [])
    expected_total = planner_output.get("total_questions", 0)
    expected_formats = planner_output.get("format_distribution", {})
    expected_difficulty = planner_output.get("difficulty_target", "medium")
    approved_topics = planner_output.get("topics", [])

    # Hitung distribusi aktual
    actual_formats: dict = {}
    actual_difficulties: dict = {}
    actual_topics: list = []

    for q in questions:
        fmt = q.get("format", "unknown")
        diff = q.get("difficulty", "unknown")
        topic = q.get("topic", "unknown")

        actual_formats[fmt] = actual_formats.get(fmt, 0) + 1
        actual_difficulties[diff] = actual_difficulties.get(diff, 0) + 1
        if topic not in actual_topics:
            actual_topics.append(topic)

    return f"""Verify the generated questions against the planner instructions.

## Planner Instructions (Expected)
Total questions     : {expected_total}
Format distribution : {json.dumps(expected_formats)}
Difficulty target   : {expected_difficulty}
Approved topics     : {approved_topics}

## Generated Output (Actual)
Total questions     : {len(questions)}
Format distribution : {json.dumps(actual_formats)}
Difficulty found    : {json.dumps(actual_difficulties)}
Topics used         : {actual_topics}

## Full Generated Questions
{json.dumps(questions, indent=2)}

Check compliance and respond with JSON only."""