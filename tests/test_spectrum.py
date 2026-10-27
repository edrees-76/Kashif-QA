"""اختبارات معايرة الطاقة الخطية وتحليل القمم (TASK-1.5 & 1.8).

Linear energy calibration and spectrum peak analysis unit tests.
Compliant with IAEA MRNI-507 (p. 14-15) and MRNI-504 (p. 7-8).
Manual calculation steps documented in comments.
"""

from __future__ import annotations

import pytest

from kashif.core.spectrum import (
    CO60_ENERGY_1_KEV,
    CO60_ENERGY_2_KEV,
    CS137_ENERGY_KEV,
    calibrate_co60,
    calibrate_nai_single_peak,
    channel_to_energy,
    energy_to_channel,
    linear_energy_calibration,
)


def test_co60_linear_calibration_manual_steps():
    """معايرة قمتي الكوبالت-60 بخطوات حسابية يدوية موثقة.
    
    المرجع: IAEA MRNI-507 ص 14-15 البنود 3.7.2 و 3.7.4.
    
    خطوات الحساب اليدوي:
    طاقات قمم الكوبالت:
    E1 = 1173.2 keV (القمة الأولى)
    E2 = 1332.5 keV (القمة الثانية)
    فرق الطاقة: Delta_E = 1332.5 - 1173.2 = 159.3 keV
    
    بافتراض قنوات القياس المسجلة في MCA:
    ch1 = 2346.4 channel
    ch2 = 2665.0 channel
    فرق القنوات: Delta_ch = 2665.0 - 2346.4 = 318.6 channels
    
    الميل:
    m = Delta_E / Delta_ch = 159.3 / 318.6 = 0.500000 keV/channel
    
    التقاطع b:
    b = E1 - (m * ch1) = 1173.2 - (0.5 * 2346.4) = 1173.2 - 1173.2 = 0.0 keV
    """
    ch1 = 2346.4
    ch2 = 2665.0
    
    m, b = calibrate_co60(ch_1173=ch1, ch_1332=ch2)
    assert pytest.approx(m, rel=1e-9) == 0.5
    assert pytest.approx(b, abs=1e-9) == 0.0

    # التحقق من دقة التحويل العكسي
    e1 = channel_to_energy(channel=ch1, slope_m=m, intercept_b=b)
    e2 = channel_to_energy(channel=ch2, slope_m=m, intercept_b=b)
    assert pytest.approx(e1, rel=1e-9) == CO60_ENERGY_1_KEV
    assert pytest.approx(e2, rel=1e-9) == CO60_ENERGY_2_KEV


def test_linear_calibration_with_non_zero_intercept():
    """معايرة خطية بوجود إزاحة تقاطع غير صفرية (b != 0).
    
    خطوات الحساب اليدوي:
    النموذج المفترض: y = 0.5 * x - 2.5
    لقمة 1173.2 keV:
    x1 = (1173.2 - (-2.5)) / 0.5 = (1173.2 + 2.5) / 0.5 = 1175.7 / 0.5 = 2351.4 channels
    لقمة 1332.5 keV:
    x2 = (1332.5 + 2.5) / 0.5 = 1335.0 / 0.5 = 2670.0 channels
    
    حساب m و b من النقطتين (2351.4, 1173.2) و (2670.0, 1332.5):
    Delta_ch = 2670.0 - 2351.4 = 318.6
    Delta_E = 159.3
    m = 159.3 / 318.6 = 0.5
    b = 1173.2 - (0.5 * 2351.4) = 1173.2 - 1175.7 = -2.5 keV
    """
    ch1 = 2351.4
    ch2 = 2670.0
    m, b = linear_energy_calibration(
        ch1=ch1, energy1=1173.2,
        ch2=ch2, energy2=1332.5,
    )
    assert pytest.approx(m, rel=1e-9) == 0.5
    assert pytest.approx(b, rel=1e-9) == -2.5


def test_nai_single_peak_calibration():
    """معايرة كاشف NaI(Tl) من قمة السيزيوم 661.5 keV.
    
    المرجع: IAEA MRNI-504 ص 7-8.
    
    خطوات الحساب اليدوي:
    طاقة السيزيوم = 661.5 keV
    إذا كانت القمة عند القناة 1323.0 مع تقاطع b = 0:
    m = 661.5 / 1323.0 = 0.5 keV/channel
    """
    m, b = calibrate_nai_single_peak(ch_661=1323.0, intercept_b=0.0)
    assert pytest.approx(m, rel=1e-9) == 0.5
    assert b == 0.0

    # تحويل طاقة السيزيوم إلى قناة
    ch = energy_to_channel(energy=CS137_ENERGY_KEV, slope_m=m, intercept_b=b)
    assert pytest.approx(ch, rel=1e-9) == 1323.0


def test_channel_energy_bidirectional_conversion():
    """التحقق من التناسق ثنائي الاتجاه بين القنوات والطاقة."""
    slope_m = 0.625
    intercept_b = 1.2

    original_channel = 1600.0
    energy = channel_to_energy(original_channel, slope_m, intercept_b)
    reconstructed_channel = energy_to_channel(energy, slope_m, intercept_b)

    assert pytest.approx(reconstructed_channel, rel=1e-9) == original_channel


def test_spectrum_validation_and_error_handling():
    """التحقق من رفض المدخلات الفاسدة وغير المتناسقة."""
    # قنوات متطابقة (تؤدي لقسمة على صفر)
    with pytest.raises(ValueError):
        linear_energy_calibration(ch1=100.0, energy1=500.0, ch2=100.0, energy2=1000.0)

    # طاقات متطابقة
    with pytest.raises(ValueError):
        linear_energy_calibration(ch1=100.0, energy1=661.5, ch2=200.0, energy2=661.5)

    # ميل سالب (القناة تزداد والطاقة تنخفض)
    with pytest.raises(ValueError) as exc:
        linear_energy_calibration(ch1=500.0, energy1=1332.5, ch2=1000.0, energy2=1173.2)
    assert "ميل المعايرة الطاقية" in str(exc.value)

    # قنوات سالبة
    with pytest.raises(ValueError):
        linear_energy_calibration(ch1=-5.0, energy1=100.0, ch2=200.0, energy2=200.0)

    # طاقات سالبة أو صفرية
    with pytest.raises(ValueError):
        linear_energy_calibration(ch1=100.0, energy1=0.0, ch2=200.0, energy2=200.0)
    with pytest.raises(ValueError):
        linear_energy_calibration(ch1=100.0, energy1=100.0, ch2=200.0, energy2=-50.0)

    # Co-60 القناة الثانية أقل من الأولى
    with pytest.raises(ValueError):
        calibrate_co60(ch_1173=2000.0, ch_1332=1500.0)

    # معايرة NaI بقناة صفرية أو سالبة
    with pytest.raises(ValueError):
        calibrate_nai_single_peak(ch_661=0.0)
    with pytest.raises(ValueError):
        calibrate_nai_single_peak(ch_661=-10.0)

    # معايرة NaI بتقاطع أكبر من طاقة القمة
    with pytest.raises(ValueError):
        calibrate_nai_single_peak(ch_661=1000.0, intercept_b=700.0)

    # ميل سالب أو صفري في التحويل
    with pytest.raises(ValueError):
        channel_to_energy(channel=100.0, slope_m=0.0, intercept_b=0.0)
    with pytest.raises(ValueError):
        channel_to_energy(channel=-10.0, slope_m=0.5, intercept_b=0.0)

    # طاقة سالبة في تحويل الطاقة لقناة
    with pytest.raises(ValueError):
        energy_to_channel(energy=-50.0, slope_m=0.5, intercept_b=0.0)
    with pytest.raises(ValueError):
        energy_to_channel(energy=100.0, slope_m=0.0, intercept_b=0.0)

    # طاقة دون عتبة القناة صفر (تنتج قناة سالبة)
    with pytest.raises(ValueError) as exc_info:
        energy_to_channel(energy=1.0, slope_m=0.5, intercept_b=10.0)
    assert "تقع دون عتبة القناة صفر" in str(exc_info.value)
