"""
prompts/analytics/quiz_analytics_prompt.py
-------------------------------------------
Prompt untuk Quiz Analytics Agent.

Berbeda dari Vocab Analytics, Quiz Analytics harus:
1. Sadar prerequisite — insight tentang pola "stuck" karena prereq belum terpenuhi
2. Cluster-aware — progress per cluster, bukan hanya per topik
3. Coverage tracking — berapa % dari 46 topik sudah pernah dilatih
"""

QUIZ_ANALYTICS_SYSTEM_PROMPT = """You are an intelligent grammar learning analytics engine.

Your task is to analyze a student's quiz history and generate actionable insights \
about their grammar learning progress.

## What You Analyze
1. **Weak topics** — topics with consistently low scores that need more practice
2. **Strong topics** — topics the student has mastered
3. **Cluster progress** — which grammar clusters are strong vs. weak overall
4. **Prerequisite patterns** — is the student stuck because a prerequisite is weak?
5. **Coverage** — what % of all topics have been practiced?
6. **Trend** — is the student improving, plateauing, or declining?

## Insight Quality Rules
- Reference SPECIFIC topics and scores from the data, not generic statements
- If a topic is weak AND it's a prerequisite for advanced topics, flag this explicitly
- Prioritize the most impactful insight — what ONE thing should the student focus on?
- Keep insight in Bahasa Indonesia, warm and encouraging in tone
- Be concrete: "Kamu lemah di Conditional Clauses (avg 45%)" bukan "Kamu perlu latihan lebih"

## Output Format
Respond with valid JSON only. No explanation, no markdown.

Example output:
{
  "coverage_pct": 65.2,
  "total_topics_practiced": 30,
  "weakest_topics": [
    {"topic": "Conditional Clauses", "avg_score": 42.0, "cluster": "Adverb Clause"},
    {"topic": "Passive Voice", "avg_score": 51.0, "cluster": "Verb Forms & Usage"}
  ],
  "strongest_topics": [
    {"topic": "Present Tenses", "avg_score": 91.0, "cluster": "Tense System"},
    {"topic": "Subject-Verb Agreement", "avg_score": 88.0, "cluster": "Subject-Verb Agreement"}
  ],
  "cluster_progress": {
    "Tense System": 82.5,
    "Verb Forms & Usage": 61.0,
    "Adverb Clause": 48.0
  },
  "prerequisite_bottleneck": "Conditional Clauses (avg 42%) menjadi bottleneck karena Advanced Conditional Patterns dan Elliptical Clauses Complex tidak bisa diakses sebelum ini dikuasai.",
  "trend": "improving",
  "insight": "Progress kamu sangat bagus di Tense System! Tapi ada bottleneck di Conditional Clauses yang perlu diselesaikan — topik ini menjadi prereq untuk 2 topik advanced yang belum bisa kamu akses. Fokus di sini dulu sebelum lanjut ke cluster Adverb Clause yang lebih kompleks."
}

## Trend Values
- "improving"           : avg score naik dalam 3 sesi terakhir
- "stable"              : perubahan < 5%
- "declining"           : avg score turun
- "insufficient_data"   : kurang dari 3 sesi"""


def build_quiz_analytics_prompt(
    sessions_data: list[dict],
    topic_tracking_data: list[dict],
    questions_data: list[dict],
    prerequisite_rules: dict,
    total_topics: int = 46,
) -> str:
    """
    Bangun user prompt untuk Quiz Analytics Agent.

    Args:
        sessions_data       : List dict dari quiz_sessions
        topic_tracking_data : List dict dari quiz_topic_tracking
        questions_data      : List dict dari quiz_questions (recent)
        prerequisite_rules  : Dict dari prerequisite_rules.json
        total_topics        : Total topik (default 46)
    """
    import json

    total_sessions = len(sessions_data)
    practiced_topics = [t for t in topic_tracking_data if t.get("total_sessions", 0) > 0]
    coverage_pct = round(len(practiced_topics) / total_topics * 100, 1)

    # Trend dari skor sesi terakhir
    scores = [s.get("score_pct", 0) for s in sessions_data]
    trend_hint = "insufficient_data"
    if len(scores) >= 3:
        recent = sum(scores[-3:]) / 3
        if len(scores) >= 6:
            prev = sum(scores[-6:-3]) / 3
            trend_hint = (
                "improving"
                if recent > prev + 5
                else ("declining" if recent < prev - 5 else "stable")
            )
        else:
            trend_hint = "improving" if scores[-1] >= scores[0] else "declining"

    # Identifikasi potential bottleneck (topik lemah yang jadi prereq penting)
    weak_as_prereq = []
    for t_data in topic_tracking_data:
        topic = t_data.get("topic", "")
        score = t_data.get("avg_score_pct", 0)
        if score < 60 and t_data.get("total_sessions", 0) > 0:
            # Cek apakah topik ini jadi prereq untuk topik lain
            dependents = [
                other
                for other, rules in prerequisite_rules.items()
                if topic in rules.get("requires", [])
            ]
            if dependents:
                weak_as_prereq.append(
                    {
                        "topic": topic,
                        "score": score,
                        "blocks": dependents,
                    }
                )

    return f"""Analyze this student's quiz learning data and generate insights.

## Summary Statistics
Total sessions      : {total_sessions}
Topics practiced    : {len(practiced_topics)} / {total_topics}
Coverage            : {coverage_pct}%
Recent scores       : {scores[-5:] if scores else []}
Trend hint          : {trend_hint}

## Topic Tracking Data (all practiced topics)
{json.dumps(topic_tracking_data, ensure_ascii=False, indent=2)}

## Prerequisite Bottleneck Analysis
Topics that are weak AND blocking other topics:
{json.dumps(weak_as_prereq, ensure_ascii=False, indent=2)}

## Session History (last 10)
{json.dumps(sessions_data[-10:], ensure_ascii=False, indent=2)}

Generate comprehensive analytics insight. Respond with JSON only."""
