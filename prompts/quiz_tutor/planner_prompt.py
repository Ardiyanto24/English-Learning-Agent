"""
prompts/quiz_tutor/planner_prompt.py
--------------------------------------
Prompt untuk Grammar Tutor Planner Agent.

Tiga perbedaan utama dari TOEFL Quiz Planner:
1. Topik dipilih sendiri oleh user — Planner tidak merekomendasikan topik.
   TOEFL Quiz Planner yang memilih topik berdasarkan weakness analysis;
   Grammar Tutor Planner hanya menerima topik pilihan user sebagai input.
2. Planner bertugas menentukan distribusi tipe soal (Tipe 1–6) berdasarkan
   proficiency level user di setiap topik — bukan menentukan format atau
   difficulty seperti TOEFL Quiz Planner.
3. Prerequisite check sudah dilakukan di layer Python sebelum LLM dipanggil
   — LLM tidak perlu mengecek prerequisite dan tidak akan menerima topik
   yang diblok. Jika ada topik yang diblok, UI menangani sebelum Planner
   dipanggil.
"""

TUTOR_PLANNER_SYSTEM_PROMPT = """Kamu adalah perencana sesi Grammar Tutor yang bertugas menyusun rencana distribusi soal berdasarkan data proficiency user.

Topik sudah dipilih oleh user dan prerequisite sudah diverifikasi sebelum kamu dipanggil. Tugasmu adalah menentukan berapa soal per tipe (Tipe 1–6) untuk setiap topik berdasarkan proficiency level user di topik tersebut.

## Tiga Proficiency Level dan Distribusi Tipe Soal

### cold_start — Topik belum pernah dilatih
User belum memiliki rekam jejak di topik ini. Fokus pada fondasi: recall definisi dan identifikasi pola dulu sebelum soal aplikasi.

| Tipe Soal        | Persentase |
|------------------|------------|
| type_1_recall    | 35%        |
| type_2_pattern   | 30%        |
| type_3_classify  | 20%        |
| type_4_transform | 10%        |
| type_5_error     | 5%         |
| type_6_reason    | 0%         |

### familiar — avg_score_pct antara 1 hingga 79
User sudah mengenal topik ini tapi belum menguasainya. Distribusi lebih merata dengan mulai memperkenalkan soal aplikasi (Tipe 4, 5, 6) secara bertahap.

| Tipe Soal        | Persentase |
|------------------|------------|
| type_1_recall    | 15%        |
| type_2_pattern   | 20%        |
| type_3_classify  | 25%        |
| type_4_transform | 20%        |
| type_5_error     | 10%        |
| type_6_reason    | 10%        |

### advanced — avg_score_pct >= 80
User sudah menguasai fondasi topik ini. Fokus pada soal aplikasi mendalam yang menantang pemahaman di level yang lebih tinggi.

| Tipe Soal        | Persentase |
|------------------|------------|
| type_1_recall    | 5%         |
| type_2_pattern   | 10%        |
| type_3_classify  | 15%        |
| type_4_transform | 30%        |
| type_5_error     | 20%        |
| type_6_reason    | 20%        |

## Distribution Rounding Rule

Total soal per tipe untuk satu topik HARUS persis sama dengan `question_count` topik tersebut — tidak boleh kurang atau lebih.

Cara menghitung:
1. Kalikan persentase setiap tipe dengan `question_count`, lalu bulatkan ke bawah (floor)
2. Hitung sisa soal: `question_count` dikurangi jumlah total setelah pembulatan
3. Tambahkan sisa soal ke tipe dengan bobot persentase tertinggi di level tersebut:
   - cold_start  → tambahkan ke type_1_recall (35%)
   - familiar    → tambahkan ke type_3_classify (25%)
   - advanced    → tambahkan ke type_4_transform (30%)

## Output Format
Respond dengan valid JSON saja. Tanpa penjelasan, tanpa markdown, tanpa teks tambahan apapun di luar JSON.

Field `status` selalu bernilai "ok" — topik yang diblok tidak akan sampai ke kamu.

Few-shot example (2 topik, proficiency level berbeda, total 10 soal):
{
  "status": "ok",
  "total_questions": 10,
  "plan": [
    {
      "topic": "Simple Past Tense",
      "question_count": 5,
      "proficiency_level": "cold_start",
      "type_distribution": {
        "type_1_recall": 2,
        "type_2_pattern": 2,
        "type_3_classify": 1,
        "type_4_transform": 0,
        "type_5_error": 0,
        "type_6_reason": 0
      }
    },
    {
      "topic": "Modal Verbs",
      "question_count": 5,
      "proficiency_level": "familiar",
      "type_distribution": {
        "type_1_recall": 1,
        "type_2_pattern": 1,
        "type_3_classify": 1,
        "type_4_transform": 1,
        "type_5_error": 1,
        "type_6_reason": 0
      }
    }
  ]
}

Respond dengan valid JSON saja. Tanpa penjelasan, tanpa markdown, tanpa teks tambahan apapun di luar JSON."""


def build_planner_prompt(
    selected_topics: list,
    total_questions: int,
    topic_history: dict,
    question_distribution: dict[str, int] | None = None,
) -> str:
    """
    Bangun user prompt untuk Grammar Tutor Planner.

    Menentukan proficiency level per topik di layer Python berdasarkan
    data historis dari DB, lalu menyajikan instruksi lengkap kepada LLM
    untuk menghitung distribusi tipe soal sesuai level tersebut.

    Args:
        selected_topics : List string topik yang dipilih user di UI,
                          contoh: ["Simple Past Tense", "Modal Verbs"]
        total_questions : Integer jumlah soal yang dipilih user (5/10/15/20)
        topic_history   : Dict dengan topic sebagai key dan dict data
                          historis sebagai value, dikompilasi dari
                          tutor_topic_tracking di DB sebelum fungsi ini
                          dipanggil. Contoh:
                          {
                            "Modal Verbs": {
                              "avg_score_pct": 72.5,
                              "total_sessions": 3
                            }
                          }
                          Topik yang belum pernah dilatih tidak akan
                          memiliki entry di dict ini.

    Returns:
        String user prompt siap dikirim ke LLM sebagai pesan user.
    """
    # --- Tentukan proficiency level per topik di layer Python ---
    topic_levels = []
    for topic in selected_topics:
        history = topic_history.get(topic)
        if history is None:
            level = "cold_start"
            avg_score = None
            total_sessions = 0
        else:
            avg_score = history.get("avg_score_pct", 0)
            total_sessions = history.get("total_sessions", 0)
            level = "advanced" if avg_score >= 80 else "familiar"
        topic_levels.append(
            {
                "topic": topic,
                "level": level,
                "avg_score": avg_score,
                "total_sessions": total_sessions,
            }
        )

    # --- Susun blok info per topik ---
    topic_blocks = []
    for t in topic_levels:
        if t["avg_score"] is None:
            history_str = "Belum pernah dilatih (cold start)"
        else:
            history_str = (
                f"avg_score_pct={t['avg_score']:.1f}%, " f"total_sessions={t['total_sessions']}"
            )
        topic_blocks.append(
            f"- {t['topic']}\n"
            f"  Proficiency level : {t['level']}\n"
            f"  Riwayat           : {history_str}"
        )

    topics_text = "\n".join(topic_blocks)

    # --- Persentase distribusi per level sebagai referensi di prompt ---
    distribution_ref = """Persentase distribusi tipe soal per level (gunakan ini sebagai acuan):

cold_start  → type_1_recall=35%, type_2_pattern=30%, type_3_classify=20%,
              type_4_transform=10%, type_5_error=5%, type_6_reason=0%

familiar    → type_1_recall=15%, type_2_pattern=20%, type_3_classify=25%,
              type_4_transform=20%, type_5_error=10%, type_6_reason=10%

advanced    → type_1_recall=5%, type_2_pattern=10%, type_3_classify=15%,
              type_4_transform=30%, type_5_error=20%, type_6_reason=20%"""

    return f"""Susun rencana distribusi soal Grammar Tutor berdasarkan data berikut.

## Topik yang Dipilih User
{topics_text}

## Total Soal
{total_questions} soal — dibagi merata ke semua topik di atas.
Topik dengan avg_score_pct terendah mendapat soal ekstra (+1) jika tidak habis dibagi.
Topik cold_start diprioritaskan mendapat soal ekstra jika ada beberapa topik cold_start.

## {distribution_ref}

## Tugasmu
Untuk setiap topik:
1. Gunakan proficiency level yang sudah ditetapkan di atas — jangan ubah
2. Hitung question_count masing-masing topik (total {total_questions} soal dibagi merata)
3. Hitung distribusi tipe soal sesuai persentase level topik tersebut
4. Pastikan jumlah semua tipe soal = question_count topik tersebut (gunakan rounding rule)

Respond dengan JSON only."""
