"""
prompts/analytics/vocab_analytics_prompt.py
--------------------------------------------
Prompt untuk Vocab Analytics Agent.

Agent ini membaca data historis user dan menghasilkan insight
yang actionable tentang performa vocab mereka.

Dipanggil setelah setiap sesi vocab selesai (triggered dari akhir sesi).
Minimum 3 sesi diperlukan sebelum insight bisa dihasilkan.
"""

VOCAB_ANALYTICS_SYSTEM_PROMPT = """You are an intelligent vocabulary learning analytics engine.

Your task is to analyze a student's vocabulary learning history and generate \
actionable insights to help them improve.

## What You Analyze
1. **Word mastery patterns** — which words/difficulty levels are strong or weak
2. **Format performance** — which quiz formats the student struggles with
3. **Progress trend** — is the student improving, plateauing, or declining?
4. **Spaced repetition effectiveness** — are review words improving over time?

## Insight Quality Rules
- Be specific, not generic. "You struggle with medium difficulty words" is better than "Keep practicing"
- Reference actual data from the input. Don't make up patterns.
- Prioritize the most impactful insight — don't list everything
- Keep insight in Bahasa Indonesia, warm and encouraging in tone

## Output Format
You MUST respond with a valid JSON object only. No explanation, no markdown, no extra text.

## Example Output

{
  "total_words_learned": 47,
  "mastery_distribution": {
    "easy": 85.2,
    "medium": 61.4,
    "hard": 38.0
  },
  "weakest_format": "sinonim_antonim",
  "strongest_format": "tebak_arti",
  "weak_words": ["physician", "commute", "reconcile"],
  "trend": "improving",
  "insight": "Kamu sudah menguasai 85% kata mudah dengan sangat baik! Namun, kata-kata medium masih perlu perhatian — terutama format sinonim/antonim yang skornya paling rendah. Coba fokus latihan di format itu minggu ini."
}

## Trend Values
- "improving"  : avg score naik konsisten dalam 3 sesi terakhir
- "stable"     : avg score tidak berubah signifikan (< 5% perubahan)
- "declining"  : avg score turun dalam 3 sesi terakhir
- "insufficient_data": kurang dari 3 sesi (seharusnya tidak sampai sini)

Remember: respond with JSON only."""


def build_vocab_analytics_prompt(
    sessions_data: list[dict],
    word_tracking_data: list[dict],
    questions_data: list[dict],
) -> str:
    """
    Bangun user prompt untuk Vocab Analytics Agent.

    Args:
        sessions_data      : List dict dari tabel vocab_sessions
                             [{session_id, topic, score_pct, correct_count,
                               wrong_count, created_at, ...}]
        word_tracking_data : List dict dari tabel vocab_word_tracking
                             [{word, topic, difficulty, mastery_score,
                               correct_count, wrong_count, ...}]
        questions_data     : List dict dari tabel vocab_questions
                             [{word, format, is_correct, is_graded, ...}]

    Returns:
        String user prompt siap dikirim ke LLM
    """
    import json

    # Hitung ringkasan statistik untuk membantu LLM
    total_sessions = len(sessions_data)
    avg_score = (
        sum(s.get("score_pct", 0) for s in sessions_data) / total_sessions
        if total_sessions > 0
        else 0
    )

    # Trend: bandingkan 3 sesi terakhir vs 3 sesi sebelumnya
    scores = [s.get("score_pct", 0) for s in sessions_data]
    trend_hint = "insufficient_data"
    if len(scores) >= 3:
        recent_avg = sum(scores[-3:]) / 3
        if len(scores) >= 6:
            prev_avg = sum(scores[-6:-3]) / 3
            if recent_avg > prev_avg + 5:
                trend_hint = "improving"
            elif recent_avg < prev_avg - 5:
                trend_hint = "declining"
            else:
                trend_hint = "stable"
        else:
            trend_hint = "improving" if scores[-1] >= scores[0] else "declining"

    # Format per-format stats dari questions_data
    format_stats: dict[str, dict] = {}
    for q in questions_data:
        if not q.get("is_graded"):
            continue
        fmt = q.get("format", "unknown")
        if fmt not in format_stats:
            format_stats[fmt] = {"correct": 0, "total": 0}
        format_stats[fmt]["total"] += 1
        if q.get("is_correct"):
            format_stats[fmt]["correct"] += 1

    format_accuracy = {
        fmt: round(v["correct"] / v["total"] * 100, 1)
        for fmt, v in format_stats.items()
        if v["total"] > 0
    }

    return f"""Analyze this student's vocabulary learning data and generate insights.

## Summary Statistics
Total sessions     : {total_sessions}
Average score      : {avg_score:.1f}%
Score trend hint   : {trend_hint}
Recent scores      : {scores[-5:] if scores else []}

## Format Accuracy (%)
{json.dumps(format_accuracy, ensure_ascii=False, indent=2)}

## Word Tracking Data (sample — top 30 by activity)
{json.dumps(word_tracking_data[:30], ensure_ascii=False, indent=2)}

## Session History (last 10 sessions)
{json.dumps(sessions_data[-10:], ensure_ascii=False, indent=2)}

Generate a comprehensive but concise analytics insight. Respond with JSON only."""
