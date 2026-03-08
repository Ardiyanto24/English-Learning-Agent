"""
utils/retry.py
--------------
Decorator retry menggunakan Tenacity.

Dua decorator:
1. @retry_llm   — untuk LLM calls: max 3x, exponential backoff 2s→4s→8s
2. @retry_once  — untuk non-LLM: max 1x retry, tunggu 1s

Penggunaan:
    from utils.retry import retry_llm, retry_once

    @retry_llm
    def call_claude(prompt):
        return claude_client.messages.create(...)

    @retry_once
    def fetch_db_data(session_id):
        return repository.get_session(session_id)
"""

import functools
from typing import Callable, Optional, Type, tuple

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    wait_fixed,
    retry_if_exception_type,
    before_sleep_log,
    after_log,
)
import logging

# Tenacity menggunakan standard logging — arahkan ke Loguru
# Import lazy untuk hindari circular import
def _get_logger():
    from utils.logger import logger
    return logger


# Standard logging adapter untuk Tenacity
_std_logger = logging.getLogger("tenacity")


# ===================================================
# Decorator 1: retry_llm
# ===================================================
retry_llm = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    # multiplier=1, min=2, max=8 → 2s, 4s, 8s
    reraise=True,   # Raise exception asli setelah habis retry
    before_sleep=before_sleep_log(_std_logger, logging.WARNING),
)
"""
Decorator untuk LLM API calls.

Behavior:
- Attempt 1: langsung
- Attempt 2: tunggu 2s
- Attempt 3: tunggu 4s
- Setelah 3x gagal: raise exception asli

Contoh penggunaan:
    @retry_llm
    def generate_questions(prompt: str) -> dict:
        response = client.messages.create(...)
        return parse_response(response)
"""


# ===================================================
# Decorator 2: retry_once
# ===================================================
retry_once = retry(
    stop=stop_after_attempt(2),    # 2 attempt = 1 retry
    wait=wait_fixed(1),            # Tunggu 1s sebelum retry
    reraise=True,
    before_sleep=before_sleep_log(_std_logger, logging.WARNING),
)
"""
Decorator untuk non-LLM calls (DB, file I/O, audio).

Behavior:
- Attempt 1: langsung
- Attempt 2: tunggu 1s
- Setelah 2x gagal: raise exception asli

Contoh penggunaan:
    @retry_once
    def save_session_to_db(session_id: str, data: dict):
        repository.save(session_id, data)
"""


# ===================================================
# Factory function untuk konfigurasi custom
# ===================================================
def make_retry(
    max_attempts: int = 3,
    wait_seconds: float = 2.0,
    exponential: bool = True,
    exception_types: Optional[tuple] = None,
):
    """
    Buat decorator retry dengan konfigurasi custom.
    Berguna untuk kasus edge case di luar dua preset di atas.

    Args:
        max_attempts   : Jumlah maksimal attempt (termasuk attempt pertama)
        wait_seconds   : Waktu tunggu dasar dalam detik
        exponential    : True = exponential backoff, False = fixed wait
        exception_types: Tuple of exception class yang di-retry.
                         None = retry semua exception.

    Contoh:
        @make_retry(max_attempts=5, wait_seconds=3, exponential=True)
        def unstable_api_call():
            ...
    """
    retry_kwargs = {
        "stop": stop_after_attempt(max_attempts),
        "reraise": True,
        "before_sleep": before_sleep_log(_std_logger, logging.WARNING),
    }

    if exponential:
        retry_kwargs["wait"] = wait_exponential(
            multiplier=1,
            min=wait_seconds,
            max=wait_seconds * 4,
        )
    else:
        retry_kwargs["wait"] = wait_fixed(wait_seconds)

    if exception_types:
        retry_kwargs["retry"] = retry_if_exception_type(exception_types)

    return retry(**retry_kwargs)