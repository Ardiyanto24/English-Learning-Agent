"""
prompts/vocab/validator_prompt.py
----------------------------------
Prompt untuk Vocab Validator Agent.

Validator bertugas memastikan output Generator sesuai dengan
instruksi Planner. Bertindak sebagai quality checker, bukan
content judge — tidak menilai kualitas soal, hanya kesesuaian
distribusi dan format.

Toleransi: match_score >= 0.8 dianggap valid.
"""

VALIDATOR_SYSTEM_PROMPT = """You are a strict quality checker for vocabulary quiz content.

Your ONLY job is to verify that the generated vocabulary questions match the planner's specifications.
You do NOT evaluate question quality or content — only structural compliance.

## What You Check
1. Total word count matches specification
2. Format distribution matches (tebak_arti, sinonim_antonim, tebak_inggris counts)
3. Difficulty distribution is consistent with target
4. new_words and review_words counts match (is_new flags)
5. All required fields present: word, difficulty, format, question_text, correct_answer, is_new

## Scoring
- match_score: float 0.0–1.0
  - 1.0  = perfect match
  - 0.8+ = acceptable (minor deviations)
  - <0.8 = invalid, needs regeneration

## Output Format
You MUST respond with a valid JSON object only. No explanation, no markdown, no extra text.

## Example: Valid Output
Planner specified: 10 words, tebak_arti:4, sinonim_antonim:3, tebak_inggris:3, new:5, review:5
Generator produced: 10 words, tebak_arti:4, sinonim_antonim:3, tebak_inggris:3, new:5, review:5

{
  "is_valid": true,
  "match_score": 1.0,
  "issues": [],
  "adjusted_words": []
}

## Example: Invalid Output (needs adjustment)
Planner specified: 10 words, tebak_arti:4, sinonim_antonim:3, tebak_inggris:3
Generator produced: 10 words, tebak_arti:5, sinonim_antonim:2, tebak_inggris:3

{
  "is_valid": false,
  "match_score": 0.75,
  "issues": [
    "Format distribution mismatch: tebak_arti expected 4 got 5, sinonim_antonim expected 3 got 2"
  ],
  "adjusted_words": [
    {
      "word": "commute",
      "difficulty": "medium",
      "format": "sinonim_antonim",
      "question_text": "Pilih sinonim dari kata 'commute':",
      "correct_answer": "travel",
      "is_new": true
    }
  ]
}

Note: adjusted_words contains ONLY the words that need to be changed, not all words.
If is_valid is true, adjusted_words must be an empty array.

Remember: respond with JSON only."""


def build_validator_prompt(
    planner_output: dict,
    generator_output: dict,
) -> str:
    """
    Bangun user prompt untuk Vocab Validator Agent.

    Args:
        planner_output  : Output dari Planner Agent (dict).
                          Berisi: topic, total_words, new_words, review_words,
                                  difficulty_target, format_distribution
        generator_output: Output dari Generator Agent (dict).
                          Berisi: words (list of word objects)

    Returns:
        String user prompt siap dikirim ke LLM
    """
    import json

    # Hitung distribusi aktual dari output Generator
    words = generator_output.get("words", [])
    actual_formats = {}
    actual_new = 0
    actual_review = 0

    for w in words:
        fmt = w.get("format", "unknown")
        actual_formats[fmt] = actual_formats.get(fmt, 0) + 1
        if w.get("is_new"):
            actual_new += 1
        else:
            actual_review += 1

    return f"""Validate the generated vocabulary questions against the planner specifications.

## Planner Specifications
Topic             : {planner_output.get('topic')}
Total words       : {planner_output.get('total_words')}
New words         : {planner_output.get('new_words')}
Review words      : {planner_output.get('review_words')}
Difficulty target : {planner_output.get('difficulty_target')}
Format distribution: {json.dumps(planner_output.get('format_distribution', {}), ensure_ascii=False)}

## Generator Output Summary
Total words generated : {len(words)}
New words (is_new=true) : {actual_new}
Review words (is_new=false) : {actual_review}
Actual format distribution : {json.dumps(actual_formats, ensure_ascii=False)}

## Full Generator Output
{json.dumps(generator_output, ensure_ascii=False, indent=2)}

Check all specifications and respond with validation result JSON only."""