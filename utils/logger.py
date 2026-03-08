"""
utils/logger.py
---------------
Global error logger menggunakan Loguru.

Penggunaan di agent lain:
    from utils.logger import logger, log_error

    logger.info("pesan biasa")
    logger.warning("pesan warning")
    log_error(error_type="llm_timeout", agent_name="vocab_generator", ...)
"""

import os
import sys
from pathlib import Path
from typing import Optional

from loguru import logger as _loguru_logger
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "app.log"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_CONSOLE = os.getenv("LOG_CONSOLE", "true").lower() == "true"

LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> | "
    "{message}"
)


def _setup():
    """Setup handler Loguru — dipanggil sekali saat module di-import."""
    LOG_DIR.mkdir(exist_ok=True)
    _loguru_logger.remove()

    if LOG_CONSOLE:
        _loguru_logger.add(
            sys.stderr,
            format=LOG_FORMAT,
            level=LOG_LEVEL,
            colorize=True,
        )

    _loguru_logger.add(
        str(LOG_FILE),
        format=LOG_FORMAT,
        level="DEBUG",
        rotation="00:00",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
        backtrace=True,
        diagnose=True,
    )


_setup()

# Export logger — ini yang diimport oleh semua agent
logger = _loguru_logger


def log_error(
    error_type: str,
    agent_name: str,
    session_id: Optional[str] = None,
    context: Optional[dict] = None,
    fallback_used: bool = False,
    exception: Optional[Exception] = None,
) -> None:
    """
    Log error ke file (Loguru) DAN insert ke tabel error_logs di DB.

    Args:
        error_type   : Kategori error. Contoh: "llm_timeout", "db_error"
        agent_name   : Nama agent. Contoh: "vocab_generator"
        session_id   : ID sesi yang sedang berjalan (opsional)
        context      : Dict info tambahan. Contoh: {"topic": "Tenses", "attempt": 2}
        fallback_used: True jika error sudah ditangani dengan fallback
        exception    : Exception object untuk log traceback lengkap
    """
    import json

    context_str = json.dumps(context or {}, ensure_ascii=False)
    log_msg = (
        f"[{agent_name}] {error_type} | "
        f"session={session_id or 'N/A'} | "
        f"fallback={fallback_used} | "
        f"context={context_str}"
    )

    if exception:
        logger.opt(exception=exception).error(log_msg)
    else:
        logger.error(log_msg)

    _insert_error_log(error_type, agent_name, session_id, context_str, fallback_used)


def _insert_error_log(
    error_type: str,
    agent_name: str,
    session_id: Optional[str],
    context: str,
    fallback_used: bool,
) -> None:
    """Insert error ke DB. Gagal silently agar tidak loop."""
    try:
        from database.connection import get_db
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO error_logs
                    (error_type, agent_name, session_id, context, fallback_used)
                VALUES (?, ?, ?, ?, ?)
                """,
                (error_type, agent_name, session_id, context, int(fallback_used)),
            )
    except Exception as db_err:
        logger.warning(f"[logger] Gagal insert ke error_logs: {db_err}")