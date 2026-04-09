"""
prompts/quiz_tutor/analytics_prompt.py
----------------------------------------
Prompt untuk Grammar Tutor Analytics Agent.

Perbedaan dari TOEFL Quiz Analytics:
1. Fokus pada pemahaman konseptual — bukan aplikasi soal TOEFL.
   TOEFL Quiz Analytics melihat coverage dan prerequisite bottleneck;
   Grammar Tutor Analytics melihat pola no_credit per tipe soal dan
   topik yang sering partial_credit.
2. Insight diarahkan pada dua dimensi pemahaman yang berbeda:
   - Recall (Tipe 1, 2, 3): apakah user hafal definisi dan pola dasar?
   - Aplikasi (Tipe 4, 5, 6): apakah user bisa menggunakan rule dalam
     konteks nyata?
   Kelemahan di recall dan kelemahan di aplikasi membutuhkan
   pendekatan latihan yang berbeda.
"""

TUTOR_ANALYTICS_SYSTEM_PROMPT = """Kamu adalah analytics engine untuk Grammar Tutor yang bertugas menganalisis pola pemahaman konseptual user dan menghasilkan insight yang actionable.

Tugasmu adalah membaca data latihan Grammar Tutor dan mengidentifikasi di mana user lemah, jenis kelemahan apa yang dimilikinya, dan apa yang sebaiknya difokuskan di sesi berikutnya.

## Fokus Analisis

### 1. Topik Terlemah
Identifikasi topik dengan `avg_score_pct` terendah yang membutuhkan latihan lebih.
Urutkan dari yang paling lemah. Sertakan angka spesifik di insight.

### 2. Tipe Soal dengan No-Credit Rate Tertinggi
Hitung no_credit rate per tipe soal: berapa persen soal dari setiap tipe yang menghasilkan `no_credit`.
Tipe soal dengan no_credit rate tinggi menunjukkan jenis pemahaman yang paling lemah.
Ini lebih informatif dari sekadar "topik lemah" karena menunjukkan JENIS kesulitan user.

### 3. Pola Partial Credit
Topik yang sering menghasilkan `partial_credit` menandakan user paham konsepnya secara umum tetapi tidak presisi dalam menjelaskan — berbeda dari topik yang menghasilkan `no_credit` yang menandakan konsep belum dipahami sama sekali.
Bedakan dua kondisi ini di insight karena rekomendasinya berbeda.

### 4. Recall vs Application
Bandingkan performa user di dua kelompok tipe soal:
- Recall (Tipe 1, 2, 3): menguji apakah user hafal definisi, rumus, dan pola dasar
- Aplikasi (Tipe 4, 5, 6): menguji apakah user bisa menggunakan rule dalam konteks nyata

Interpretasi pola:
- Recall rendah, Aplikasi rendah → user belum memahami fondasi konsep
- Recall tinggi, Aplikasi rendah → user hafal teori tapi belum bisa mengaplikasikan
- Recall rendah, Aplikasi tinggi → jarang terjadi, mungkin user belajar dari contoh bukan definisi

### 5. Rekomendasi Sesi Berikutnya
Berikan rekomendasi spesifik dan actionable: topik mana yang harus dilatih ulang, tipe soal apa yang perlu diperbanyak, dan apakah user sebaiknya fokus memperkuat recall atau meningkatkan kemampuan aplikasi.

## Insight Quality Rules
- Referensikan TOPIK dan ANGKA SPESIFIK dari data — bukan pernyataan generik seperti "kamu perlu lebih banyak latihan"
- Bedakan "tidak paham konsep" (no_credit tinggi di Tipe 1 dan 2) dari "paham tapi tidak bisa mengaplikasikan" (no_credit tinggi di Tipe 4, 5, dan 6)
- Nada harus warm dan encouraging — user sedang belajar, bukan diuji
- Jika data terbatas, sampaikan insight berdasarkan data yang ada tanpa berspekulasi

## Output Format
Respond dengan valid JSON saja. Tanpa penjelasan, tanpa markdown, tanpa teks tambahan apapun di luar JSON.

{
  "weak_topics": [
    {"topic": "string", "avg_score_pct": float}
  ],
  "weak_question_types": ["string — tipe soal dengan no_credit rate tertinggi, diurutkan dari terburuk"],
  "recall_vs_application": {
    "recall_avg": float,
    "application_avg": float
  },
  "pattern_insight": "string — analisis pola utama yang ditemukan dari data, referensikan angka spesifik",
  "recommendations": ["string — rekomendasi spesifik dan actionable, minimal 2 maksimal 4"],
  "overall_insight": "string — ringkasan keseluruhan dalam Bahasa Indonesia, nada encouraging"
}"""


def build_tutor_analytics_prompt(
    sessions_data: list,
    topic_tracking_data: list,
    questions_data: list,
) -> str:
    """
    Bangun user prompt untuk Grammar Tutor Analytics Agent.

    Melakukan empat preprocessing di layer Python sebelum menyusun prompt
    agar LLM menerima data siap analisis — bukan raw data mentah.

    Args:
        sessions_data       : List dict dari tabel tutor_sessions,
                              berisi metadata dan skor setiap sesi
                              Grammar Tutor yang sudah selesai.
        topic_tracking_data : List dict dari tabel tutor_topic_tracking,
                              berisi akumulasi performa user per topik
                              grammar lintas seluruh sesi.
        questions_data      : List dict dari tabel tutor_questions,
                              berisi setiap soal beserta credit_level
                              dan score yang sudah dinilai Corrector.

    Returns:
        String user prompt siap dikirim ke LLM sebagai pesan user.
    """
    import json

    # --- Preprocessing 1: Total sesi dan total soal ---
    total_sessions = len(sessions_data)
    total_questions = len(questions_data)

    # --- Preprocessing 2: No-credit rate per question_type ---
    # Hitung jumlah soal dan jumlah no_credit per tipe
    type_counts: dict = {}
    type_no_credit: dict = {}

    for q in questions_data:
        qtype = q.get("question_type", "unknown")
        credit = q.get("credit_level", "")

        type_counts[qtype] = type_counts.get(qtype, 0) + 1
        if credit == "no_credit":
            type_no_credit[qtype] = type_no_credit.get(qtype, 0) + 1

    # Hitung rate (%) per tipe, urutkan dari no_credit rate tertinggi
    no_credit_rates: list = []
    for qtype, count in type_counts.items():
        no_credit_count = type_no_credit.get(qtype, 0)
        rate = round(no_credit_count / count * 100, 1) if count > 0 else 0.0
        no_credit_rates.append(
            {
                "question_type": qtype,
                "total": count,
                "no_credit_count": no_credit_count,
                "no_credit_rate_pct": rate,
            }
        )
    no_credit_rates.sort(key=lambda x: x["no_credit_rate_pct"], reverse=True)

    # --- Preprocessing 3: Recall vs Application average score ---
    recall_types = {"type_1_recall", "type_2_pattern", "type_3_classify"}
    application_types = {"type_4_transform", "type_5_error", "type_6_reason"}

    recall_scores: list = []
    application_scores: list = []

    for q in questions_data:
        qtype = q.get("question_type", "")
        score = q.get("score")
        # Hanya hitung soal yang sudah dinilai (score tidak None)
        if score is None:
            continue
        if qtype in recall_types:
            recall_scores.append(score)
        elif qtype in application_types:
            application_scores.append(score)

    recall_avg = round(sum(recall_scores) / len(recall_scores) * 100, 1) if recall_scores else None
    application_avg = (
        round(sum(application_scores) / len(application_scores) * 100, 1)
        if application_scores
        else None
    )

    # Format untuk tampilan di prompt
    recall_display = f"{recall_avg}%" if recall_avg is not None else "Belum ada data"
    application_display = f"{application_avg}%" if application_avg is not None else "Belum ada data"

    # --- Preprocessing 4: Ambil 10 sesi terakhir ---
    recent_sessions = sessions_data[-10:] if len(sessions_data) > 10 else sessions_data
    recent_scores = [s.get("score_pct", 0) for s in recent_sessions]

    # --- Format no_credit rate untuk tampilan di prompt ---
    no_credit_lines = (
        "\n".join(
            f"  {item['question_type']}: {item['no_credit_rate_pct']}% "
            f"({item['no_credit_count']}/{item['total']} soal)"
            for item in no_credit_rates
        )
        if no_credit_rates
        else "  Belum ada data"
    )

    return f"""Analisis data latihan Grammar Tutor berikut dan hasilkan insight yang actionable.

## Ringkasan Statistik
Total sesi selesai : {total_sessions}
Total soal dinilai : {total_questions}
Skor sesi terkini  : {recent_scores}

## No-Credit Rate per Tipe Soal (diurutkan dari tertinggi)
{no_credit_lines}

## Recall vs Application
Recall (Tipe 1, 2, 3)     : {recall_display}
Aplikasi (Tipe 4, 5, 6)   : {application_display}

## Data Topic Tracking (semua topik yang pernah dilatih)
{json.dumps(topic_tracking_data, ensure_ascii=False, indent=2)}

## Riwayat 10 Sesi Terakhir
{json.dumps(recent_sessions, ensure_ascii=False, indent=2)}

Analisis pola pemahaman user berdasarkan data di atas.
Hasilkan insight yang referensikan topik dan angka spesifik — bukan pernyataan generik.
Respond dengan JSON only."""
