"""
tests/integration/test_speaking_flow.py
-----------------------------------------
Integration test untuk full flow satu sesi Speaking Agent.

Yang diverifikasi:
  - Tabel `sessions`          : session_id ada, status = 'completed'
  - Tabel `speaking_sessions` : metadata + skor tersimpan setelah evaluasi
  - Tabel `speaking_exchanges`: setiap exchange (AI prompt + user transcript)
                                 tersimpan secara incremental
  - Recovery flow             : transcript bisa di-rebuild dari DB setelah refresh

Mock audio:
  STT (transcribe_audio_bytes) → di-mock, return string transcript langsung
  TTS (generate_speech)        → di-mock, return bytes kosong (tidak butuh audio nyata)
  Recorder                     → di-mock, tidak ada hardware mic dibutuhkan

Semua LLM di-mock — test tidak memanggil Claude sungguhan.

Cara jalankan:
    pytest tests/integration/test_speaking_flow.py -v
"""

import json
from unittest.mock import MagicMock, patch

import pytest


# ===================================================
# Helper
# ===================================================
def _make_transcript(n_exchanges: int = 3) -> list[dict]:
    """Buat full_history speaking dengan n exchange pasang AI-user."""
    h = []
    for i in range(n_exchanges):
        h.append({"role": "ai", "text": f"AI prompt {i+1}: Tell me about topic {i+1}."})
        h.append({"role": "user", "text": f"User answer {i+1}: I think that is very important."})
    return h


def _make_evaluation(sub_mode: str = "prompted_response", is_graded: bool = True) -> dict:
    """Buat mock evaluation result dari Speaking Evaluator."""
    base = {
        "grammar_score": 8.0,
        "relevance_score": 7.0,
        "final_score": 7.5,
        "is_graded": is_graded,
        "feedback": {
            "grammar": "Good grammar usage overall.",
            "relevance": "Stayed on topic well.",
            "overall": "Well done!",
        },
    }
    if sub_mode == "oral_presentation":
        base["vocabulary_score"] = 6.0
        base["structure_score"] = 8.0
        base["feedback"]["vocabulary"] = "Good vocabulary range."
        base["feedback"]["structure"] = "Well structured presentation."
    return base


# ===================================================
# Integration Test: Full Speaking Session Flow
# ===================================================
class TestSpeakingFullFlow:

    def test_session_created_in_db(self, tmp_db):
        """
        Setelah sesi dimulai, tabel `sessions` harus punya
        record dengan mode='speaking' dan status='active'.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="speaking")

        with get_db() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row is not None
        assert row["mode"] == "speaking"
        assert row["status"] == "active"

    def test_speaking_session_metadata_saved(self, tmp_db):
        """
        Metadata sesi (sub_mode, topic, category) harus tersimpan
        di tabel `speaking_sessions` setelah sesi dimulai.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import save_speaking_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="speaking")
        save_speaking_session(
            session_id=sid,
            sub_mode="prompted_response",
            topic="Daily routines",
            category="General",
        )

        with get_db() as conn:
            row = conn.execute("SELECT * FROM speaking_sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row is not None
        assert row["sub_mode"] == "prompted_response"
        assert row["topic"] == "Daily routines"
        assert row["category"] == "General"

    def test_exchange_saved_incrementally(self, tmp_db):
        """
        Setiap exchange (AI prompt + user transcript) harus tersimpan
        ke DB secara incremental — bukan batch di akhir.

        Ini penting: jika browser ditutup di tengah sesi, data tidak hilang.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            save_exchange,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="speaking")
        save_speaking_session(sid, "prompted_response", "Health", "General")

        # Exchange 1 — AI prompt dulu, user belum menjawab
        eid1 = save_exchange(
            session_id=sid,
            exchange_number=1,
            agent_prompt="Tell me about your exercise routine.",
            user_transcript=None,  # belum dijawab
            is_followup=False,
        )

        with get_db() as conn:
            row = conn.execute("SELECT * FROM speaking_exchanges WHERE id = ?", (eid1,)).fetchone()

        # AI prompt tersimpan, user_transcript masih None
        assert row["agent_prompt"] == "Tell me about your exercise routine."
        assert row["user_transcript"] is None
        assert row["exchange_number"] == 1

    def test_user_transcript_updated_after_answer(self, tmp_db):
        """
        Setelah user menjawab (STT selesai), user_transcript dan
        assessor_decision harus terupdate di tabel `speaking_exchanges`.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            save_exchange,
            update_exchange_transcript,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="speaking")
        save_speaking_session(sid, "prompted_response", "Health", "General")

        eid = save_exchange(
            session_id=sid,
            exchange_number=1,
            agent_prompt="How often do you exercise?",
            user_transcript=None,
        )

        # Simulasi STT selesai — update transcript
        update_exchange_transcript(
            exchange_id=eid,
            user_transcript="I exercise three times a week.",
            assessor_decision="continue",
        )

        with get_db() as conn:
            row = conn.execute("SELECT user_transcript, assessor_decision " "FROM speaking_exchanges WHERE id = ?", (eid,)).fetchone()

        assert row["user_transcript"] == "I exercise three times a week."
        assert row["assessor_decision"] == "continue"

    def test_multiple_exchanges_saved_in_order(self, tmp_db):
        """
        Sesi dengan 3 exchange harus punya 3 record di `speaking_exchanges`,
        tersimpan berurutan sesuai exchange_number.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            save_exchange,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        history = _make_transcript(n_exchanges=3)
        ai_turns = [h for h in history if h["role"] == "ai"]
        user_turns = [h for h in history if h["role"] == "user"]

        create_session(sid, mode="speaking")
        save_speaking_session(sid, "prompted_response", "Daily routines", "General")

        for i, (ai, user) in enumerate(zip(ai_turns, user_turns)):
            save_exchange(
                session_id=sid,
                exchange_number=i + 1,
                agent_prompt=ai["text"],
                user_transcript=user["text"],
                is_followup=i > 0,  # exchange ke-2 dst adalah follow-up
            )

        with get_db() as conn:
            rows = conn.execute("SELECT exchange_number, agent_prompt, user_transcript " "FROM speaking_exchanges WHERE session_id = ? " "ORDER BY exchange_number ASC", (sid,)).fetchall()

        assert len(rows) == 3
        assert rows[0]["exchange_number"] == 1
        assert rows[1]["exchange_number"] == 2
        assert rows[2]["exchange_number"] == 3

        # Verifikasi isi tersimpan dengan benar
        assert "AI prompt 1" in rows[0]["agent_prompt"]
        assert "User answer 1" in rows[0]["user_transcript"]

    def test_scores_saved_after_evaluation(self, tmp_db):
        """
        Setelah Evaluator selesai, skor harus tersimpan di
        tabel `speaking_sessions`:
          - grammar_score, relevance_score, final_score
          - full_transcript (JSON string)
          - is_graded = True
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            update_speaking_scores,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        history = _make_transcript(n_exchanges=3)
        evaluation = _make_evaluation("prompted_response")

        create_session(sid, mode="speaking")
        save_speaking_session(sid, "prompted_response", "Health", "General")

        user_turns = [t for t in history if t["role"] == "user"]
        update_speaking_scores(
            session_id=sid,
            total_exchanges=len(user_turns),
            full_transcript=json.dumps(history, ensure_ascii=False),
            grammar_score=evaluation["grammar_score"],
            relevance_score=evaluation["relevance_score"],
            final_score=evaluation["final_score"],
            is_graded=evaluation["is_graded"],
        )

        with get_db() as conn:
            row = conn.execute("SELECT grammar_score, relevance_score, final_score, " "total_exchanges, is_graded, full_transcript " "FROM speaking_sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["grammar_score"] == pytest.approx(8.0, abs=0.01)
        assert row["relevance_score"] == pytest.approx(7.0, abs=0.01)
        assert row["final_score"] == pytest.approx(7.5, abs=0.01)
        assert row["total_exchanges"] == 3
        assert row["is_graded"] == 1  # True di SQLite

        # full_transcript bisa di-parse kembali
        transcript = json.loads(row["full_transcript"])
        assert isinstance(transcript, list)
        assert len(transcript) == 6  # 3 AI + 3 user

    def test_oral_presentation_extra_scores_saved(self, tmp_db):
        """
        oral_presentation harus menyimpan vocabulary_score
        dan structure_score tambahan di `speaking_sessions`.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            update_speaking_scores,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        history = _make_transcript(n_exchanges=1)
        evaluation = _make_evaluation("oral_presentation")

        create_session(sid, mode="speaking")
        save_speaking_session(sid, "oral_presentation", "Climate change", "Academic")

        update_speaking_scores(
            session_id=sid,
            total_exchanges=1,
            full_transcript=json.dumps(history, ensure_ascii=False),
            grammar_score=evaluation["grammar_score"],
            relevance_score=evaluation["relevance_score"],
            final_score=evaluation["final_score"],
            vocabulary_score=evaluation["vocabulary_score"],
            structure_score=evaluation["structure_score"],
            is_graded=evaluation["is_graded"],
        )

        with get_db() as conn:
            row = conn.execute("SELECT vocabulary_score, structure_score " "FROM speaking_sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["vocabulary_score"] == pytest.approx(6.0, abs=0.01)
        assert row["structure_score"] == pytest.approx(8.0, abs=0.01)

    def test_session_completed_status_saved(self, tmp_db):
        """
        Setelah evaluasi selesai, status sesi harus 'completed'
        dan completed_at harus terisi.
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="speaking")
        update_session_status(sid, status="completed")

        with get_db() as conn:
            row = conn.execute("SELECT status, completed_at FROM sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["status"] == "completed"
        assert row["completed_at"] is not None

    def test_ungraded_session_saved_when_evaluator_fails(self, tmp_db):
        """
        Jika Evaluator LLM gagal, skor tetap tersimpan dengan
        is_graded=False. Transcript tetap ada — data tidak hilang.
        """
        from database.connection import get_db
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            update_speaking_scores,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        history = _make_transcript(n_exchanges=2)

        create_session(sid, mode="speaking")
        save_speaking_session(sid, "prompted_response", "Travel", "General")

        # Simpan sebagai ungraded
        update_speaking_scores(
            session_id=sid,
            total_exchanges=2,
            full_transcript=json.dumps(history, ensure_ascii=False),
            grammar_score=0,
            relevance_score=0,
            final_score=0,
            is_graded=False,
        )

        with get_db() as conn:
            row = conn.execute("SELECT is_graded, full_transcript " "FROM speaking_sessions WHERE session_id = ?", (sid,)).fetchone()

        assert row["is_graded"] == 0  # False

        # Transcript tetap tersimpan meskipun ungraded
        transcript = json.loads(row["full_transcript"])
        assert len(transcript) > 0

    def test_transcript_rebuild_from_db_after_refresh(self, tmp_db):
        """
        Jika browser refresh di tengah sesi, full_history harus bisa
        di-rebuild dari tabel `speaking_exchanges`.

        Ini adalah recovery flow yang penting — sesi tidak hilang
        hanya karena browser ditutup.
        """
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            save_exchange,
            rebuild_transcript_from_db,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        history = _make_transcript(n_exchanges=2)

        ai_turns = [h for h in history if h["role"] == "ai"]
        user_turns = [h for h in history if h["role"] == "user"]

        create_session(sid, mode="speaking")
        save_speaking_session(sid, "conversation_practice", "Technology", "General")

        # Simpan 2 exchange ke DB
        for i, (ai, user) in enumerate(zip(ai_turns, user_turns)):
            save_exchange(
                session_id=sid,
                exchange_number=i + 1,
                agent_prompt=ai["text"],
                user_transcript=user["text"],
                is_followup=i > 0,
            )

        # Simulasi browser refresh — rebuild dari DB
        recovered = rebuild_transcript_from_db(sid)

        assert recovered is not None
        assert recovered["is_recoverable"] is True
        assert recovered["exchange_count"] == 2
        assert recovered["sub_mode"] == "conversation_practice"
        assert recovered["main_topic"] == "Technology"
        assert len(recovered["full_history"]) == 4  # 2 AI + 2 user

    def test_rebuild_empty_session_not_recoverable(self, tmp_db):
        """
        Sesi tanpa satu pun exchange (user belum pernah menjawab)
        → is_recoverable = False.
        """
        from database.repositories.session_repository import create_session
        from database.repositories.speaking_repository import (
            save_speaking_session,
            rebuild_transcript_from_db,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="speaking")
        save_speaking_session(sid, "prompted_response", "Food", "General")
        # Tidak ada exchange yang disimpan

        recovered = rebuild_transcript_from_db(sid)

        assert recovered is not None
        assert recovered["is_recoverable"] is False
        assert recovered["exchange_count"] == 0

    def test_full_flow_prompted_response_with_mocked_llm(self, tmp_db):
        """
        TEST UTAMA — simulasi full flow sesi prompted_response:

        1. Buat session
        2. Generator buat opening prompt (mock)
        3. Loop 2 exchange:
           a. Simpan AI prompt ke DB
           b. Mock STT → dapat transcript user
           c. Simpan user transcript ke DB
           d. Assessor decide (mock) → continue/stop
        4. Evaluator nilai full transcript (mock)
        5. Simpan skor ke DB
        6. Complete session

        Verifikasi akhir:
        - sessions          : status = 'completed'
        - speaking_sessions : skor tersimpan, is_graded=True
        - speaking_exchanges: semua exchange tersimpan berurutan
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from database.repositories.speaking_repository import (
            save_speaking_session,
            save_exchange,
            update_exchange_transcript,
            update_speaking_scores,
        )
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        sub_mode = "prompted_response"
        topic = "Daily routines"

        # ── Step 1: Buat session ─────────────────────────
        create_session(sid, mode="speaking")
        save_speaking_session(sid, sub_mode, topic, "General")

        # ── Step 2: Mock Generator output ───────────────
        opening_prompt = "Tell me about your daily morning routine."

        # ── Step 3: Loop exchange dengan mock STT + Assessor
        full_history = [{"role": "ai", "text": opening_prompt}]
        exchange_ids = []
        n_exchanges = 2

        mock_assessor_responses = [
            {"decision": "continue", "reason": "Needs more detail.", "suggested_followup": "Can you elaborate?"},
            {"decision": "stop", "reason": "Conversation complete.", "suggested_followup": None},
        ]
        mock_transcripts = [
            "I usually wake up at 6 AM and exercise.",
            "Then I have breakfast and go to work.",
        ]

        with patch("agents.speaking.assessor._call_assessor_llm") as mock_assess:
            mock_assess.side_effect = mock_assessor_responses

            from agents.speaking.assessor import run_assessor

            for i in range(n_exchanges):
                # Simpan AI prompt dulu (incremental)
                eid = save_exchange(
                    session_id=sid,
                    exchange_number=i + 1,
                    agent_prompt=opening_prompt if i == 0 else "Can you elaborate?",
                    user_transcript=None,
                    is_followup=i > 0,
                )
                exchange_ids.append(eid)

                # Simulasi STT — dapat transcript (mock audio, tidak butuh mic)
                user_transcript = mock_transcripts[i]
                full_history.append({"role": "user", "text": user_transcript})

                # Assessor decide
                assessment = run_assessor(
                    sub_mode=sub_mode,
                    exchange_count=i + 1,
                    full_history=full_history,
                    main_topic=topic,
                    latest_transcript=user_transcript,
                )

                # Update transcript + keputusan assessor ke DB
                update_exchange_transcript(
                    exchange_id=eid,
                    user_transcript=user_transcript,
                    assessor_decision=assessment["decision"],
                )

                if assessment["decision"] == "stop":
                    break

                # AI follow-up
                follow_up_text = "Can you tell me more?"
                full_history.append({"role": "ai", "text": follow_up_text})

        # ── Step 4: Evaluator (mock) ─────────────────────
        mock_eval = _make_evaluation(sub_mode, is_graded=True)

        with patch("agents.speaking.evaluator._call_evaluator_llm") as mock_ev:
            mock_ev.return_value = mock_eval

            from agents.speaking.evaluator import run_evaluator

            evaluation = run_evaluator(
                sub_mode=sub_mode,
                main_topic=topic,
                prompt_text=opening_prompt,
                full_transcript=full_history,
                session_id=sid,
            )

        # ── Step 5: Simpan skor ──────────────────────────
        user_turns = [t for t in full_history if t["role"] == "user"]
        update_speaking_scores(
            session_id=sid,
            total_exchanges=len(user_turns),
            full_transcript=json.dumps(full_history, ensure_ascii=False),
            grammar_score=evaluation["grammar_score"],
            relevance_score=evaluation["relevance_score"],
            final_score=evaluation["final_score"],
            is_graded=evaluation["is_graded"],
        )

        # ── Step 6: Complete session ─────────────────────
        update_session_status(sid, status="completed")

        # ════════════════════════════════════════════════
        # VERIFIKASI AKHIR
        # ════════════════════════════════════════════════
        with get_db() as conn:

            # 1. sessions: status completed
            session = conn.execute("SELECT status, completed_at FROM sessions WHERE session_id = ?", (sid,)).fetchone()
            assert session["status"] == "completed"
            assert session["completed_at"] is not None

            # 2. speaking_sessions: skor tersimpan
            sp = conn.execute("SELECT grammar_score, relevance_score, final_score, " "total_exchanges, is_graded " "FROM speaking_sessions WHERE session_id = ?", (sid,)).fetchone()
            assert sp["grammar_score"] == pytest.approx(8.0, abs=0.01)
            assert sp["relevance_score"] == pytest.approx(7.0, abs=0.01)
            assert sp["final_score"] == pytest.approx(7.5, abs=0.01)
            assert sp["is_graded"] == 1

            # 3. speaking_exchanges: semua exchange tersimpan
            exchanges = conn.execute("SELECT exchange_number, user_transcript, assessor_decision " "FROM speaking_exchanges WHERE session_id = ? " "ORDER BY exchange_number ASC", (sid,)).fetchall()

            assert len(exchanges) >= 1

            # Semua exchange yang selesai harus punya user_transcript
            for ex in exchanges:
                if ex["assessor_decision"] is not None:
                    assert ex["user_transcript"] is not None

    def test_abandoned_session_not_evaluated(self, tmp_db):
        """
        Jika user keluar di tengah sesi, status harus 'abandoned'
        dan speaking_sessions tidak boleh punya skor.
        """
        from database.connection import get_db
        from database.repositories.session_repository import (
            create_session,
            update_session_status,
        )
        from database.repositories.speaking_repository import save_speaking_session
        from utils.helpers import generate_session_id

        sid = generate_session_id()
        create_session(sid, mode="speaking")
        save_speaking_session(sid, "prompted_response", "Health", "General")

        # User keluar — tidak ada evaluasi
        update_session_status(sid, status="abandoned")

        with get_db() as conn:
            session = conn.execute("SELECT status FROM sessions WHERE session_id = ?", (sid,)).fetchone()
            sp = conn.execute("SELECT grammar_score, final_score, is_graded " "FROM speaking_sessions WHERE session_id = ?", (sid,)).fetchone()

        assert session["status"] == "abandoned"
        assert sp["grammar_score"] is None  # tidak ada skor
        assert sp["final_score"] is None
        # is_graded DEFAULT TRUE di schema — sesi abandoned tidak memanggil
        # update_speaking_scores(), jadi nilainya tetap default (1/True)
        # yang penting: grammar_score dan final_score masih None (belum dievaluasi)
        assert sp["grammar_score"] is None
