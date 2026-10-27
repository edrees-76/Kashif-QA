"""اختبارات دوال الحسابات الفيزيائية لمعايير IAEA (TASK-1.2 & 1.8).

Physics unit tests strictly compliant with:
- IAEA MRNI-507 (Rev. D0)
- IAEA MRNI-504 (Rev. D0)
- Explicit manual calculation steps documented in comments.
"""

from __future__ import annotations

import math
import pytest

from kashif.core.physics import (
    count_rate,
    dead_time_pct,
    energy_resolution_pct,
    form_factor,
    fwhm_kev,
    fwtm_kev,
    operating_voltage_nai,
    relative_efficiency_calibrated_source,
    relative_efficiency_comparison,
)


def test_energy_resolution_pct_iaea_mrni_504_page_8_example():
    """اختبار الدقة الطاقية %R باستخدام المثال النصي الصريح في IAEA MRNI-504 ص 8 الشكل 4.
    
    النص من وثيقة IAEA MRNI-504 (Rev. D0) صفحة 8:
    "From the Fig.4, the resolution is:
     R(%) = (48.06 keV / 661.5 keV) * 100
     R = 7.26 %"
    
    خطوات الحساب:
    48.06 / 661.5 = 0.0726530612244898
    0.0726530612244898 * 100 = 7.26530612244898%
    ملاحظة توثيقية: مؤلف الوثيقة اقتطع الناتج دون تقريب (Truncation) ليكتب 7.26%
    بينما التقريب الرياضي لمنزلتين هو 7.27%. نتحقق من كليهما.
    """
    res = energy_resolution_pct(fwhm_kev=48.06, peak_energy_kev=661.5)
    assert pytest.approx(res, rel=1e-5) == (48.06 / 661.5) * 100.0
    # الاقتطاع المباشر الوارد نصاً في الوثيقة
    assert math.floor(res * 100) / 100 == 7.26
    # التقريب الحسابي القياسي
    assert round(res, 2) == 7.27


def test_fwhm_and_fwtm_kev_calculations():
    """حساب FWHM و FWTM بوحدة keV بتحويل القنوات عبر الميل m.
    
    المرجع: IAEA MRNI-507 ص 15 البنود 3.7.6 و 3.7.8:
    خطوات الحساب اليدوي:
    1. بافتراض ميل المعايرة m = 0.5 keV/channel (حيث القناة 2665 تقابل 1332.5 keV).
    2. إذا كان عرض القمة بالقنوات FWHM_ch = 3.80 channels:
       FWHM = 3.80 * 0.5 = 1.90 keV (يطابق تماماً القيمة النموذجية 1.9 keV المذكورة في ص 15 البند 3.7.7).
    3. إذا كان العرض عند عشر الارتفاع FWTM_ch = 7.00 channels:
       FWTM = 7.00 * 0.5 = 3.50 keV.
    """
    m = 0.5
    fwhm = fwhm_kev(fwhm_channels=3.80, slope_m=m)
    assert pytest.approx(fwhm, rel=1e-9) == 1.90

    fwtm = fwtm_kev(fwtm_channels=7.00, slope_m=m)
    assert pytest.approx(fwtm, rel=1e-9) == 3.50


def test_form_factor_calculation():
    """حساب معامل شكل النبضة FWTM / FWHM.
    
    المرجع: IAEA MRNI-507 ص 15 البند 3.7.8:
    "Calculate the form factor (FWTM/FWHM) ... the ideal value should be less than 1.9"
    
    خطوات الحساب اليدوي:
    باستخدام القيم المحسوبة أعلاه:
    FWTM = 3.50 keV, FWHM = 1.90 keV
    Form Factor = 3.50 / 1.90 = 1.8421052631578947
    القيمة < 1.9 (شكل متناسق ومقبول).
    """
    ff = form_factor(fwtm=3.50, fwhm=1.90)
    expected = 3.50 / 1.90
    assert pytest.approx(ff, rel=1e-9) == expected
    assert ff < 1.90


def test_relative_efficiency_comparison_with_nai():
    """حساب الكفاءة النسبية بطريقة المقارنة مع كاشف NaI(Tl) قياسي 3x3 بوصة.
    
    المرجع: IAEA MRNI-507 ص 16 البند 3.8.4:
    Eff = (C1 / C2) * 100
    
    خطوات الحساب اليدوي:
    إذا كانت المساحة الصافية لقمة الكوبالت في كاشف الجرمانيوم C1 = 2500.0 نبضة
    والمساحة في كاشف NaI القياسي لنفس الزمن C2 = 10000.0 نبضة:
    Eff = (2500.0 / 10000.0) * 100 = 0.25 * 100 = 25.0%
    """
    eff = relative_efficiency_comparison(net_counts_hpge=2500.0, net_counts_nai=10000.0)
    assert pytest.approx(eff, rel=1e-9) == 25.0


def test_relative_efficiency_calibrated_source():
    """حساب الكفاءة النسبية باستخدام مصدر Co-60 معاير.
    
    المرجع: IAEA MRNI-507 ص 16 البند 3.8.3 (b) ومعيار IEEE 325:
    Eff = (C1 / (1.2e-3 * A * T1)) * 100
    
    خطوات الحساب اليدوي:
    - الفعالية المعتمدة A = 37,000.0 Bq (1 uCi، منصوص عليها في ص 7 البند 3.3).
    - زمن القياس الحي T1 = 300.0 ثانية (5 دقائق).
    - المقام: 1.2e-3 * 37000.0 * 300.0 = 13,320.0
    - المساحة الصافية C1 = 3,330.0 نبضة
    - Eff = (3330.0 / 13320.0) * 100 = 0.25 * 100 = 25.0%
    """
    a_bq = 37000.0
    t1_s = 300.0
    c1 = 3330.0
    expected_denom = 1.2e-3 * a_bq * t1_s  # 13320.0
    assert expected_denom == 13320.0

    eff = relative_efficiency_calibrated_source(
        net_counts_hpge=c1,
        activity_bq=a_bq,
        live_time_sec=t1_s,
    )
    assert pytest.approx(eff, rel=1e-9) == 25.0


def test_count_rate_calculation():
    """حساب معدل النبضات بالثانية cps.
    
    المرجع: IAEA MRNI-507 ص 14 البند 3.7.2:
    "pulse repetition rate not greater than 2 000 cps"
    
    خطوات الحساب اليدوي:
    النبضات = 18,000 نبضة، الزمن = 15.0 ثانية:
    cps = 18000.0 / 15.0 = 1200.0 cps
    """
    cps = count_rate(total_counts=18000.0, time_seconds=15.0)
    assert pytest.approx(cps, rel=1e-9) == 1200.0


def test_dead_time_pct_calculation():
    """حساب النسبة المئوية لزمن الموت.
    
    المرجع: IAEA MRNI-507 ص 14 البند 3.7.2 و IAEA-TECDOC-602 ص 22.
    
    خطوات الحساب اليدوي:
    الزمن الحقيقي (Clock Time) = 100.0 ثانية
    الزمن الحي (Live Time) = 96.0 ثانية
    النسبة: (1 - (96.0 / 100.0)) * 100 = (1 - 0.96) * 100 = 0.04 * 100 = 4.0%
    """
    dt = dead_time_pct(live_time_seconds=96.0, real_time_seconds=100.0)
    assert pytest.approx(dt, rel=1e-9) == 4.0


def test_operating_voltage_nai_calculation():
    """حساب جهد التشغيل المقترح لكاشف NaI(Tl).
    
    المرجع: IAEA MRNI-504 ص 7 البند 3.5.1:
    Vo = (VM + Vi) / 2
    
    خطوات الحساب اليدوي:
    جهد ظهور النبضات Vi = 700.0 V
    الجهد الأقصى VM = 1100.0 V
    Vo = (700.0 + 1100.0) / 2 = 1800.0 / 2 = 900.0 V
    """
    vo = operating_voltage_nai(v_initial=700.0, v_max=1100.0)
    assert pytest.approx(vo, rel=1e-9) == 900.0


# ---------------------------------------------------------------------------
# اختبارات تغطية كافة الفحوصات والحدود الشاذة (Error & Edge Paths)
# ---------------------------------------------------------------------------

def test_physics_error_rejections_and_boundaries():
    """تغطية كافة مسارات التحقق الصارم في physics.py."""
    # FWHM
    with pytest.raises(ValueError):
        fwhm_kev(fwhm_channels=-1.0, slope_m=0.5)
    with pytest.raises(ValueError):
        fwhm_kev(fwhm_channels=3.0, slope_m=0.0)
    with pytest.raises(ValueError):
        fwhm_kev(fwhm_channels=3.0, slope_m=-0.5)

    # FWTM
    with pytest.raises(ValueError):
        fwtm_kev(fwtm_channels=-1.0, slope_m=0.5)
    with pytest.raises(ValueError):
        fwtm_kev(fwtm_channels=7.0, slope_m=0.0)
    with pytest.raises(ValueError):
        fwtm_kev(fwtm_channels=7.0, slope_m=-0.5)

    # Form factor
    with pytest.raises(ValueError):
        form_factor(fwtm=3.0, fwhm=0.0)
    with pytest.raises(ValueError):
        form_factor(fwtm=-1.0, fwhm=2.0)

    # Energy resolution
    with pytest.raises(ValueError):
        energy_resolution_pct(fwhm_kev=48.0, peak_energy_kev=0.0)
    with pytest.raises(ValueError):
        energy_resolution_pct(fwhm_kev=-1.0, peak_energy_kev=661.5)

    # Efficiency - comparison
    with pytest.raises(ValueError):
        relative_efficiency_comparison(net_counts_hpge=100.0, net_counts_nai=0.0)
    with pytest.raises(ValueError):
        relative_efficiency_comparison(net_counts_hpge=-10.0, net_counts_nai=100.0)

    # Efficiency - calibrated source
    with pytest.raises(ValueError):
        relative_efficiency_calibrated_source(net_counts_hpge=100.0, activity_bq=0.0, live_time_sec=10.0)
    with pytest.raises(ValueError):
        relative_efficiency_calibrated_source(net_counts_hpge=100.0, activity_bq=37000.0, live_time_sec=0.0)
    with pytest.raises(ValueError):
        relative_efficiency_calibrated_source(net_counts_hpge=-5.0, activity_bq=37000.0, live_time_sec=100.0)

    # Count rate
    with pytest.raises(ValueError):
        count_rate(total_counts=100.0, time_seconds=0.0)
    with pytest.raises(ValueError):
        count_rate(total_counts=-10.0, time_seconds=10.0)

    # Dead time
    with pytest.raises(ValueError):
        dead_time_pct(live_time_seconds=90.0, real_time_seconds=0.0)
    with pytest.raises(ValueError):
        dead_time_pct(live_time_seconds=-5.0, real_time_seconds=100.0)
    with pytest.raises(ValueError):
        dead_time_pct(live_time_seconds=120.0, real_time_seconds=100.0)

    # Operating voltage NaI
    with pytest.raises(ValueError):
        operating_voltage_nai(v_initial=-10.0, v_max=1000.0)
    with pytest.raises(ValueError):
        operating_voltage_nai(v_initial=800.0, v_max=700.0)
