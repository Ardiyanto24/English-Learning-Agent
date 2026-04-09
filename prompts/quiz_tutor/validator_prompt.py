"""
prompts/quiz_tutor/validator_prompt.py
----------------------------------------
Prompt untuk Grammar Tutor Validator Agent.

Validator hanya mengecek struktur dan kepatuhan terhadap instruksi
Planner — bukan menilai kualitas konten soal. Pertanyaan yang dijawab
Validator:
1. Apakah jumlah soal per topik sesuai instruksi?
2. Apakah distribusi tipe soal per topik sesuai instruksi?
3. Apakah semua field wajib ada dan tidak kosong?
4. Apakah nilai question_type valid?
5. Apakah input_type konsisten dengan question_type?
"""

TUTOR_VALIDATOR_SYSTEM_PROMPT = """Kamu adalah quality checker untuk soal Grammar Tutor.

Tugasmu adalah memverifikasi apakah soal yang digenerate sesuai dengan instruksi Planner.
Kamu mengecek STRUKTUR dan KEPATUHAN saja — bukan kualitas konten atau kebenaran grammar soal.

## Kriteria Validasi (urutan pengecekan)

### Kriteria 1 — Jumlah soal per topik
Hitung jumlah soal yang digenerate untuk setiap topik.
Bandingkan dengan `question_count` per topik di planner output.
Gagal jika jumlah aktual tidak sama dengan `question_count` yang diinstruksikan.

### Kriteria 2 — Distribusi tipe soal per topik
Hitung jumlah soal per `question_type` untuk setiap topik.
Bandingkan dengan `type_distribution` per topik di planner output.
Toleransi: distribusi dianggap sesuai jika selisih maksimal 1 soal per tipe.

### Kriteria 3 — Field wajib lengkap (KRITIKAL)
Setiap soal WAJIB memiliki semua field berikut dengan nilai tidak kosong:
- `topic` — tidak boleh kosong atau null
- `question_type` — tidak boleh kosong atau null
- `question_text` — tidak boleh kosong atau null
- `reference_answer` — tidak boleh kosong atau null
- `input_type` — tidak boleh kosong atau null

EXCEPTION: Jika kriteria ini gagal pada soal manapun, `match_score` otomatis 0.0
tanpa memandang hasil kriteria lainnya. Soal tanpa field lengkap tidak boleh lolos validasi.

### Kriteria 4 — Nilai question_type valid
Setiap `question_type` harus berupa salah satu dari enam nilai berikut:
`type_1_recall`, `type_2_pattern`, `type_3_classify`,
`type_4_transform`, `type_5_error`, `type_6_reason`
Gagal jika ada nilai di luar daftar ini.

### Kriteria 5 — Konsistensi input_type dengan question_type
Nilai `input_type` harus konsisten dengan `question_type`:
- `text_input` → untuk: type_1_recall, type_2_pattern, type_3_classify, type_5_error
- `text_area`  → untuk: type_4_transform, type_6_reason
Gagal jika ada soal yang input_type-nya tidak sesuai dengan question_type-nya.

## Scoring
- `match_score` dihitung sebagai: jumlah_kriteria_lolos / 5
- Nilai `match_score >= 0.8` dianggap valid (minimal 4 dari 5 kriteria lolos)
- EXCEPTION: Jika Kriteria 3 gagal → `match_score = 0.0` dan `is_valid = false`
  tanpa memandang hasil kriteria lain

## Output Format
Respond dengan valid JSON saja. Tanpa penjelasan, tanpa markdown, tanpa teks tambahan.

{
  "is_valid": true | false,
  "match_score": float,
  "issues": ["string — deskripsikan setiap kriteria yang gagal, list kosong jika semua lolos"],
  "adjusted_questions": []
}"""


def build_validator_prompt(planner_output: dict, generator_output: dict) -> str:
    """
    Bangun user prompt untuk Grammar Tutor Validator.

    Menghitung distribusi aktual dari generator_output di layer Python
    sebelum menyusun prompt — LLM tidak perlu menghitung sendiri,
    cukup membandingkan expected vs actual yang sudah disajikan.

    Args:
        planner_output   : Output dari Tutor Planner Agent, berisi:
                           {
                             "status": "ok",
                             "total_questions": int,
                             "plan": [
                               {
                                 "topic": str,
                                 "question_count": int,
                                 "proficiency_level": str,
                                 "type_distribution": {
                                   "type_1_recall": int, ...
                                 }
                               }
                             ]
                           }
        generator_output : Output dari Tutor Generator Agent, berisi:
                           {
                             "questions": [
                               {
                                 "topic": str,
                                 "question_type": str,
                                 "question_text": str,
                                 "reference_answer": str,
                                 "input_type": str
                               }
                             ]
                           }

    Returns:
        String user prompt siap dikirim ke LLM sebagai pesan user.
    """
    import json

    plan = planner_output.get("plan", [])
    questions = generator_output.get("questions", [])
    total_expected = planner_output.get("total_questions", 0)

    # --- Hitung distribusi aktual dari generator output ---
    # actual_per_topic: { topic: { question_type: count } }
    actual_per_topic: dict = {}
    for q in questions:
        topic = q.get("topic", "unknown")
        qtype = q.get("question_type", "unknown")
        if topic not in actual_per_topic:
            actual_per_topic[topic] = {}
        actual_per_topic[topic][qtype] = actual_per_topic[topic].get(qtype, 0) + 1

    # --- Susun blok perbandingan expected vs actual per topik ---
    comparison_blocks = []
    for item in plan:
        topic = item.get("topic", "")
        expected_count = item.get("question_count", 0)
        expected_dist = item.get("type_distribution", {})

        actual_topic_data = actual_per_topic.get(topic, {})
        actual_count = sum(actual_topic_data.values())

        # Baris perbandingan per tipe soal
        type_lines = []
        all_types = [
            "type_1_recall", "type_2_pattern", "type_3_classify",
            "type_4_transform", "type_5_error", "type_6_reason",
        ]
        for qtype in all_types:
            exp = expected_dist.get(qtype, 0)
            act = actual_topic_data.get(qtype, 0)
            status = "✓" if exp == act else "✗"
            # Hanya tampilkan tipe yang expected > 0 atau actual > 0
            if exp > 0 or act > 0:
                type_lines.append(
                    f"    {status} {qtype}: expected={exp}, actual={act}"
                )

        count_status = "✓" if expected_count == actual_count else "✗"
        type_block = "\n".join(type_lines) if type_lines else "    (tidak ada soal)"

        comparison_blocks.append(
            f"Topik: {topic}\n"
            f"  {count_status} Jumlah soal: expected={expected_count}, actual={actual_count}\n"
            f"  Distribusi tipe soal:\n{type_block}"
        )

    comparison_text = "\n\n".join(comparison_blocks)

    return f"""Verifikasi soal yang digenerate terhadap instruksi Planner.

## Ringkasan
Total soal expected : {total_expected}
Total soal actual   : {len(questions)}

## Perbandingan Expected vs Actual per Topik
{comparison_text}

## Full List Soal yang Digenerate
{json.dumps(questions, ensure_ascii=False, indent=2)}

Cek kelima kriteria validasi dan respond dengan JSON only."""