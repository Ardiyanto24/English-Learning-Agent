"""
verify_setup.py
----------------
Script verifikasi semua koneksi API setelah migrasi.
Jalankan dengan: python verify_setup.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

print("=" * 50)
print("VERIFIKASI SETUP API")
print("=" * 50)

# ===================================================
# TEST 1 — Google Credentials
# ===================================================
print("\n[TEST 1] Google Credentials...")
creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if not creds:
    print("  FAIL — GOOGLE_APPLICATION_CREDENTIALS tidak ada di .env")
elif not os.path.exists(creds):
    print(f"  FAIL — File tidak ditemukan: {creds}")
else:
    print(f"  OK — File ditemukan: {creds}")

# ===================================================
# TEST 2 — Google TTS
# ===================================================
print("\n[TEST 2] Google Text-to-Speech...")
try:
    from modules.audio.tts import generate_speech
    audio = generate_speech("Hello, this is a test.")
    if audio:
        print(f"  OK — Audio generated ({len(audio)} bytes)")
    else:
        print("  FAIL — generate_speech() returned None")
except Exception as e:
    print(f"  FAIL — {e}")

# ===================================================
# TEST 3 — Google STT
# ===================================================
print("\n[TEST 3] Google Speech-to-Text...")
try:
    from modules.audio.stt import transcribe_audio_bytes
    transcribe_audio_bytes(b"", filename="test.wav")
    print("  OK — STT client berhasil diinisialisasi")
except Exception as e:
    print(f"  FAIL — {e}")

# ===================================================
# TEST 4 — Anthropic API
# ===================================================
print("\n[TEST 4] Anthropic API...")
try:
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=20,
        messages=[{"role": "user", "content": "Say exactly: connected"}]
    )
    print(f"  OK — Response: {response.content[0].text.strip()}")
except Exception as e:
    print(f"  FAIL — {e}")

# ===================================================
# SUMMARY
# ===================================================
print("\n" + "=" * 50)
print("Verifikasi selesai.")
print("=" * 50)