"""اختبارات حاسبة الاضمحلال الإشعاعي وتوحيد الوحدات النووية (TASK-1.3 & 1.8).

Decay calculations and unit conversion unit tests.
All values are based on official nuclear definitions or explicit step-by-step
manual arithmetic documented in comments.
"""

from __future__ import annotations

from datetime import date
import math
import pytest

from kashif.core.decay import (
    BQ_PER_CI,
    BQ_PER_MCI,
    BQ_PER_UCI,
    DAYS_PER_YEAR,
    HALF_LIFE_CO60_DAYS,
    HALF_LIFE_CS137_DAYS,
    bq_to_ci,
    bq_to_gbq,
    bq_to_kbq,
    bq_to_mbq,
    bq_to_mci,
    bq_to_uci,
    ci_to_bq,
    decay_activity,
    decay_activity_between_dates,
    decay_activity_co60,
    decay_activity_cs137,
    gbq_to_bq,
    kbq_to_bq,
    mbq_to_bq,
    mci_to_bq,
    uci_to_bq,
)


def test_decay_exact_half_life():
    """التحقق من انخفاض الفعالية إلى النصف تماماً بعد مرور نصف عمر واحد.
    
    خطوات الحساب اليدوي:
    الفعالية الابتدائية A0 = 37,000.0 Bq
    عمر النصف T_1/2 = 1925.38 يوم
    الزمن المنقضي t = 1925.38 يوم
    المعادلة: A(t) = A0 * exp(-ln(2) * 1925.38 / 1925.38)
                   = A0 * exp(-ln(2))
                   = A0 * 0.5
                   = 37000.0 / 2 = 18500.0 Bq بالضبط.
    """
    a_t = decay_activity_co60(activity_0=37000.0, elapsed_days=1925.38)
    assert pytest.approx(a_t, rel=1e-12) == 18500.0


def test_decay_two_half_lives():
    """التحقق من انخفاض الفعالية إلى الربع بعد مروري نصفي عمر.
    
    خطوات الحساب اليدوي:
    A0 = 37,000.0 Bq
    t = 2 * 1925.38 = 3850.76 يوم
    A(t) = 37000.0 * (0.5)^2 = 37000.0 / 4 = 9250.0 Bq بالضبط.
    """
    a_t = decay_activity_co60(activity_0=37000.0, elapsed_days=2 * 1925.38)
    assert pytest.approx(a_t, rel=1e-12) == 9250.0


def test_decay_cs137_half_life():
    """التحقق من دالة اضمحلال السيزيوم-137 بعد نصف عمر واحد بالضبط."""
    a_t = decay_activity_cs137(activity_0=37000.0, elapsed_days=HALF_LIFE_CS137_DAYS)
    assert pytest.approx(a_t, rel=1e-12) == 18500.0


def test_decay_zero_activity():
    """فعالية ابتدائية صفرية تبقى صفراً دائماً."""
    assert decay_activity(activity_0=0.0, elapsed_days=500.0, half_life_days=1000.0) == 0.0


def test_decay_intermediate_time_co60_one_year():
    """حساب اضمحلال الكوبالت-60 بعد سنة واحدة بالضبط (365.25 يوم).
    
    خطوات الحساب اليدوي:
    A0 = 37,000.0 Bq (1 uCi)
    T_1/2 = 1925.38 يوم
    t = 365.25 يوم
    ثابت الاضمحلال lambda = ln(2) / 1925.38 = 0.6931471805599453 / 1925.38 = 0.00036000539143387033 day^-1
    الأس: -lambda * t = -0.00036000539143387033 * 365.25 = -0.13149196922122114
    معامل الاضمحلال: exp(-0.13149196922122114) = 0.8767852495634151
    الفعالية المتبقية: 37000.0 * 0.8767852495634151 = 32441.05423384636 Bq
    """
    lambda_val = math.log(2.0) / 1925.38
    expected = 37000.0 * math.exp(-lambda_val * 365.25)
    
    res = decay_activity_co60(activity_0=37000.0, elapsed_days=365.25)
    assert pytest.approx(res, rel=1e-9) == expected
    assert pytest.approx(res, rel=1e-5) == 32441.05


def test_decay_between_dates_cs137():
    """حساب الاضمحلال بين تاريخين لمصدر السيزيوم-137.
    
    خطوات الحساب اليدوي:
    cert_date = 2024-01-01
    target_date = 2025-01-01 (سنة كبيسة 2024: 366 يوماً)
    A0 = 37,000.0 Bq
    T_1/2 = 10986.72 يوم
    lambda = ln(2) / 10986.72 = 6.309044598801968e-5 day^-1
    exp(-lambda * 366) = exp(-0.023091103231615203) = 0.9771728148386348
    A(366) = 37000.0 * 0.9771728148386348 = 36155.39414902949 Bq
    """
    d1 = date(2024, 1, 1)
    d2 = date(2025, 1, 1)
    assert (d2 - d1).days == 366

    res = decay_activity_between_dates(
        activity_0=37000.0,
        cert_date=d1,
        target_date=d2,
        half_life_days=HALF_LIFE_CS137_DAYS,
    )
    expected = 37000.0 * math.exp(-(math.log(2.0) / 10986.72) * 366)
    assert pytest.approx(res, rel=1e-9) == expected


def test_unit_conversions_exact_standards():
    """التحقق من دقة معاملات التحويل للوحدات النووية المعتمدة."""
    # 1 uCi <-> Bq
    assert uci_to_bq(1.0) == 37000.0
    assert bq_to_uci(37000.0) == 1.0

    # 10 uCi = 370 kBq
    assert uci_to_bq(10.0) == 370000.0
    assert bq_to_kbq(370000.0) == 370.0
    assert kbq_to_bq(370.0) == 370000.0

    # 1 mCi = 37 MBq
    assert mci_to_bq(1.0) == 37000000.0
    assert bq_to_mci(37000000.0) == 1.0
    assert bq_to_mbq(37000000.0) == 37.0
    assert mbq_to_bq(37.0) == 37000000.0

    # 1 Ci = 37 GBq
    assert ci_to_bq(1.0) == 37000000000.0
    assert bq_to_ci(37000000000.0) == 1.0
    assert bq_to_gbq(37000000000.0) == 37.0
    assert gbq_to_bq(37.0) == 37000000000.0


def test_decay_negative_inputs_rejection():
    """التحقق من رفض الزمن السالب أو الفعالية السالبة في جميع دوال التحويل."""
    with pytest.raises(ValueError):
        decay_activity(activity_0=-100.0, elapsed_days=10.0, half_life_days=100.0)

    with pytest.raises(ValueError):
        decay_activity(activity_0=100.0, elapsed_days=-5.0, half_life_days=100.0)

    with pytest.raises(ValueError):
        decay_activity(activity_0=100.0, elapsed_days=10.0, half_life_days=0.0)

    with pytest.raises(ValueError):
        decay_activity_between_dates(
            activity_0=100.0,
            cert_date=date(2025, 1, 1),
            target_date=date(2024, 1, 1),
            half_life_days=100.0,
        )

    # رفض القيم السالبة في كافة دوال التحويل
    with pytest.raises(ValueError):
        uci_to_bq(-1.0)
    with pytest.raises(ValueError):
        bq_to_uci(-50.0)
    with pytest.raises(ValueError):
        mci_to_bq(-1.0)
    with pytest.raises(ValueError):
        bq_to_mci(-1.0)
    with pytest.raises(ValueError):
        ci_to_bq(-1.0)
    with pytest.raises(ValueError):
        bq_to_ci(-1.0)
    with pytest.raises(ValueError):
        bq_to_kbq(-1.0)
    with pytest.raises(ValueError):
        kbq_to_bq(-1.0)
    with pytest.raises(ValueError):
        bq_to_mbq(-1.0)
    with pytest.raises(ValueError):
        mbq_to_bq(-1.0)
    with pytest.raises(ValueError):
        bq_to_gbq(-1.0)
    with pytest.raises(ValueError):
        gbq_to_bq(-1.0)


def test_tbq_conversion_and_activity_units():
    """اختبار تحويل وحدات التيرا بيكرل ودوال التحويل الشاملة لوحدات النشاط الثماني."""
    from kashif.core.decay import (
        BQ_PER_TBQ,
        ACTIVITY_UNITS,
        bq_to_tbq,
        tbq_to_bq,
        convert_activity_to_bq,
        convert_activity_from_bq,
        format_activity_auto,
    )

    assert BQ_PER_TBQ == 1e12
    assert tbq_to_bq(1.5) == 1.5e12
    assert bq_to_tbq(3.0e12) == 3.0

    # Test all 8 units in convert_activity_to_bq and convert_activity_from_bq
    units_expected = [
        ("Bq", 100.0, 100.0),
        ("kBq", 2.5, 2500.0),
        ("MBq", 4.0, 4_000_000.0),
        ("GBq", 1.2, 1_200_000_000.0),
        ("TBq", 0.5, 500_000_000_000.0),
        ("uCi", 1.0, 37000.0),
        ("µCi", 2.0, 74000.0),
        ("mCi", 1.0, 37_000_000.0),
        ("Ci", 1.0, 37_000_000_000.0),
    ]
    for unit, val, expected_bq in units_expected:
        bq = convert_activity_to_bq(val, unit)
        assert pytest.approx(bq, rel=1e-9) == expected_bq
        val_back = convert_activity_from_bq(expected_bq, unit)
        assert pytest.approx(val_back, rel=1e-9) == val

    # Test auto formatting
    assert "TBq" in format_activity_auto(2.5e12)
    assert "GBq" in format_activity_auto(4.5e9)
    assert "MBq" in format_activity_auto(1.2e6)
    assert "kBq" in format_activity_auto(5.0e3)
    assert "Bq" in format_activity_auto(500.0)

    # Errors
    with pytest.raises(ValueError):
        bq_to_tbq(-1.0)
    with pytest.raises(ValueError):
        bq_to_tbq(float("nan"))
    with pytest.raises(ValueError):
        tbq_to_bq(-1.0)
    with pytest.raises(ValueError):
        convert_activity_to_bq(100, "unknown_unit")
    with pytest.raises(ValueError):
        convert_activity_to_bq(-10, "Bq")
    with pytest.raises(ValueError):
        convert_activity_from_bq(100, "unknown_unit")
    with pytest.raises(ValueError):
        convert_activity_from_bq(-10, "Bq")
    with pytest.raises(ValueError):
        format_activity_auto(-10)


def test_half_life_units_conversion_and_formatting():
    """اختبار تحويل أعمار النصف بجميع الوحدات الخمس وتنسيقها.
    
    حسابات يدوية صريحة للتحويل إلى أيام:
    1) ثانية (s):
       86400 ثانية / 86400 ثانية/يوم = 1.0 يوم
       172800 ثانية / 86400 = 2.0 يوم
    2) دقيقة (m):
       1440 دقيقة / 1440 دقيقة/يوم = 1.0 يوم
       720 دقيقة / 1440 = 0.5 يوم
    3) ساعة (h):
       24 ساعة / 24 ساعة/يوم = 1.0 يوم
       6.0067 ساعة / 24 = 0.2502791666666667 يوم (مثل عمر النصف لـ Tc-99m)
    4) يوم (d):
       10986.72 يوم * 1.0 = 10986.72 يوم (السيزيوم-137)
    5) سنة (y):
       السنة النووية = 365.25 يوماً بالضبط
       5.2714 سنة * 365.25 يوم/سنة = 1925.37885 يوم (الكوبالت-60)
       30.08 سنة * 365.25 يوم/سنة = 10986.72 يوم
    """
    from kashif.core.decay import half_life_to_days, format_half_life

    # 1. ثانية
    assert half_life_to_days(86400.0, "s") == 1.0
    assert half_life_to_days(172800.0, "s") == 2.0

    # 2. دقيقة
    assert half_life_to_days(1440.0, "m") == 1.0
    assert half_life_to_days(720.0, "m") == 0.5

    # 3. ساعة
    assert half_life_to_days(24.0, "h") == 1.0
    assert pytest.approx(half_life_to_days(6.0, "h"), rel=1e-9) == 0.25

    # 4. يوم
    assert half_life_to_days(10986.72, "d") == 10986.72

    # 5. سنة
    assert half_life_to_days(1.0, "y") == 365.25
    assert pytest.approx(half_life_to_days(5.2714, "y"), rel=1e-9) == 1925.37885

    # Formatting checks
    assert "سنة" in format_half_life(5.27, "y")
    assert "ساعة" in format_half_life(6.01, "h")
    assert "دقيقة" in format_half_life(109.7, "m")
    assert "ثانية" in format_half_life(30.0, "s")
    assert "يوم" in format_half_life(271.74, "d")

    # Scientific notation for very large/small
    sci_large = format_half_life(1.5e5, "y")
    assert "e" in sci_large.lower() or "1.500e+05" in sci_large
    sci_small = format_half_life(0.005, "s")
    assert "e" in sci_small.lower()

    # Invalid input guards (zero, negative, NaN, Inf, invalid unit)
    with pytest.raises(ValueError):
        half_life_to_days(0.0, "s")
    with pytest.raises(ValueError):
        half_life_to_days(-10.0, "y")
    with pytest.raises(ValueError):
        half_life_to_days(float("nan"), "d")
    with pytest.raises(ValueError):
        half_life_to_days(float("inf"), "h")
    with pytest.raises(ValueError):
        half_life_to_days(10.0, "unknown")

    with pytest.raises(ValueError):
        format_half_life(0.0, "y")
    with pytest.raises(ValueError):
        format_half_life(-5.0, "y")
    with pytest.raises(ValueError):
        format_half_life(float("nan"), "y")
    with pytest.raises(ValueError):
        format_half_life(10.0, "unknown")

