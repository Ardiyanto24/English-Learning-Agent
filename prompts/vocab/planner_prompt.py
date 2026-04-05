"""
prompts/vocab/planner_prompt.py
---------------------------------
Prompt untuk Vocab Planner Agent.

Planner bertugas membaca history user dari DB dan memutuskan
konfigurasi sesi vocab yang optimal berdasarkan 2 logic:

1. Difficulty Progression : Easy → Medium → Hard
   - Naik difficulty jika avg mastery_score >= 80% di level saat ini
   - Turun difficulty jika avg mastery_score < 40%

2. Cognitive Load Management:
   - Maksimal 5 kata baru per sesi
   - Sisa slot → review kata yang mastery_score < 60% (spaced repetition)

Cold start (user baru): gunakan default config langsung tanpa LLM.
"""

from config.settings import VOCAB_FORMAT_PCT

PLANNER_SYSTEM_PROMPT = """You are a vocabulary learning planner for an English learning app.

Your job is to analyze a student's learning history and create an optimal session plan \
that balances new learning with spaced repetition review.

## Planning Logic

### 1. Difficulty Progression
- Start at "easy" for new users (cold start)
- Upgrade to "medium" when: average mastery_score >= 80% for easy words
- Upgrade to "hard" when: average mastery_score >= 80% for medium words
- Downgrade one level when: average mastery_score < 40% for current level

### 2. Cognitive Load Management
- Maximum 5 new words per session (to avoid cognitive overload)
- Remaining slots → review words with mastery_score < 60% (spaced repetition)
- Total words per session: 10 (default)

### 3. Format Distribution
Use PERCENTAGE-BASED distribution, then round to whole numbers.
Caps that MUST be respected regardless of difficulty:
- sinonim_antonim : MAXIMUM 20% of total_words (round down if needed)
- tebak_inggris   : 20-50% of total_words depending on level
- tebak_arti      : fill the remaining slots

Per difficulty guideline:
- easy   : tebak_arti 60%, sinonim_antonim 20%, tebak_inggris 20%
- medium : tebak_arti 40%, sinonim_antonim 20%, tebak_inggris 40%
- hard   : tebak_arti 30%, sinonim_antonim 20%, tebak_inggris 50%

IMPORTANT: The sum of all format counts MUST equal total_words exactly.

## Output Format
You MUST respond with a valid JSON object only. No explanation, no markdown, no extra text.

## Example Output (returning student, medium level)
History shows: avg mastery_score medium words = 65%, 12 weak words available for review

{
  "topic": "sehari_hari",
  "total_words": 10,
  "new_words": 5,
  "review_words": 5,
  "difficulty_target": "medium",
  "format_distribution": {
    "tebak_arti": 3,
    "sinonim_antonim": 4,
    "tebak_inggris": 3
  }
}

## Example Output (cold start / new user)
No history available — use default config:

{
  "topic": "sehari_hari",
  "total_words": 10,
  "new_words": 5,
  "review_words": 5,
  "difficulty_target": "easy",
  "format_distribution": {
    "tebak_arti": 4,
    "sinonim_antonim": 3,
    "tebak_inggris": 3
  }
}

Remember: respond with JSON only."""


def build_planner_prompt(
    topic: str,
    history_summary: dict,
    total_words: int
) -> str:
    """
    Bangun user prompt untuk Vocab Planner Agent.

    Args:
        topic          : Topik situasi yang dipilih user.
                         Contoh: "sehari_hari", "di_kampus", "perjalanan"
        history_summary: Ringkasan history dari DB. Struktur:
                         {
                           "is_cold_start": bool,
                           "current_difficulty": "easy"|"medium"|"hard",
                           "avg_mastery_easy": float,    # 0-100, -1 jika tidak ada data
                           "avg_mastery_medium": float,
                           "avg_mastery_hard": float,
                           "weak_words_count": int,      # kata dengan mastery < 60%
                           "total_sessions": int,
                         }

    Returns:
        String user prompt siap dikirim ke LLM.
        Jika cold start, kembalikan None — gunakan default config langsung
        tanpa LLM call untuk hemat token.
    """

    # Cold start: tidak perlu LLM, langsung pakai default
    # Caller harus cek return None dan gunakan DEFAULT_PLANNER_CONFIG
    if history_summary.get("is_cold_start"):
        return None

    current_difficulty = history_summary.get("current_difficulty", "easy")
    avg_mastery = {
        "easy": history_summary.get("avg_mastery_easy", -1),
        "medium": history_summary.get("avg_mastery_medium", -1),
        "hard": history_summary.get("avg_mastery_hard", -1),
    }
    weak_words_count = history_summary.get("weak_words_count", 0)
    total_sessions = history_summary.get("total_sessions", 0)

    # Format mastery info
    mastery_lines = []
    for level, score in avg_mastery.items():
        if score >= 0:
            mastery_lines.append(f"  - {level}: {score:.1f}%")
        else:
            mastery_lines.append(f"  - {level}: no data yet")
    mastery_str = "\n".join(mastery_lines)

    return f"""Create a vocabulary session plan for this student.

## Student Profile
Topic selected   : {topic}
Total words      : {total_words}
Total sessions   : {total_sessions}
Current level    : {current_difficulty}
Weak words available for review: {weak_words_count} words (mastery < 60%)

## Mastery Scores by Difficulty
{mastery_str}

## Instructions
- total_words is FIXED at {total_words} — do not change this value
- The sum of format_distribution values MUST equal {total_words} exactly
- new_words maximum is {min(5, total_words)} (cognitive load cap)
- Determine if difficulty should stay, upgrade, or downgrade based on mastery scores
- Set new_words to maximum 5 (cognitive load limit)
- Set review_words = total_words - new_words (use weak words for spaced repetition)
- If weak_words_count < review_words needed, reduce review_words accordingly
- Distribute formats appropriately for the difficulty level

Respond with session plan JSON only."""


def build_default_planner_config(topic: str, total_words: int) -> dict:
    """
    Bangun default config untuk cold start secara dinamis.
    Distribusi format dihitung dari persentase, bukan hardcode.
    """
    difficulty = "easy"
    pct = VOCAB_FORMAT_PCT[difficulty]

    # Hitung distribusi dengan pembulatan — pastikan total = total_words
    tebak_arti = round(total_words * pct["tebak_arti"])
    sinonim = round(total_words * pct["sinonim_antonim"])
    tebak_ing = total_words - tebak_arti - sinonim  # sisa untuk hindari rounding drift

    return {
        "topic": topic,
        "total_words": total_words,
        "new_words": min(5, total_words),   # tetap hormati cognitive load limit
        "review_words": max(0, total_words - min(5, total_words)),
        "difficulty_target": difficulty,
        "format_distribution": {
            "tebak_arti": max(1, tebak_arti),
            "sinonim_antonim": max(0, sinonim),
            "tebak_inggris": max(0, tebak_ing),
        },
    }
