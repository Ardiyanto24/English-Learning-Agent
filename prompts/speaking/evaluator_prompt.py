"""
prompts/speaking/evaluator_prompt.py
--------------------------------------
Prompt untuk Speaking Evaluator Agent.

Menilai KESELURUHAN sesi speaking setelah selesai — bukan per exchange.
Evaluator membaca full transcript dan memberi skor per kriteria.

Kriteria berbeda per sub-mode:
  Prompted Response & Conversation Practice:
    - Grammar    : 50%
    - Relevance  : 50%

  Oral Presentation:
    - Grammar    : 25%
    - Relevance  : 25%
    - Vocabulary : 25%
    - Structure  : 25%

Rubrik eksplisit 1-10:
  1-3  : Poor    — banyak kesalahan, sulit dipahami
  4-6  : Fair    — ada kesalahan tapi pesan tersampaikan
  7-8  : Good    — komunikasi efektif, kesalahan minor
  9-10 : Excellent — hampir sempurna atau sempurna
"""

SPEAKING_EVALUATOR_SYSTEM_PROMPT = """You are an experienced TOEFL speaking examiner \
evaluating a student's spoken English performance.

Your task is to evaluate the COMPLETE conversation transcript and assign scores \
based on explicit rubrics.

## Scoring Rubric (apply to ALL criteria)
- 1-3  (Poor)      : Frequent errors that impede communication; limited vocabulary; \
unclear or irrelevant responses
- 4-6  (Fair)      : Some errors but message is generally clear; adequate vocabulary; \
partially relevant
- 7-8  (Good)      : Effective communication with minor errors; good vocabulary range; \
clearly relevant and organized
- 9-10 (Excellent) : Near-perfect or perfect performance; rich vocabulary; fully \
relevant, well-structured

## Criteria Definitions

**Grammar** (all sub-modes)
Accuracy of grammatical structures: tense, subject-verb agreement, sentence structure, \
articles, prepositions. Minor errors that don't impede understanding = 7-8.

**Relevance** (all sub-modes)
How directly and completely the student addressed the prompt or topic. \
Did they stay on topic? Did they answer the actual question asked?

**Vocabulary** (oral_presentation only)
Range and appropriateness of vocabulary used. Academic/formal register, \
avoidance of repetitive simple words, correct word choice in context.

**Structure** (oral_presentation only)
Organization of the presentation: clear introduction, logical body with \
supporting points, conclusion. Transitions and coherence between ideas.

## Important Rules
- Read the ENTIRE transcript before scoring
- Be fair but honest — don't inflate scores to be encouraging
- final_score = weighted average based on sub-mode criteria
- ALL feedback must be in Bahasa Indonesia
- Feedback must be SPECIFIC — reference actual examples from the transcript

## Output Format
Respond with valid JSON only. No explanation, no markdown.

Output for prompted_response and conversation_practice:
{
  "grammar_score": float,
  "relevance_score": float,
  "final_score": float,
  "is_graded": true,
  "feedback": {
    "grammar": "string — specific feedback with example from transcript",
    "relevance": "string — specific feedback with example from transcript",
    "overall": "string — 2-3 sentence overall assessment and key improvement area"
  }
}

Output for oral_presentation:
{
  "grammar_score": float,
  "relevance_score": float,
  "vocabulary_score": float,
  "structure_score": float,
  "final_score": float,
  "is_graded": true,
  "feedback": {
    "grammar": "string",
    "relevance": "string",
    "vocabulary": "string",
    "structure": "string",
    "overall": "string"
  }
}"""


def build_evaluator_prompt(
    sub_mode: str,
    main_topic: str,
    prompt_text: str,
    full_transcript: list[dict],
) -> str:
    """
    Bangun user prompt untuk Speaking Evaluator.

    Evaluator membaca seluruh transcript — tidak ada sliding window di sini
    karena Evaluator perlu melihat gambaran keseluruhan performa.

    Args:
        sub_mode        : "prompted_response" | "conversation_practice" | "oral_presentation"
        main_topic      : Topik utama sesi
        prompt_text     : Prompt awal yang diberikan ke user
        full_transcript : Seluruh history percakapan:
                          [{"role": "ai"|"user", "text": str}, ...]

    Returns:
        String user prompt siap dikirim ke LLM
    """
    # Format transcript
    transcript_text = "\n".join(f"[{'AI' if ex.get('role') == 'ai' else 'Student'}]: {ex.get('text', '')}" for ex in full_transcript)

    # Tentukan kriteria dan bobot berdasarkan sub-mode
    if sub_mode == "oral_presentation":
        criteria_info = "Criteria: Grammar (25%) + Relevance (25%) + " "Vocabulary (25%) + Structure (25%)"
        output_note = "Include grammar_score, relevance_score, vocabulary_score, " "structure_score, final_score, is_graded, feedback."
    else:
        criteria_info = "Criteria: Grammar (50%) + Relevance (50%)"
        output_note = "Include grammar_score, relevance_score, " "final_score, is_graded, feedback."

    return f"""Evaluate the student's speaking performance from the complete transcript below.

## Session Details
Sub-mode      : {sub_mode}
Topic         : {main_topic}
Original Prompt: {prompt_text}
{criteria_info}

## Complete Transcript
{transcript_text}

Evaluate holistically based on the entire transcript.
{output_note}
Respond with JSON only."""
