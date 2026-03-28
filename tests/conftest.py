"""
tests/conftest.py
------------------
Pytest fixtures yang dipakai bersama oleh semua test.

File ini dibaca otomatis oleh pytest — tidak perlu di-import manual.
"""

import json
from unittest.mock import MagicMock

import pytest


# ===================================================
# Fixture: Temporary DB
# ===================================================
@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """
    Buat SQLite DB baru di folder temp pytest.
    Patch DATABASE_PATH agar semua kode pakai DB ini,
    bukan DB production.

    Setiap test function yang pakai fixture ini
    mendapat DB bersih yang fresh — tidak saling mencemari.
    """
    db_path = str(tmp_path / "test_agent.db")

    # Patch env var dan atribut modul sekaligus
    monkeypatch.setenv("DATABASE_PATH", db_path)

    import database.connection as conn_module

    monkeypatch.setattr(conn_module, "DATABASE_PATH", db_path)

    # Init semua 14 tabel di DB temp
    from database.connection import init_database

    init_database()

    yield db_path
    # Setelah test selesai, tmp_path otomatis dihapus oleh pytest


# ===================================================
# Helper: Mock LLM Response
# ===================================================
def make_llm_response(text: str) -> MagicMock:
    """
    Buat mock object yang meniru struktur anthropic.Message.

    Penggunaan di test:
        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = make_llm_response('{"key": "val"}')
    """
    msg = MagicMock()
    msg.content = [MagicMock()]
    msg.content[0].text = text
    return msg


def make_llm_json(data: dict) -> MagicMock:
    """Shortcut: buat mock LLM response langsung dari dict."""
    return make_llm_response(json.dumps(data))


# ===================================================
# Sample Data — Vocab
# ===================================================
@pytest.fixture
def sample_planner_output_vocab():
    """
    Simulasi output Vocab Planner cold start.
    Dipakai oleh test Validator dan test Generator.
    """
    return {
        "topic": "sehari_hari",
        "difficulty": "easy",
        "total_words": 10,
        "new_words": 5,
        "review_words": 5,
        "format_distribution": {
            "tebak_arti": 4,
            "sinonim_antonim": 3,
            "tebak_inggris": 3,
        },
        "words_to_review": [],
        "is_cold_start": True,
    }


@pytest.fixture
def sample_vocab_questions():
    """
    Simulasi 10 soal vocab yang sudah di-generate.
    Distribusi format sesuai sample_planner_output_vocab.
    """
    formats = ["tebak_arti"] * 4 + ["sinonim_antonim"] * 3 + ["tebak_inggris"] * 3
    words = [
        "accomplish",
        "adequate",
        "ambiguous",
        "analyze",
        "apparent",
        "appropriate",
        "approximate",
        "arbitrary",
        "assess",
        "assume",
    ]
    return [
        {
            "word": w,
            "format": f,
            "topic": "sehari_hari",
            "difficulty": "easy",
            "question_text": f"What does '{w}' mean?",
            "correct_answer": f"answer_for_{w}",
            "options": ["A", "B", "C", "D"],
        }
        for w, f in zip(words, formats)
    ]


# ===================================================
# Sample Data — Quiz
# ===================================================
@pytest.fixture
def sample_planner_output_quiz():
    """Simulasi output Quiz Planner (bukan cold start)."""
    return {
        "topic": "present_tenses",
        "difficulty": "easy",
        "total_questions": 10,
        "format_distribution": {
            "multiple_choice": 7,
            "error_id": 1,
            "fill_blank": 2,
        },
        "is_cold_start": False,
        "prerequisite_met": True,
        "rag_context": "Present simple is used for habits and routines.",
    }


@pytest.fixture
def sample_generator_output_quiz():
    """Simulasi 10 soal quiz yang sudah di-generate."""
    questions = []
    for i in range(10):
        fmt = "multiple_choice" if i < 7 else ("error_id" if i == 7 else "fill_blank")
        questions.append(
            {
                "id": i + 1,
                "format": fmt,
                "question_text": f"Sample question {i + 1}.",
                "options": ["A. opt A", "B. opt B", "C. opt C", "D. opt D"],
                "correct_answer": "A",
                "difficulty": "easy",
                "topic": "present_tenses",
            }
        )
    return {"questions": questions}


# ===================================================
# Sample Data — Speaking
# ===================================================
@pytest.fixture
def sample_speaking_history_short():
    """2 exchange — belum sampai batas prompted_response (max 3)."""
    return [
        {"role": "ai", "text": "Tell me about your daily routine."},
        {"role": "user", "text": "I wake up at 7 AM."},
        {"role": "ai", "text": "What do you have for breakfast?"},
        {"role": "user", "text": "I have rice and eggs."},
    ]


@pytest.fixture
def sample_speaking_history_long():
    """12 exchange — sudah masuk Fase 2 conversation_practice (> 10)."""
    h = []
    for i in range(12):
        h.append({"role": "ai", "text": f"AI turn {i + 1}"})
        h.append({"role": "user", "text": f"User response {i + 1}"})
    return h
