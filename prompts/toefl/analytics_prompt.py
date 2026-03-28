"""
prompts/analytics/analytics_prompt.py
---------------------------------------------
Prompt untuk TOEFL Analytics Agent.

Berbeda dari Vocab/Quiz/Speaking Analytics, TOEFL Analytics harus:
1. Memahami sistem skor TOEFL ITP — raw → extrapolated → scaled → estimated
2. Analisis per section (Listening, Structure, Reading) dengan range scaled berbeda
3. Analisis per mode (50%, 75%, 100%) karena skor antar mode tidak langsung comparable
4. Identifikasi trend estimated_score sebagai indikator kesiapan TOEFL user
"""

import json

TOEFL_ANALYTICS_SYSTEM_PROMPT = """You are an intelligent TOEFL ITP score analytics \
engine for an English learning application.

Your task is to analyze a student's TOEFL simulation history and generate actionable \
insights about their score development and section strengths/weaknesses.

## TOEFL ITP Score Context
- Scores are SCALED (not raw) — use scaled scores for comparison across modes
- Scaled score ranges: Listening 31-68, Structure 31-68, Reading 31-67
- Estimated score = (L_scaled + S_scaled + R_scaled) × 10/3, range 310-677
- Modes: 50pct (half test), 75pct (three-quarter), 100pct (full test)
- When comparing across modes, rely on scaled scores — they are extrapolated \
to full-test equivalent before scaling

## What You Analyze
1. **Score trend** — is estimated_score improving, stable, or declining?
2. **Section breakdown** — which section has the lowest avg scaled score?
3. **Mode distribution** — has the student progressed to harder modes?
4. **Weakest section** — the section most in need of focused practice

## Section Trend Values
For each section, evaluate the last 3 sessions:
- "improving"          : avg scaled score naik
- "stable"             : perubahan < 2 poin
- "declining"          : avg scaled score turun
- "insufficient_data"  : kurang dari 3 data poin

## Insight Quality Rules
- Reference SPECIFIC scores from the data
- Keep insight in Bahasa Indonesia, warm and encouraging
- Mention the weakest section explicitly with its avg scaled score
- If all sections are improving, acknowledge this positively
- Be concise — 2-3 sentences maximum for the insight field

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "total_simulations"  : integer,
  "avg_estimated_score": float,
  "score_trend"        : [integer],
  "weakest_section"    : "listening" | "structure" | "reading",
  "section_breakdown"  : {
    "listening": {"avg_scaled": float, "trend": "improving|stable|declining|insufficient_data"},
    "structure": {"avg_scaled": float, "trend": "improving|stable|declining|insufficient_data"},
    "reading"  : {"avg_scaled": float, "trend": "improving|stable|declining|insufficient_data"}
  },
  "insight": "string — 2-3 kalimat dalam Bahasa Indonesia"
}"""


def build_toefl_analytics_prompt(sessions_data: list[dict]) -> str:
    """
    Bangun user prompt untuk TOEFL Analytics Agent.

    Pre-hitung agregasi sederhana di sini untuk membantu LLM
    fokus pada insight, bukan kalkulasi dasar.

    Args:
        sessions_data: List dict dari toefl_sessions (score_status='completed'),
                       diurutkan ASC by created_at
    """
    total = len(sessions_data)

    # Score trend — estimated_score tiap sesi (untuk grafik UI)
    score_trend = [s["estimated_score"] for s in sessions_data if s.get("estimated_score")]

    # Avg estimated score keseluruhan
    avg_estimated = round(sum(score_trend) / len(score_trend), 1) if score_trend else None

    # Avg scaled per section
    def _avg_scaled(key: str) -> float | None:
        vals = [s[key] for s in sessions_data if s.get(key)]
        return round(sum(vals) / len(vals), 1) if vals else None

    section_avgs = {
        "listening": _avg_scaled("listening_scaled"),
        "structure": _avg_scaled("structure_scaled"),
        "reading": _avg_scaled("reading_scaled"),
    }

    # Mode distribution — berapa sesi per mode
    mode_dist: dict[str, int] = {}
    for s in sessions_data:
        mode = s.get("mode", "unknown")
        mode_dist[mode] = mode_dist.get(mode, 0) + 1

    # Recent 5 sesi untuk trend context
    recent = sessions_data[-5:] if len(sessions_data) >= 5 else sessions_data

    return f"""Analyze this student's TOEFL simulation history and generate score insights.

## Summary Statistics
Total simulations    : {total}
Mode distribution    : {json.dumps(mode_dist, ensure_ascii=False)}
Avg estimated score  : {avg_estimated}
Score trend (all)    : {score_trend}
Avg scaled scores    : {json.dumps(section_avgs, ensure_ascii=False)}

## Full Session History
{json.dumps(sessions_data, ensure_ascii=False, indent=2)}

## Recent 5 Sessions (for trend)
{json.dumps(recent, ensure_ascii=False, indent=2)}

Generate TOEFL analytics insights. Respond with JSON only."""
