"""
tests/unit/test_toefl_agent.py
--------------------------------
Unit test untuk TOEFL Simulator — berdasarkan kode asli.

Yang ditest:
  MODE_CONFIG (toefl.py)
    : distribusi soal akurat per mode (50%/75%/100%)
    : timer proporsional
    : semua mode punya listening, structure, reading

  Converter (modules/scoring/toefl_converter.py)
    : extrapolate_score — formula proyeksi ke full test
    : convert_to_scaled — lookup tabel ITP resmi
    : calculate_estimated_toefl — formula final (L+S+R)*10/3
    : process_full_score — end-to-end pipeline
    : edge cases: nol, perfect, section invalid, total_mode nol

  Session Manager (modules/session/toefl_session_manager.py)
    : konstanta PAUSE_EXPIRY_DAYS = 7
    : SECTION_TOTALS sesuai spesifikasi
    : pause hanya valid setelah section 1 atau 2
    : resume expired → ResumeResult(success=False)
    : resume valid → ResumeResult(success=True)
    : _expires_str menghasilkan tanggal 7 hari ke depan

Semua test TIDAK memanggil LLM atau DB sungguhan — semua di-mock.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch


# ===================================================
# Test: MODE_CONFIG — distribusi soal per mode
# ===================================================
class TestModeConfig:
    """
    MODE_CONFIG didefinisikan di pages/toefl.py.
    Ini adalah sumber kebenaran untuk distribusi soal dan timer
    yang dipakai oleh Generator dan Session Manager.
    """

    def test_mode_50pct_listening_total(self):
        """Mode 50%: Listening harus 25 soal."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["50%"]["listening"]["total"] == 25

    def test_mode_50pct_structure_total(self):
        """Mode 50%: Structure harus 20 soal."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["50%"]["structure"]["total"] == 20

    def test_mode_50pct_reading_total(self):
        """Mode 50%: Reading harus 25 soal."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["50%"]["reading"]["total"] == 25

    def test_mode_75pct_listening_total(self):
        """Mode 75%: Listening harus 38 soal."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["75%"]["listening"]["total"] == 38

    def test_mode_75pct_structure_total(self):
        """Mode 75%: Structure harus 30 soal."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["75%"]["structure"]["total"] == 30

    def test_mode_75pct_reading_total(self):
        """Mode 75%: Reading harus 37 soal."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["75%"]["reading"]["total"] == 37

    def test_mode_100pct_listening_total(self):
        """Mode 100%: Listening harus 50 soal (full test)."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["100%"]["listening"]["total"] == 50

    def test_mode_100pct_structure_total(self):
        """Mode 100%: Structure harus 40 soal (full test)."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["100%"]["structure"]["total"] == 40

    def test_mode_100pct_reading_total(self):
        """Mode 100%: Reading harus 50 soal (full test)."""
        from pages.toefl import MODE_CONFIG
        assert MODE_CONFIG["100%"]["reading"]["total"] == 50

    def test_all_modes_have_three_sections(self):
        """Setiap mode harus punya ketiga section."""
        from pages.toefl import MODE_CONFIG
        for mode, cfg in MODE_CONFIG.items():
            assert "listening"  in cfg, f"Mode {mode} missing listening"
            assert "structure"  in cfg, f"Mode {mode} missing structure"
            assert "reading"    in cfg, f"Mode {mode} missing reading"

    def test_all_modes_have_timers(self):
        """Setiap mode harus punya konfigurasi timer."""
        from pages.toefl import MODE_CONFIG
        for mode, cfg in MODE_CONFIG.items():
            assert "timers" in cfg, f"Mode {mode} missing timers"
            timers = cfg["timers"]
            assert "listening"  in timers
            assert "structure"  in timers
            assert "reading"    in timers

    def test_75pct_timer_greater_than_50pct(self):
        """
        Timer 75% harus lebih besar dari 50% untuk semua section.
        Ini memastikan proporsionalitas waktu dengan jumlah soal.
        """
        from pages.toefl import MODE_CONFIG
        for section in ["listening", "structure", "reading"]:
            assert (
                MODE_CONFIG["75%"]["timers"][section] >
                MODE_CONFIG["50%"]["timers"][section]
            ), f"Timer 75% harus > 50% untuk section {section}"

    def test_100pct_timer_greater_than_75pct(self):
        """Timer 100% harus lebih besar dari 75% untuk semua section."""
        from pages.toefl import MODE_CONFIG
        for section in ["listening", "structure", "reading"]:
            assert (
                MODE_CONFIG["100%"]["timers"][section] >
                MODE_CONFIG["75%"]["timers"][section]
            ), f"Timer 100% harus > 75% untuk section {section}"

    def test_section_totals_match_mode_config(self):
        """
        SECTION_TOTALS di session manager harus konsisten
        dengan MODE_CONFIG di toefl.py.
        """
        from pages.toefl import MODE_CONFIG
        from modules.session.toefl_session_manager import SECTION_TOTALS

        for mode in ["50%", "75%", "100%"]:
            cfg = MODE_CONFIG[mode]
            st  = SECTION_TOTALS[mode]
            assert st[1] == cfg["listening"]["total"], f"Listening mismatch mode {mode}"
            assert st[2] == cfg["structure"]["total"], f"Structure mismatch mode {mode}"
            assert st[3] == cfg["reading"]["total"],   f"Reading mismatch mode {mode}"


# ===================================================
# Test: Converter — formula skor ITP
# ===================================================
class TestToeflConverter:

    # ── extrapolate_score ───────────────────────────
    def test_extrapolate_50pct_listening(self):
        """
        Mode 50% Listening: 25 soal, benar 18
        → extrapolate ke 50 soal = round(18/25 * 50) = 36
        """
        from modules.scoring.toefl_converter import extrapolate_score
        assert extrapolate_score(raw=18, total_mode=25, total_full=50) == 36

    def test_extrapolate_perfect_score(self):
        """Semua benar di mode parsial → extrapolate ke perfect full score."""
        from modules.scoring.toefl_converter import extrapolate_score
        assert extrapolate_score(raw=25, total_mode=25, total_full=50) == 50

    def test_extrapolate_zero_score(self):
        """Nol benar → extrapolate ke 0."""
        from modules.scoring.toefl_converter import extrapolate_score
        assert extrapolate_score(raw=0, total_mode=25, total_full=50) == 0

    def test_extrapolate_zero_total_mode_returns_zero(self):
        """
        total_mode = 0 → return 0 (tidak boleh ZeroDivisionError).
        Edge case: sesi dibuat tapi tidak ada soal.
        """
        from modules.scoring.toefl_converter import extrapolate_score
        assert extrapolate_score(raw=10, total_mode=0, total_full=50) == 0

    def test_extrapolate_clamped_to_total_full(self):
        """
        Hasil extrapolate tidak boleh melebihi total_full.
        Ini mencegah skor yang mustahil muncul.
        """
        from modules.scoring.toefl_converter import extrapolate_score
        # raw > total_mode tidak mungkin terjadi, tapi clamp tetap aman
        result = extrapolate_score(raw=30, total_mode=25, total_full=50)
        assert result <= 50

    def test_extrapolate_75pct_structure(self):
        """
        Mode 75% Structure: 30 soal, benar 24
        → round(24/30 * 40) = round(32.0) = 32
        """
        from modules.scoring.toefl_converter import extrapolate_score
        assert extrapolate_score(raw=24, total_mode=30, total_full=40) == 32

    # ── convert_to_scaled ───────────────────────────
    def test_convert_listening_max_raw(self):
        """Listening raw 50 → scaled 68 (nilai tertinggi tabel ITP)."""
        from modules.scoring.toefl_converter import convert_to_scaled
        assert convert_to_scaled(50, "listening") == 68

    def test_convert_structure_max_raw(self):
        """Structure raw 40 → scaled 68."""
        from modules.scoring.toefl_converter import convert_to_scaled
        assert convert_to_scaled(40, "structure") == 68

    def test_convert_reading_max_raw(self):
        """Reading raw 50 → scaled 67 (satu lebih rendah dari L dan S)."""
        from modules.scoring.toefl_converter import convert_to_scaled
        assert convert_to_scaled(50, "reading") == 67

    def test_convert_zero_raw_returns_minimum_31(self):
        """
        Raw 0 di semua section → scaled minimum 31.
        Tidak ada skor di bawah 31 dalam skala TOEFL ITP.
        """
        from modules.scoring.toefl_converter import convert_to_scaled
        assert convert_to_scaled(0, "listening") == 31
        assert convert_to_scaled(0, "structure") == 31
        assert convert_to_scaled(0, "reading")   == 31

    def test_convert_invalid_section_raises_value_error(self):
        """Section tidak valid → harus raise ValueError."""
        from modules.scoring.toefl_converter import convert_to_scaled
        with pytest.raises(ValueError):
            convert_to_scaled(25, "writing")   # bukan section TOEFL ITP

    def test_convert_case_insensitive(self):
        """Section name tidak case-sensitive (Listening == listening)."""
        from modules.scoring.toefl_converter import convert_to_scaled
        assert convert_to_scaled(25, "Listening") == convert_to_scaled(25, "listening")

    def test_convert_raw_clamped_above_max(self):
        """
        Raw score melebihi max (contoh: listening raw=999)
        harus di-clamp ke max sebelum lookup tabel.
        """
        from modules.scoring.toefl_converter import convert_to_scaled
        # Sama dengan raw=50 karena di-clamp
        assert convert_to_scaled(999, "listening") == convert_to_scaled(50, "listening")

    def test_convert_specific_values_from_table(self):
        """
        Verifikasi beberapa nilai spesifik dari tabel konversi resmi.
        Ini memastikan tabel tidak salah input.
        """
        from modules.scoring.toefl_converter import convert_to_scaled

        # Listening
        assert convert_to_scaled(30, "listening") == 48
        assert convert_to_scaled(20, "listening") == 38
        assert convert_to_scaled(10, "listening") == 31

        # Structure
        assert convert_to_scaled(30, "structure") == 58
        assert convert_to_scaled(20, "structure") == 46

        # Reading
        assert convert_to_scaled(30, "reading") == 47
        assert convert_to_scaled(20, "reading") == 37

    # ── calculate_estimated_toefl ───────────────────
    def test_calculate_estimated_formula(self):
        """
        Formula: round((L + S + R) * 10 / 3)
        Contoh: L=50, S=50, R=48 → round((50+50+48)*10/3) = round(493.33) = 493
        """
        from modules.scoring.toefl_converter import calculate_estimated_toefl
        result = calculate_estimated_toefl(50, 50, 48)
        assert result == round((50 + 50 + 48) * 10 / 3)

    def test_calculate_estimated_minimum_310(self):
        """Semua scaled minimum (31,31,31) → estimated minimum 310."""
        from modules.scoring.toefl_converter import calculate_estimated_toefl
        result = calculate_estimated_toefl(31, 31, 31)
        assert result == 310

    def test_calculate_estimated_maximum_677(self):
        """Semua scaled maksimum (68,68,67) → estimated 677 (atau di-clamp ke 677)."""
        from modules.scoring.toefl_converter import calculate_estimated_toefl
        result = calculate_estimated_toefl(68, 68, 67)
        assert result == 677

    def test_calculate_estimated_always_in_range(self):
        """
        Semua kemungkinan kombinasi scaled score harus
        menghasilkan estimated dalam range 310–677.
        """
        from modules.scoring.toefl_converter import calculate_estimated_toefl

        test_cases = [
            (31, 31, 31),   # minimum
            (68, 68, 67),   # maksimum
            (50, 45, 48),   # tipikal
            (35, 40, 38),   # bawah rata-rata
        ]
        for l, s, r in test_cases:
            result = calculate_estimated_toefl(l, s, r)
            assert 310 <= result <= 677, (
                f"Estimated {result} out of range for L={l} S={s} R={r}"
            )

    # ── process_full_score (end-to-end pipeline) ────
    def test_process_full_score_returns_all_fields(self):
        """process_full_score harus return semua 10 intermediate fields."""
        from modules.scoring.toefl_converter import process_full_score

        result = process_full_score(
            listening_raw=18, structure_raw=15, reading_raw=20,
            listening_total_mode=25, structure_total_mode=20,
            reading_total_mode=25,
        )

        required = {
            "listening_raw", "structure_raw", "reading_raw",
            "listening_extrapolated", "structure_extrapolated", "reading_extrapolated",
            "listening_scaled", "structure_scaled", "reading_scaled",
            "estimated_score",
        }
        assert required.issubset(set(result.keys()))

    def test_process_full_score_estimated_in_valid_range(self):
        """Estimated score dari process_full_score selalu 310–677."""
        from modules.scoring.toefl_converter import process_full_score

        result = process_full_score(
            listening_raw=18, structure_raw=15, reading_raw=20,
            listening_total_mode=25, structure_total_mode=20,
            reading_total_mode=25,
        )
        assert 310 <= result["estimated_score"] <= 677

    def test_process_full_score_pipeline_consistency(self):
        """
        Verifikasi pipeline konsisten: extrapolate → scale → estimate.
        Hasilnya harus sama jika dihitung manual step by step.
        """
        from modules.scoring.toefl_converter import (
            calculate_estimated_toefl,
            convert_to_scaled,
            extrapolate_score,
            process_full_score,
        )

        l_raw, s_raw, r_raw = 18, 15, 20
        l_mode, s_mode, r_mode = 25, 20, 25

        # Hitung manual
        l_extrap  = extrapolate_score(l_raw, l_mode, 50)
        s_extrap  = extrapolate_score(s_raw, s_mode, 40)
        r_extrap  = extrapolate_score(r_raw, r_mode, 50)
        l_scaled  = convert_to_scaled(l_extrap, "listening")
        s_scaled  = convert_to_scaled(s_extrap, "structure")
        r_scaled  = convert_to_scaled(r_extrap, "reading")
        manual    = calculate_estimated_toefl(l_scaled, s_scaled, r_scaled)

        # Hitung via pipeline
        result    = process_full_score(l_raw, s_raw, r_raw, l_mode, s_mode, r_mode)

        assert result["estimated_score"]         == manual
        assert result["listening_extrapolated"]  == l_extrap
        assert result["structure_extrapolated"]  == s_extrap
        assert result["reading_extrapolated"]    == r_extrap
        assert result["listening_scaled"]        == l_scaled
        assert result["structure_scaled"]        == s_scaled
        assert result["reading_scaled"]          == r_scaled


# ===================================================
# Test: Session Manager — expiry dan pause/resume
# ===================================================
class TestToeflSessionManager:

    # ── Konstanta ───────────────────────────────────
    def test_pause_expiry_days_is_7(self):
        """PAUSE_EXPIRY_DAYS harus tepat 7 hari sesuai spesifikasi."""
        from modules.session.toefl_session_manager import PAUSE_EXPIRY_DAYS
        assert PAUSE_EXPIRY_DAYS == 7

    def test_section_order_is_1_2_3(self):
        """SECTION_ORDER harus [1, 2, 3] — Listening, Structure, Reading."""
        from modules.session.toefl_session_manager import SECTION_ORDER
        assert SECTION_ORDER == [1, 2, 3]

    def test_section_totals_50pct(self):
        """SECTION_TOTALS mode 50% harus L:25, S:20, R:25."""
        from modules.session.toefl_session_manager import SECTION_TOTALS
        assert SECTION_TOTALS["50%"]  == {1: 25, 2: 20, 3: 25}

    def test_section_totals_75pct(self):
        """SECTION_TOTALS mode 75% harus L:38, S:30, R:37."""
        from modules.session.toefl_session_manager import SECTION_TOTALS
        assert SECTION_TOTALS["75%"]  == {1: 38, 2: 30, 3: 37}

    def test_section_totals_100pct(self):
        """SECTION_TOTALS mode 100% harus L:50, S:40, R:50."""
        from modules.session.toefl_session_manager import SECTION_TOTALS
        assert SECTION_TOTALS["100%"] == {1: 50, 2: 40, 3: 50}

    # ── _expires_str ────────────────────────────────
    def test_expires_str_adds_7_days(self):
        """
        _expires_str harus menghasilkan tanggal tepat 7 hari ke depan.
        Ini yang disimpan ke DB saat user pause sesi.
        """
        from modules.session.toefl_session_manager import _expires_str, PAUSE_EXPIRY_DAYS

        now     = datetime(2025, 1, 1, 10, 0, 0)
        result  = _expires_str(now)
        expected = (now + timedelta(days=PAUSE_EXPIRY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")

        assert result == expected

    def test_expires_str_format(self):
        """
        Format expires_at harus 'YYYY-MM-DD HH:MM:SS'
        agar bisa disimpan dan dibaca dari SQLite.
        """
        from modules.session.toefl_session_manager import _expires_str

        now    = datetime(2025, 6, 15, 8, 30, 0)
        result = _expires_str(now)

        # Panjang format: "YYYY-MM-DD HH:MM:SS" = 19 karakter
        assert len(result) == 19
        assert result == "2025-06-22 08:30:00"

    # ── pause_session ───────────────────────────────
    def test_pause_after_section_3_fails(self):
        """
        Pause setelah section 3 (Reading) harus gagal.
        Reading adalah section terakhir — tidak ada yang bisa di-resume.
        """
        from modules.session.toefl_session_manager import pause_session

        result = pause_session(
            session_id="test-session",
            completed_section=3,   # Reading = section terakhir
            mode="50%",
        )

        assert result.success is False
        assert result.reason  is not None

    def test_pause_after_invalid_section_fails(self):
        """Section 0 atau 99 bukan section valid → pause harus gagal."""
        from modules.session.toefl_session_manager import pause_session

        for invalid in [0, 4, 99]:
            result = pause_session(
                session_id="test-session",
                completed_section=invalid,
                mode="50%",
            )
            assert result.success is False, f"Section {invalid} harus gagal"

    def test_pause_section_not_complete_fails(self):
        """
        Jika section belum selesai (is_section_complete=False),
        pause harus ditolak — tidak boleh pause di tengah section.
        """
        from modules.session.toefl_session_manager import pause_session

        with patch("modules.session.toefl_session_manager.is_section_complete") as mock_check:
            mock_check.return_value = False   # section belum selesai

            result = pause_session(
                session_id="test-session",
                completed_section=1,
                mode="50%",
            )

        assert result.success is False

    def test_pause_section_1_success(self):
        """
        Pause setelah section 1 (Listening) yang sudah selesai → sukses.
        PauseResult.expires_at harus ada.
        """
        from modules.session.toefl_session_manager import pause_session

        with patch("modules.session.toefl_session_manager.is_section_complete") as mock_check, \
             patch("modules.session.toefl_session_manager.pause_toefl_session") as mock_pause:

            mock_check.return_value = True   # section sudah selesai
            mock_pause.return_value = True   # DB berhasil disimpan

            result = pause_session(
                session_id="test-session",
                completed_section=1,
                mode="50%",
            )

        assert result.success    is True
        assert result.expires_at is not None

    def test_pause_section_2_success(self):
        """Pause setelah section 2 (Structure) yang sudah selesai → sukses."""
        from modules.session.toefl_session_manager import pause_session

        with patch("modules.session.toefl_session_manager.is_section_complete") as mock_check, \
             patch("modules.session.toefl_session_manager.pause_toefl_session") as mock_pause:

            mock_check.return_value = True
            mock_pause.return_value = True

            result = pause_session(
                session_id="test-session",
                completed_section=2,
                mode="50%",
            )

        assert result.success is True

    # ── resume_session ──────────────────────────────
    def test_resume_expired_session_returns_failure(self):
        """
        Sesi yang sudah expired (check_and_resume → None)
        → ResumeResult(success=False).
        Sesi tidak bisa dilanjutkan setelah 7 hari.
        """
        from modules.session.toefl_session_manager import resume_session

        with patch("modules.session.toefl_session_manager.check_and_resume_toefl_session") as mock_check:
            mock_check.return_value = None   # None = expired atau tidak valid

            result = resume_session(session_id="expired-session")

        assert result.success is False
        assert result.reason  is not None

    def test_resume_valid_session_returns_success(self):
        """
        Sesi yang masih valid (check_and_resume → state dict)
        → ResumeResult(success=True, state=...).
        """
        from modules.session.toefl_session_manager import resume_session

        mock_state = {
            "session_id":       "valid-session",
            "current_section":  2,
            "mode":             "50%",
            "expires_at":       "2025-12-31 10:00:00",
        }

        with patch("modules.session.toefl_session_manager.check_and_resume_toefl_session") as mock_check:
            mock_check.return_value = mock_state

            result = resume_session(session_id="valid-session")

        assert result.success    is True
        assert result.state      == mock_state
        assert result.expires_at == "2025-12-31 10:00:00"

    def test_resume_db_error_returns_failure(self):
        """
        DB error saat resume → ResumeResult(success=False).
        Tidak boleh crash — harus graceful.
        """
        from modules.session.toefl_session_manager import resume_session

        with patch("modules.session.toefl_session_manager.check_and_resume_toefl_session") as mock_check:
            mock_check.side_effect = Exception("DB connection failed")

            result = resume_session(session_id="test-session")

        assert result.success is False
        assert result.reason  is not None

    def test_resume_result_has_current_section(self):
        """
        State dari resume harus punya current_section
        agar UI tahu dari section mana melanjutkan.
        """
        from modules.session.toefl_session_manager import resume_session

        mock_state = {
            "session_id":      "test",
            "current_section": 2,   # lanjut dari Structure
            "mode":            "50%",
            "expires_at":      "2025-12-31 10:00:00",
        }

        with patch("modules.session.toefl_session_manager.check_and_resume_toefl_session") as mock_check:
            mock_check.return_value = mock_state

            result = resume_session(session_id="test")

        assert result.state["current_section"] == 2