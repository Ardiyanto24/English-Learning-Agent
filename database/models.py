"""
database/models.py
------------------
Definisi semua 14 tabel dan index database.

Semua CREATE TABLE statement dikumpulkan di sini sebagai konstanta string.
Dieksekusi oleh init_database() di connection.py saat aplikasi pertama jalan.

Urutan CREATE_ALL_TABLES penting:
- Tabel yang direferensi (parent) harus dibuat sebelum tabel yang mereferensi (child)
- sessions harus dibuat sebelum vocab_sessions, quiz_sessions, dst.
"""

# ==============================================================
# TABEL 1 — users
# Menyimpan data profil dan onboarding user
# ==============================================================
CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    target_toefl        INTEGER NOT NULL,
    grammar_level       TEXT NOT NULL,
    first_vocab_topic   TEXT NOT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 2 — sessions
# Metadata setiap sesi latihan lintas semua mode
# ==============================================================
CREATE_SESSIONS = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT UNIQUE NOT NULL,
    mode            TEXT NOT NULL,
    status          TEXT NOT NULL,
    is_adjusted     BOOLEAN DEFAULT FALSE,
    is_flagged      BOOLEAN DEFAULT FALSE,
    flag_reason     TEXT,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP,
    expires_at      TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 3 — vocab_sessions
# ==============================================================
CREATE_VOCAB_SESSIONS = """
CREATE TABLE IF NOT EXISTS vocab_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    topic           TEXT NOT NULL,
    total_words     INTEGER NOT NULL,
    new_words       INTEGER NOT NULL,
    review_words    INTEGER NOT NULL,
    correct_count   INTEGER DEFAULT 0,
    wrong_count     INTEGER DEFAULT 0,
    score_pct       REAL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 4 — vocab_questions
# ==============================================================
CREATE_VOCAB_QUESTIONS = """
CREATE TABLE IF NOT EXISTS vocab_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    word            TEXT NOT NULL,
    format          TEXT NOT NULL,
    topic           TEXT NOT NULL,
    difficulty      TEXT NOT NULL,
    question_text   TEXT NOT NULL,
    correct_answer  TEXT NOT NULL,
    user_answer     TEXT,
    is_correct      BOOLEAN,
    is_graded       BOOLEAN DEFAULT TRUE,
    is_new_word     BOOLEAN DEFAULT TRUE,
    attempt_count   INTEGER DEFAULT 1,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 5 — vocab_word_tracking
# Tracking per kata untuk spaced repetition
# ==============================================================
CREATE_VOCAB_WORD_TRACKING = """
CREATE TABLE IF NOT EXISTS vocab_word_tracking (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word            TEXT NOT NULL,
    topic           TEXT NOT NULL,
    difficulty      TEXT NOT NULL,
    total_seen      INTEGER DEFAULT 0,
    total_correct   INTEGER DEFAULT 0,
    total_wrong     INTEGER DEFAULT 0,
    mastery_score   REAL DEFAULT 0,
    last_seen_at    TIMESTAMP,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(word, topic)
);
"""

# ==============================================================
# TABEL 6 — quiz_sessions
# ==============================================================
CREATE_QUIZ_SESSIONS = """
CREATE TABLE IF NOT EXISTS quiz_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    topics          TEXT NOT NULL,
    total_questions INTEGER NOT NULL,
    correct_count   INTEGER DEFAULT 0,
    wrong_count     INTEGER DEFAULT 0,
    score_pct       REAL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 7 — quiz_questions
# ==============================================================
CREATE_QUIZ_QUESTIONS = """
CREATE TABLE IF NOT EXISTS quiz_questions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id              TEXT NOT NULL REFERENCES sessions(session_id),
    topic                   TEXT NOT NULL,
    cluster                 TEXT NOT NULL,
    format                  TEXT NOT NULL,
    difficulty              TEXT NOT NULL,
    question_text           TEXT NOT NULL,
    options                 TEXT,
    correct_answer          TEXT NOT NULL,
    user_answer             TEXT,
    is_correct              BOOLEAN,
    is_graded               BOOLEAN DEFAULT TRUE,
    feedback_verdict        TEXT,
    feedback_explanation    TEXT,
    feedback_concept        TEXT,
    feedback_example        TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 8 — quiz_topic_tracking
# ==============================================================
CREATE_QUIZ_TOPIC_TRACKING = """
CREATE TABLE IF NOT EXISTS quiz_topic_tracking (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    topic               TEXT UNIQUE NOT NULL,
    cluster             TEXT NOT NULL,
    total_sessions      INTEGER DEFAULT 0,
    total_questions     INTEGER DEFAULT 0,
    total_correct       INTEGER DEFAULT 0,
    total_wrong         INTEGER DEFAULT 0,
    avg_score_pct       REAL DEFAULT 0,
    last_score_pct      REAL DEFAULT 0,
    is_prerequisite_met BOOLEAN DEFAULT FALSE,
    last_practiced_at   TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 9 — speaking_sessions
# ==============================================================
CREATE_SPEAKING_SESSIONS = """
CREATE TABLE IF NOT EXISTS speaking_sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    sub_mode            TEXT NOT NULL,
    topic               TEXT NOT NULL,
    category            TEXT NOT NULL,
    total_exchanges     INTEGER DEFAULT 0,
    duration_seconds    INTEGER,
    full_transcript     TEXT,
    grammar_score       REAL,
    relevance_score     REAL,
    vocabulary_score    REAL,
    structure_score     REAL,
    final_score         REAL,
    is_graded           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 10 — speaking_exchanges
# ==============================================================
CREATE_SPEAKING_EXCHANGES = """
CREATE TABLE IF NOT EXISTS speaking_exchanges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES sessions(session_id),
    exchange_number     INTEGER NOT NULL,
    agent_prompt        TEXT NOT NULL,
    user_transcript     TEXT,
    is_followup         BOOLEAN DEFAULT FALSE,
    assessor_decision   TEXT,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 11 — toefl_sessions
# ==============================================================
CREATE_TOEFL_SESSIONS = """
CREATE TABLE IF NOT EXISTS toefl_sessions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id              TEXT NOT NULL REFERENCES sessions(session_id),
    mode                    TEXT NOT NULL,
    current_section         INTEGER DEFAULT 1,
    listening_raw           INTEGER,
    structure_raw           INTEGER,
    reading_raw             INTEGER,
    listening_extrapolated  INTEGER,
    structure_extrapolated  INTEGER,
    reading_extrapolated    INTEGER,
    listening_scaled        INTEGER,
    structure_scaled        INTEGER,
    reading_scaled          INTEGER,
    estimated_score         INTEGER,
    score_status            TEXT DEFAULT 'pending',
    paused_at               TIMESTAMP,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 12 — toefl_questions
# ==============================================================
CREATE_TOEFL_QUESTIONS = """
CREATE TABLE IF NOT EXISTS toefl_questions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    section         TEXT NOT NULL,
    part            TEXT NOT NULL,
    question_number INTEGER NOT NULL,
    question_text   TEXT NOT NULL,
    passage_text    TEXT,
    audio_script    TEXT,
    options         TEXT NOT NULL,
    correct_answer  TEXT NOT NULL,
    user_answer     TEXT,
    is_correct      BOOLEAN,
    difficulty      TEXT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 13 — analytics_snapshots
# ==============================================================
CREATE_ANALYTICS_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_type   TEXT NOT NULL,
    content         TEXT NOT NULL,
    generated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# TABEL 14 — error_logs
# ==============================================================
CREATE_ERROR_LOGS = """
CREATE TABLE IF NOT EXISTS error_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    error_type      TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    session_id      TEXT,
    context         TEXT,
    fallback_used   TEXT,
    is_resolved     BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ==============================================================
# INDEX — untuk performa query yang sering dipakai
# ==============================================================
CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_quiz_topic ON quiz_topic_tracking(topic);",
    "CREATE INDEX IF NOT EXISTS idx_quiz_questions_topic ON quiz_questions(topic, session_id);",
    "CREATE INDEX IF NOT EXISTS idx_vocab_word ON vocab_word_tracking(word, topic);",
    "CREATE INDEX IF NOT EXISTS idx_vocab_last_seen ON vocab_word_tracking(last_seen_at);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_mode ON sessions(mode, status);",
    "CREATE INDEX IF NOT EXISTS idx_sessions_created ON sessions(started_at);",
    "CREATE INDEX IF NOT EXISTS idx_toefl_score ON toefl_sessions(estimated_score, created_at);",
    "CREATE INDEX IF NOT EXISTS idx_error_logs_agent ON error_logs(agent_name, timestamp);",
]

# ==============================================================
# EXPORT — dipakai oleh connection.py di init_database()
# Urutan CREATE_ALL_TABLES penting: parent table dulu, child table belakangan
# ==============================================================
CREATE_ALL_TABLES = [
    CREATE_USERS,
    CREATE_SESSIONS,
    CREATE_VOCAB_SESSIONS,
    CREATE_VOCAB_QUESTIONS,
    CREATE_VOCAB_WORD_TRACKING,
    CREATE_QUIZ_SESSIONS,
    CREATE_QUIZ_QUESTIONS,
    CREATE_QUIZ_TOPIC_TRACKING,
    CREATE_SPEAKING_SESSIONS,
    CREATE_SPEAKING_EXCHANGES,
    CREATE_TOEFL_SESSIONS,
    CREATE_TOEFL_QUESTIONS,
    CREATE_ANALYTICS_SNAPSHOTS,
    CREATE_ERROR_LOGS,
]

CREATE_ALL_INDEXES = CREATE_INDEXES