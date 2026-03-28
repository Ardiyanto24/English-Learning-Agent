"""
prompts/analytics/toefl_analytics_prompt.py
--------------------------------------------
Prompt untuk TOEFL Analytics Agent.

Berbeda dari Quiz/Vocab Analytics karena fokusnya adalah:
1. Trend estimasi skor dari simulasi ke simulasi
2. Performa per section (Listening, Structure, Reading) secara terpisah
3. Identifikasi weakest section yang paling menarik perhatian
4. Analisis mode yang dimainkan — apakah user sudah siap naik dari 50% ke 75%?
"""

TOEFL_ANALYTICS_SYSTEM_PROMPT = """You are an expert TOEFL ITP score analytics engine.

Your task is to analyze a student's TOEFL simulation history and generate \
actionable insights to help them improve their estimated score.

## What You Analyze
1. **Score trend**       — is the estimated score improving across simulations?
2. **Section breakdown** — which section (Listening / Structure / Reading) is weakest?
3. **Mode progression**  — is the student ready to move to a harder mode (e.g., 50% -> 75%)?
4. **Section consistency** — are scores consistent or volatile across simulations?
5. **Gap to target**     — how far is the current estimated score from the student's target?

## Insight Quality Rules
- Reference SPECIFIC scores and sections from the data, not generic statements
- Compare section scores against each other — identify the biggest drag on total score
- If weakest section is consistently weak across simulations, flag it as a priority
- Keep insight in Bahasa Indonesia, warm and motivating in tone
- Be concrete: "Section Structure kamu rata-rata 42 (scaled), jauh di bawah Listening 55 dan Reading 51"
- If only 3 simulations available, note that trend is early and more data helps accuracy

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "total_simulations": 5,
  "avg_estimated_score": 487,
  "best_estimated_score": 510,
  "latest_estimated_score": 497,
  "section_averages": {
    "listening_scaled": 55.2,
    "structure_scaled": 42.0,
    "reading_scaled": 51.4
  },
  "weakest_section": "structure",
  "most_improved_section": "reading",
  "score_trend": "improving",
  "mode_recommendation": "Kamu sudah konsisten di atas 480 dengan mode 50%. Pertimbangkan untuk naik ke mode 75% untuk simulasi yang lebih realistis.",
  "insight": "Progress kamu menunjukkan tren positif!"
}

## Score Trend Values
- "improving"          : estimated score naik >= 10 poin dari simulasi pertama ke terakhir
- "stable"             : perubahan < 10 poin
- "declining"          : estimated score turun
- "insufficient_data"  : kurang dari 3 simulasi"""


def build_toefl_analytics_prompt(sessions_data: list) -> str:
    """
    Bangun user prompt untuk TOEFL Analytics Agent.

    Args:
        sessions_data: List dict dari toefl_sessions (status='completed'),
                       sudah di-filter dari yang abandoned.
                       Setiap dict berisi: session_id, mode, estimated_score,
                       listening_scaled, structure_scaled, reading_scaled,
                       listening_raw, structure_raw, reading_raw, created_at
    """
    import json

    total = len(sessions_data)

    def _avg(key: str) -> float:
        vals = [s.get(key) for s in sessions_data if s.get(key) is not None]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    avg_listening = _avg("listening_scaled")
    avg_structure = _avg("structure_scaled")
    avg_reading = _avg("reading_scaled")
    avg_estimated = _avg("estimated_score")

    scores = [s.get("estimated_score", 0) for s in sessions_data]
    trend_hint = "insufficient_data"
    if len(scores) >= 3:
        if len(scores) >= 6:
            first_avg = sum(scores[:3]) / 3
            last_avg = sum(scores[-3:]) / 3
            trend_hint = "improving" if last_avg >= first_avg + 10 else "declining" if last_avg <= first_avg - 10 else "stable"
        else:
            trend_hint = "improving" if scores[-1] >= scores[0] + 10 else "declining" if scores[-1] <= scores[0] - 10 else "stable"

    section_avgs = {
        "listening": avg_listening,
        "structure": avg_structure,
        "reading": avg_reading,
    }
    weakest = min(section_avgs, key=lambda k: section_avgs[k])

    modes = [s.get("mode", "") for s in sessions_data]
    mode_counts = {m: modes.count(m) for m in set(modes)}

    return f"""Analyze this student's TOEFL simulation history and generate insights.

## Summary Statistics
Total simulations     : {total}
Avg estimated score   : {avg_estimated}
Best estimated score  : {max(scores) if scores else 0}
Latest estimated score: {scores[-1] if scores else 0}
Score trend hint      : {trend_hint}

## Section Averages (scaled scores)
Listening  : {avg_listening}
Structure  : {avg_structure}
Reading    : {avg_reading}
Weakest    : {weakest}

## Mode Distribution
{json.dumps(mode_counts, ensure_ascii=False)}

## Full Simulation History (chronological)
{json.dumps(sessions_data, ensure_ascii=False, indent=2)}

Generate comprehensive TOEFL analytics insight. Respond with JSON only."""
