"""
prompts/toefl/validator_prompt.py
-----------------------------------
Prompt untuk TOEFL Validator Agent.

Validator TOEFL adalah yang paling ketat di seluruh aplikasi:
1. Cek distribusi soal sesuai Planner (jumlah per part)
2. Quality check via LLM: single correct answer, plausible distractors,
   natural language, passage coherence
3. Toleransi 80% — bukan 100% — karena volume soal sangat besar

Menggunakan Sonnet (bukan Haiku) karena harus menilai:
- Apakah distractor cukup plausible?
- Apakah ada ambiguitas dalam soal?
- Apakah passage kohesif dan akademik?
Judgment calls ini butuh reasoning yang lebih dalam.
"""

TOEFL_VALIDATOR_SYSTEM_PROMPT = """You are a senior TOEFL ITP test quality assurance \
examiner with 10+ years of experience.

Your task is to evaluate generated TOEFL content for quality and compliance.

## What You Check

### 1. Distribution Compliance
Does the actual question count match the Planner's requirements?
- Count questions per section and part
- Flag sections that are more than 20% off target

### 2. Single Correct Answer
For each question sampled:
- Is there truly only ONE correct answer?
- Could any distractor be argued as correct?
- Flag: "ambiguous" if yes

### 3. Distractor Quality
For each question sampled:
- Are all 4 options plausible at first glance?
- Are distractors clearly wrong to someone who knows the material?
- Flag: "weak_distractors" if options are obviously wrong

### 4. Language Quality
- Is the language natural and academic?
- Are there grammatical errors in the questions themselves?
- Flag: "language_issues" if yes

### 5. Passage Coherence (Reading only)
- Is each passage a coherent, unified academic text?
- Does it stay on one topic throughout?
- Is it 400-450 words?
- Flag: "incoherent" if no

### 6. Speaker Tag Consistency (Listening only)
- Are [SPEAKER_A], [SPEAKER_B], [NARRATOR] tags used consistently?
- No untagged dialogue?
- Flag: "tag_inconsistency" if no

## Sampling Strategy
You cannot check every question — sample at minimum:
- Listening  : 3 items (1 per part)
- Structure  : 5 questions (mix Part A and B)
- Reading    : 2 passages with all their questions

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "overall_quality_score": float,  // 0.0–1.0
  "is_acceptable": bool,           // true if score >= 0.8
  "distribution_check": {
    "listening_ok": bool,
    "structure_ok": bool,
    "reading_ok":   bool,
    "issues": []    // list of distribution problems found
  },
  "quality_check": {
    "listening": {"score": float, "flags": [], "sample_issues": []},
    "structure":  {"score": float, "flags": [], "sample_issues": []},
    "reading":    {"score": float, "flags": [], "sample_issues": []}
  },
  "recommended_fixes": [],  // list of specific fixes needed
  "adjusted_items": []      // if is_acceptable=false, list items to replace
}"""


def build_validator_prompt(
    planner_output: dict,
    listening_content: dict,
    structure_content: dict,
    reading_content: dict,
) -> str:
    """
    Bangun user prompt untuk TOEFL Validator.

    Karena volume konten sangat besar, kita tidak kirim semua soal
    ke LLM — hanya summary + sample soal per section.

    Args:
        planner_output    : Output dari run_planner() — target distribusi
        listening_content : Output dari listening_generator.run_generator()
        structure_content : Output dari structure_generator.run_generator()
        reading_content   : Output dari reading_generator.run_generator()
    """
    import json

    # Hitung distribusi aktual
    actual = {
        "listening": {
            "part_a": len(listening_content.get("part_a", [])),
            "part_b": len(listening_content.get("part_b", [])),
            "part_c": len(listening_content.get("part_c", [])),
            "total": listening_content.get("total_questions", 0),
        },
        "structure": {
            "part_a": len(structure_content.get("part_a", [])),
            "part_b": len(structure_content.get("part_b", [])),
            "total": structure_content.get("total_questions", 0),
        },
        "reading": {
            "passages": reading_content.get("passages_generated", 0),
            "total": reading_content.get("total_questions", 0),
        },
    }

    # Sample soal untuk quality check
    # Listening: ambil 1 item dari setiap part
    listening_samples = []
    for part_key in ("part_a", "part_b", "part_c"):
        items = listening_content.get(part_key, [])
        if items:
            item = items[0]
            listening_samples.append(
                {
                    "part": part_key.upper().replace("_", " "),
                    "script": item["script"] + "...",
                    "questions": item["questions"][:2],
                }
            )

    # Structure: ambil 3 dari Part A dan 3 dari Part B
    structure_samples = {
        "part_a": structure_content.get("part_a", [])[:3],
        "part_b": structure_content.get("part_b", [])[:3],
    }

    # Reading: ambil passage pertama lengkap
    reading_samples = []
    passages = reading_content.get("passages", [])
    if passages:
        p = passages[0]
        reading_samples.append(
            {
                "title": p["title"],
                "passage_text": p["passage_text"][:400] + "...",
                "word_count": p["word_count"],
                "questions": p["questions"],
            }
        )

    # ── Keluarkan JSON ke variable agar tidak error di dalam f-string ──
    target_dist = json.dumps(
        {
            "listening": planner_output.get("listening", {}),
            "structure": planner_output.get("structure", {}),
            "reading": planner_output.get("reading", {}),
        },
        ensure_ascii=False,
        indent=2,
    )
    actual_dist = json.dumps(actual, ensure_ascii=False, indent=2)
    listening_sample_str = json.dumps(listening_samples, ensure_ascii=False, indent=2)
    structure_sample_str = json.dumps(structure_samples, ensure_ascii=False, indent=2)
    reading_sample_str = json.dumps(reading_samples, ensure_ascii=False, indent=2)

    return f"""Perform quality check on this generated TOEFL ITP content.

## Target Distribution (from Planner)
{target_dist}

## Actual Distribution Generated
{actual_dist}

## Listening Sample (for quality check)
{listening_sample_str}

## Structure Sample (for quality check)
{structure_sample_str}

## Reading Sample (for quality check)
{reading_sample_str}

Evaluate quality and respond with JSON only."""
