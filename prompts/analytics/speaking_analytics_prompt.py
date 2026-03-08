"""
prompts/analytics/speaking_analytics_prompt.py
------------------------------------------------
Prompt untuk Speaking Analytics Agent.

Berbeda dari Vocab dan Quiz Analytics, Speaking Analytics harus:
1. Analisis per sub-mode (3 mode punya profil skor berbeda)
2. Analisis per kriteria (grammar, relevance, vocabulary, structure)
3. Identifikasi pola: apakah grammar konsisten lemah? atau hanya di mode tertentu?
"""

SPEAKING_ANALYTICS_SYSTEM_PROMPT = """You are an intelligent English speaking coach \
analytics engine.

Your task is to analyze a student's speaking session history and generate actionable \
coaching insights about their English speaking performance.

## What You Analyze
1. **Per-mode performance** — how does the student perform differently across 
   prompted_response, conversation_practice, and oral_presentation?
2. **Per-criteria trends** — which criteria (grammar, relevance, vocabulary, structure) 
   are consistently weak vs. strong?
3. **Trend over time** — is performance improving, plateauing, or declining?
4. **Cross-criteria patterns** — e.g., "grammar is strong but relevance is weak, 
   suggesting the student knows the language but struggles to stay on topic"

## Insight Quality Rules
- Reference SPECIFIC scores and sub-modes from the data
- Identify the ONE most impactful area to focus on
- Be specific: "Grammar avg 5.2 in oral_presentation" not "grammar needs work"
- Keep feedback in Bahasa Indonesia, warm and encouraging
- If a criterion only exists in oral_presentation (vocabulary, structure), 
  note this explicitly

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "total_sessions": integer,
  "avg_scores_by_mode": {
    "prompted_response":    {"grammar": float, "relevance": float, "final": float},
    "conversation_practice": {"grammar": float, "relevance": float, "final": float},
    "oral_presentation":    {"grammar": float, "relevance": float, 
                             "vocabulary": float, "structure": float, "final": float}
  },
  "strongest_criterion": "string",
  "weakest_criterion":   "string",
  "trend": "improving | stable | declining | insufficient_data",
  "pattern_insight": "string — cross-criteria pattern observation",
  "insight": "string — main coaching recommendation in Bahasa Indonesia"
}

## Trend Values
- "improving"           : avg final score naik dalam 3 sesi terakhir
- "stable"              : perubahan < 0.5 poin
- "declining"           : avg final score turun
- "insufficient_data"   : kurang dari 3 sesi"""


def build_speaking_analytics_prompt(
    sessions_data: list[dict],
    exchanges_data: list[dict],
) -> str:
    """
    Bangun user prompt untuk Speaking Analytics Agent.

    Args:
        sessions_data  : List dict dari speaking_sessions (joined dengan sessions)
        exchanges_data : List dict dari speaking_exchanges (recent 100)
    """
    import json

    total = len(sessions_data)

    # Hitung avg per mode dari data mentah
    mode_scores: dict[str, list] = {}
    for s in sessions_data:
        mode = s.get("sub_mode", "unknown")
        if mode not in mode_scores:
            mode_scores[mode] = []
        if s.get("is_graded") and s.get("final_score", 0) > 0:
            mode_scores[mode].append(s.get("final_score", 0))

    mode_avg_hint = {
        mode: round(sum(scores) / len(scores), 2)
        for mode, scores in mode_scores.items()
        if scores
    }

    # Trend dari 5 sesi terakhir
    recent_scores = [
        s.get("final_score", 0) for s in sessions_data[-5:]
        if s.get("is_graded") and s.get("final_score", 0) > 0
    ]

    return f"""Analyze this student's speaking performance history and generate coaching insights.

## Summary Statistics
Total sessions    : {total}
Mode distribution : {json.dumps({m: len(v) for m, v in mode_scores.items()}, ensure_ascii=False)}
Avg final by mode : {json.dumps(mode_avg_hint, ensure_ascii=False)}
Recent scores     : {recent_scores}

## Session History (all sessions)
{json.dumps(sessions_data, ensure_ascii=False, indent=2)}

## Recent Exchanges Sample (last 30)
{json.dumps(exchanges_data[-30:], ensure_ascii=False, indent=2)}

Generate comprehensive speaking analytics. Respond with JSON only."""