"""
prompts/speaking/assessor_prompt.py
--------------------------------------
Prompt untuk Conversation Assessor Agent.

Agent unik yang tidak ada di Vocab/Quiz.
Tugasnya: setelah setiap giliran bicara user,
putuskan apakah conversation harus lanjut, stop, atau ganti subtopik.

Menggunakan SLIDING WINDOW — hanya 3-5 exchange terakhir yang dikirim,
bukan seluruh transcript. Ini mencegah LLM "terdistraksi" oleh
konteks awal yang sudah tidak relevan.

3 keputusan yang mungkin:
- continue     : topik belum habis, lanjutkan dengan follow-up question
- stop         : topik sudah exhausted atau batas exchange tercapai
- new_subtopic : jawaban bagus tapi perlu energi baru untuk lanjut
"""

SPEAKING_ASSESSOR_SYSTEM_PROMPT = """You are a conversation flow assessor for an English \
speaking practice application.

Your task is to decide whether the conversation should continue, stop, or move to a \
new subtopic — based on the student's most recent response and the conversation context.

## Decision Rules

### "continue"
Use when:
- Student's answer was incomplete or ambiguous (needs clarification or elaboration)
- Topic has clear room for deeper discussion
- Exchange count is below the threshold for the sub-mode
Generate a natural follow-up question in `suggested_followup`.

### "stop"
Use when:
- Topic has been fully explored (answer was comprehensive)
- Exchange count has reached the sub-mode limit
- For prompted_response: always stop after 3 exchanges maximum

### "new_subtopic"
Use when:
- Student gave a good, complete answer
- BUT the conversation needs fresh energy to stay engaging
- Only for conversation_practice (fase 2: exchange 10-15)
Generate a related but new angle in `suggested_followup`.

## Sub-Mode Exchange Limits
- prompted_response    : max 3 exchanges → then STOP
- conversation_practice: 
  - fase 1 (< 10 exchanges): NEVER stop, always continue or new_subtopic
  - fase 2 (10-15 exchanges): may stop if topic naturally closed
- oral_presentation    : no assessor needed (monologue, not dialogue)

## Output Format
Respond with valid JSON only. No explanation, no markdown.

{
  "decision": "continue | stop | new_subtopic",
  "reason": "string — brief explanation of the decision",
  "suggested_followup": "string | null — only if continue or new_subtopic"
}

Examples:

continue:
{
  "decision": "continue",
  "reason": "Student mentioned time management strategies but didn't elaborate on which was most effective.",
  "suggested_followup": "That's interesting — which of those strategies do you rely on most when things get overwhelming?"
}

stop:
{
  "decision": "stop",
  "reason": "Student gave a comprehensive answer covering both pros and cons. Topic fully explored.",
  "suggested_followup": null
}

new_subtopic:
{
  "decision": "new_subtopic",
  "reason": "Good response, but the discussion on AI automation has been fully covered. Shifting to related topic.",
  "suggested_followup": "You've made great points about automation. What about AI in healthcare specifically — do you think it's a positive development?"
}"""


def build_assessor_prompt(
    sub_mode: str,
    exchange_count: int,
    conversation_window: list[dict],
    main_topic: str,
    latest_transcript: str,
) -> str:
    """
    Bangun user prompt untuk Conversation Assessor.

    Menggunakan sliding window — hanya kirim 3-5 exchange terakhir
    ke LLM, bukan seluruh history.

    Args:
        sub_mode            : "prompted_response" | "conversation_practice"
        exchange_count      : Total exchange yang sudah terjadi
        conversation_window : List 3-5 exchange terakhir:
                              [{"role": "ai"|"user", "text": str}, ...]
        main_topic          : Topik utama sesi (untuk context)
        latest_transcript   : Transkrip jawaban user terbaru

    Returns:
        String user prompt siap dikirim ke LLM
    """
    # Format conversation window menjadi teks yang readable
    window_text = "\n".join(
        f"[{'AI' if ex.get('role') == 'ai' else 'Student'}]: {ex.get('text', '')}"
        for ex in conversation_window
    )

    # Tentukan fase untuk conversation_practice
    phase_note = ""
    if sub_mode == "conversation_practice":
        if exchange_count < 10:
            phase_note = "Phase 1 (< 10 exchanges): Do NOT stop. Always continue or new_subtopic."
        else:
            phase_note = "Phase 2 (10-15 exchanges): May stop if topic naturally closed."
    elif sub_mode == "prompted_response":
        remaining = max(0, PROMPTED_RESPONSE_MAX - exchange_count)
        phase_note = (
            f"Max {PROMPTED_RESPONSE_MAX} exchanges for prompted_response. "
            f"Exchanges used: {exchange_count}. "
            f"Remaining: {remaining}."
        )

    return f"""Assess the conversation and decide whether to continue, stop, or move to a new subtopic.

## Session Context
Sub-mode       : {sub_mode}
Main topic     : {main_topic}
Exchange count : {exchange_count}
{phase_note}

## Recent Conversation (last {len(conversation_window)} exchanges)
{window_text}

## Student's Latest Response
{latest_transcript}

Assess and respond with JSON only."""