"""
config/settings.py
------------------
Konfigurasi global untuk seluruh project.

Semua konstanta yang dipakai lebih dari satu file harus ada di sini.
Jangan hardcode nilai-nilai ini langsung di agent atau module lain.

Cara pakai:
    from config.settings import HAIKU_MODEL, MASTERY_THRESHOLD
"""

# ===================================================
# Model Names
# ===================================================
HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"

# ===================================================
# Vocab Agent — Default Cold Start Config
# ===================================================
VOCAB_DEFAULT_TOTAL_WORDS = 10
VOCAB_MIN_WORDS = 5       # batas bawah pilihan user
VOCAB_MAX_WORDS = 20      # batas atas pilihan user
VOCAB_DEFAULT_NEW_WORDS = 5
VOCAB_DEFAULT_REVIEW_WORDS = 5
VOCAB_DEFAULT_DIFFICULTY = "easy"
VOCAB_DEFAULT_TOPIC = "sehari_hari"
VOCAB_FORMAT_PCT = {
    "easy": {
        "tebak_arti": 0.60,
        "sinonim_antonim": 0.20,
        "tebak_inggris": 0.20,
    },
    "medium": {
        "tebak_arti": 0.40,
        "sinonim_antonim": 0.20,
        "tebak_inggris": 0.40,
    },
    "hard": {
        "tebak_arti": 0.30,
        "sinonim_antonim": 0.20,
        "tebak_inggris": 0.50,
    },
}

# ===================================================
# Spaced Repetition
# ===================================================
# Kata dengan mastery_score di bawah threshold ini
# diprioritaskan sebagai review words
MASTERY_THRESHOLD = 0.6  # 60%

# Difficulty progression thresholds
DIFFICULTY_UPGRADE_THRESHOLD = 80.0  # Naik level jika avg mastery >= 80%
DIFFICULTY_DOWNGRADE_THRESHOLD = 40.0  # Turun level jika avg mastery < 40%

# ===================================================
# Analytics
# ===================================================
# Minimum sesi yang dibutuhkan sebelum analytics bisa dijalankan
MIN_SESSIONS_FOR_ANALYTICS = 3

# ===================================================
# Validator
# ===================================================
VALIDATOR_MATCH_THRESHOLD = 0.8  # match_score >= 0.8 dianggap valid
VALIDATOR_MAX_RETRY = 3

# ===================================================
# Retry & Timeout
# ===================================================
LLM_MAX_RETRY = 3
LLM_RETRY_MIN_WAIT = 2  # seconds
LLM_RETRY_MAX_WAIT = 8  # seconds

# ===================================================
# Session
# ===================================================
VALID_MODES = {"vocab", "quiz", "speaking", "toefl"}
SESSION_EXPIRY_HOURS = 24  # Sesi expired setelah 24 jam tidak aktif

# ===================================================
# ChromaDB / RAG
# ===================================================
RAG_TOP_K = 3
RAG_THRESHOLD_STRICT = 0.75
RAG_THRESHOLD_RELAXED = 0.60
