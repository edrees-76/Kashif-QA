"""اختبارات محرك قواعد القرار والتحقق الفني (TASK-1.4 & 1.8).

Complete unit tests for all conformity and governance rules:
- R-HPGE-01 to R-HPGE-15
- R-NAI-01 to R-NAI-03
- R-INST-01 to R-INST-03
- R-SRC-01
- RULE-SEC-01 to RULE-SEC-03
- Verdict aggregation hierarchy

Every numeric rule is strictly tested at the exact boundary, just below, and just above,
using pytest.mark.parametrize with rule ID in the test case name.
"""

from __future__ import annotations

from datetime import date
from typing import List
import pytest

from kashif.core.models import (
    Verdict,
    VoltagePolarity,
    WindowStatus,
)
from kashif.core.rules import (
    RuleResult,
    aggregate_verdicts,
    eval_r_hpge_01_window_integrity,
    eval_r_hpge_02_window_scratch,
    eval_r_hpge_03_cooling_time,
    eval_r_hpge_04_cryostat_vacuum,
    eval_r_hpge_05_nim_voltages,
    eval_r_hpge_06_hv_polarity,
    eval_r_hpge_07_hv_ramp_rate,
    eval_r_hpge_08_preamp_signal,
    eval_r_hpge_09_pole_zero,
    eval_r_hpge_10_mca_rates,
    eval_r_hpge_11_peak_counts,
    eval_r_hpge_12_fwhm,
    eval_r_hpge_13_14_form_factor,
    eval_r_hpge_15_efficiency,
    eval_r_inst_01_calibration,
    eval_r_inst_02_hv_power_supply,
    eval_r_inst_03_amplifier_shaping,
    eval_r_nai_01_pmt_voltage,
    eval_r_nai_02_resolution,
    eval_r_nai_03_gain_drift,
    eval_r_src_01_source_activity,
    eval_rule_sec_01_separation_of_duties,
    eval_rule_sec_02_admin_approval_prohibition,
    eval_rule_sec_03_immutable_approved_record,
)
from kashif.core.thresholds import (
    SystemThresholds,
    get_default_thresholds,
    load_thresholds_from_dict,
)


# ---------------------------------------------------------------------------
# R-HPGE-01: سلامة الحاوية والنافذة
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status, struck, expected_verdict, test_id", [
    (WindowStatus.INTACT, False, Verdict.PASS, "R-HPGE-01-pass-intact"),
    (WindowStatus.BROKEN, False, Verdict.FAILURE, "R-HPGE-01-fail-broken"),
    (WindowStatus.INTACT, True, Verdict.FAILURE, "R-HPGE-01-fail-struck"),
    (WindowStatus.BROKEN, True, Verdict.FAILURE, "R-HPGE-01-fail-broken-and-struck"),
])
def test_r_hpge_01_window_integrity(status: WindowStatus, struck: bool, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_01_window_integrity(status, struck)
    assert res.rule_id == "R-HPGE-01"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-HPGE-02: خدش نافذة البريليوم
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status, expected_verdict, test_id", [
    (WindowStatus.INTACT, Verdict.PASS, "R-HPGE-02-pass-intact"),
    (WindowStatus.SCRATCHED, Verdict.OUT_OF_SPEC, "R-HPGE-02-oos-scratched"),
])
def test_r_hpge_02_window_scratch(status: WindowStatus, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_02_window_scratch(status)
    assert res.rule_id == "R-HPGE-02"
    assert res.verdict == expected_verdict
    assert res.confirmed is False  # [TBC]


# ---------------------------------------------------------------------------
# R-HPGE-03: زمن التبريد بالنيتروجين السائل (عتبة 6.0 ساعات)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hours, expected_verdict, test_id", [
    (5.99, Verdict.BLOCK, "R-HPGE-03-below-boundary-block"),
    (6.00, Verdict.PASS, "R-HPGE-03-exact-boundary-pass"),
    (6.01, Verdict.PASS, "R-HPGE-03-above-boundary-pass"),
    (10.0, Verdict.PASS, "R-HPGE-03-well-above-pass"),
    (0.0, Verdict.BLOCK, "R-HPGE-03-zero-block"),
])
def test_r_hpge_03_cooling_time(hours: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_03_cooling_time(hours)
    assert res.rule_id == "R-HPGE-03"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


def test_r_hpge_03_cooling_time_with_cert_hours():
    """Test R-HPGE-03 with certificate cooling limits: effective = max(6.0, cert_hours)."""
    # 8.0h cert limit governs
    res_block = eval_r_hpge_03_cooling_time(7.99, cooling_hours_cert=8.0)
    assert res_block.verdict == Verdict.BLOCK
    assert "8.0" in res_block.message_ar

    res_pass = eval_r_hpge_03_cooling_time(8.00, cooling_hours_cert=8.0)
    assert res_pass.verdict == Verdict.PASS

    # 4.0h cert limit does NOT lower standard 6.0h minimum
    res_floor = eval_r_hpge_03_cooling_time(5.99, cooling_hours_cert=4.0)
    assert res_floor.verdict == Verdict.BLOCK
    assert "6.0" in res_floor.message_ar

    res_floor_pass = eval_r_hpge_03_cooling_time(6.00, cooling_hours_cert=4.0)
    assert res_floor_pass.verdict == Verdict.PASS


# ---------------------------------------------------------------------------
# R-HPGE-04: تفريغ الحاوية وتكثف الرطوبة أو الصقيع
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("wet_or_frozen, leak, expected_verdict, test_id", [
    (False, False, Verdict.PASS, "R-HPGE-04-pass-dry"),
    (True, False, Verdict.FAILURE, "R-HPGE-04-fail-wet-or-frozen"),
    (False, True, Verdict.FAILURE, "R-HPGE-04-fail-vacuum-leak"),
])
def test_r_hpge_04_cryostat_vacuum(wet_or_frozen: bool, leak: bool, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_04_cryostat_vacuum(wet_or_frozen, leak)
    assert res.rule_id == "R-HPGE-04"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-HPGE-05: جهود سلة NIM (سماحية ±5% لـ ±24V و ±12V)
# حد +24V: 22.80V إلى 25.20V
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("v24_pos, expected_verdict, test_id", [
    (24.0, Verdict.PASS, "R-HPGE-05-exact-nominal-pass"),
    (25.19, Verdict.PASS, "R-HPGE-05-v24-just-below-upper-pass"),
    (25.20, Verdict.PASS, "R-HPGE-05-v24-exact-upper-boundary-pass"),
    (25.21, Verdict.OUT_OF_SPEC, "R-HPGE-05-v24-just-above-upper-oos"),
    (22.81, Verdict.PASS, "R-HPGE-05-v24-just-above-lower-pass"),
    (22.80, Verdict.PASS, "R-HPGE-05-v24-exact-lower-boundary-pass"),
    (22.79, Verdict.OUT_OF_SPEC, "R-HPGE-05-v24-just-below-lower-oos"),
])
def test_r_hpge_05_nim_voltages_boundary(v24_pos: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_05_nim_voltages(
        v_pos_24=v24_pos,
        v_neg_24=-24.0,
        v_pos_12=12.0,
        v_neg_12=-12.0,
    )
    assert res.rule_id == "R-HPGE-05"
    assert res.verdict == expected_verdict
    assert res.confirmed is False  # [TBC]


# ---------------------------------------------------------------------------
# R-HPGE-06: قطبية الجهد العالي
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("applied, nominal, cert, expected_verdict, test_id", [
    (VoltagePolarity.POSITIVE, VoltagePolarity.POSITIVE, VoltagePolarity.POSITIVE, Verdict.PASS, "R-HPGE-06-pass-pos-pos-cert"),
    (VoltagePolarity.NEGATIVE, VoltagePolarity.NEGATIVE, VoltagePolarity.NEGATIVE, Verdict.PASS, "R-HPGE-06-pass-neg-neg-cert"),
    (VoltagePolarity.POSITIVE, VoltagePolarity.POSITIVE, None, Verdict.WARNING, "R-HPGE-06-warn-missing-cert-pos"),
    (VoltagePolarity.NEGATIVE, VoltagePolarity.NEGATIVE, None, Verdict.WARNING, "R-HPGE-06-warn-missing-cert-neg"),
    (VoltagePolarity.POSITIVE, VoltagePolarity.NEGATIVE, None, Verdict.BLOCK, "R-HPGE-06-block-mismatch-pos-neg"),
    (VoltagePolarity.NEGATIVE, VoltagePolarity.POSITIVE, None, Verdict.BLOCK, "R-HPGE-06-block-mismatch-neg-pos"),
    (VoltagePolarity.POSITIVE, VoltagePolarity.POSITIVE, VoltagePolarity.NEGATIVE, Verdict.BLOCK, "R-HPGE-06-block-applied-vs-cert"),
])
def test_r_hpge_06_hv_polarity(applied: VoltagePolarity, nominal: VoltagePolarity, cert: Optional[VoltagePolarity], expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_06_hv_polarity(applied, nominal, certified_polarity=cert)
    assert res.rule_id == "R-HPGE-06"
    assert res.verdict == expected_verdict
    assert res.confirmed is (cert is not None or expected_verdict == Verdict.BLOCK)


# ---------------------------------------------------------------------------
# R-HPGE-07: معدل رفع الجهد العالي (عتبة 10.0 V/s)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rate, expected_verdict, test_id", [
    (9.99, Verdict.PASS, "R-HPGE-07-below-boundary-pass"),
    (10.00, Verdict.PASS, "R-HPGE-07-exact-boundary-pass"),
    (10.01, Verdict.BLOCK, "R-HPGE-07-above-boundary-block"),
    (15.0, Verdict.BLOCK, "R-HPGE-07-well-above-block"),
])
def test_r_hpge_07_hv_ramp_rate(rate: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_07_hv_ramp_rate(rate)
    assert res.rule_id == "R-HPGE-07"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-HPGE-08: إشارة المضخم الأولي
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("has_signal, expected_verdict, test_id", [
    (True, Verdict.PASS, "R-HPGE-08-pass-signal-present"),
    (False, Verdict.FAILURE, "R-HPGE-08-fail-no-signal"),
])
def test_r_hpge_08_preamp_signal(has_signal: bool, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_08_preamp_signal(has_signal)
    assert res.rule_id == "R-HPGE-08"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-HPGE-09: تعويض القطب والصفر (Pole-Zero)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("has_distortion, expected_verdict, test_id", [
    (False, Verdict.PASS, "R-HPGE-09-pass-undistorted"),
    (True, Verdict.OUT_OF_SPEC, "R-HPGE-09-oos-distortion"),
])
def test_r_hpge_09_pole_zero(has_distortion: bool, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_09_pole_zero(has_distortion)
    assert res.rule_id == "R-HPGE-09"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-HPGE-10: معدل العد الإجمالي (2000 cps) وزمن الموت (5.0%)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cps, dt, expected_verdict, test_id", [
    # اختبار حدود cps عند زمن موت آمن (4.0%)
    (1999.0, 4.0, Verdict.PASS, "R-HPGE-10-cps-below-pass"),
    (2000.0, 4.0, Verdict.PASS, "R-HPGE-10-cps-exact-boundary-pass"),
    (2001.0, 4.0, Verdict.OUT_OF_SPEC, "R-HPGE-10-cps-above-oos"),
    # اختبار حدود زمن الموت عند cps آمن (1000 cps)
    (1000.0, 4.99, Verdict.PASS, "R-HPGE-10-dt-below-pass"),
    (1000.0, 5.00, Verdict.PASS, "R-HPGE-10-dt-exact-boundary-pass"),
    (1000.0, 5.01, Verdict.OUT_OF_SPEC, "R-HPGE-10-dt-above-oos"),
])
def test_r_hpge_10_mca_rates(cps: float, dt: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_10_mca_rates(total_cps=cps, dead_time_pct=dt)
    assert res.rule_id == "R-HPGE-10"
    assert res.verdict == expected_verdict


# ---------------------------------------------------------------------------
# R-HPGE-11: إحصاء مساحة القمة الطيفية (10000 نبضة)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("net_counts, expected_verdict, test_id", [
    (9999.0, Verdict.WAIT, "R-HPGE-11-below-boundary-wait"),
    (10000.0, Verdict.PASS, "R-HPGE-11-exact-boundary-pass"),
    (10001.0, Verdict.PASS, "R-HPGE-11-above-boundary-pass"),
])
def test_r_hpge_11_peak_counts(net_counts: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_11_peak_counts(net_counts)
    assert res.rule_id == "R-HPGE-11"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-HPGE-12: دقة FWHM (مع قيمة اسمية 2.0 keV: 110%=2.2 keV, 125%=2.5 keV)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("measured, expected_id, expected_verdict, test_id", [
    # حدود عتبة 110% (2.200 keV)
    (2.199, "R-HPGE-12A", Verdict.PASS, "R-HPGE-12-pass-below-110"),
    (2.200, "R-HPGE-12A", Verdict.PASS, "R-HPGE-12-pass-exact-110"),
    (2.201, "R-HPGE-12B", Verdict.OUT_OF_SPEC, "R-HPGE-12-oos-above-110"),
    # حدود عتبة 125% (2.500 keV)
    (2.499, "R-HPGE-12B", Verdict.OUT_OF_SPEC, "R-HPGE-12-oos-below-125"),
    (2.500, "R-HPGE-12B", Verdict.OUT_OF_SPEC, "R-HPGE-12-oos-exact-125"),
    (2.501, "R-HPGE-12C", Verdict.FAILURE, "R-HPGE-12-fail-above-125"),
])
def test_r_hpge_12_fwhm_with_nominal(measured: float, expected_id: str, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_12_fwhm(measured_fwhm_kev=measured, nominal_fwhm_kev=2.0)
    assert res.rule_id == expected_id
    assert res.verdict == expected_verdict
    assert res.confirmed is False  # [TBC]


@pytest.mark.parametrize("measured, expected_id, expected_verdict, test_id", [
    # الحدود البديلة: 1.9 keV و 2.4 keV
    (1.899, "R-HPGE-12A", Verdict.PASS, "R-HPGE-12-fallback-below-1.9"),
    (1.900, "R-HPGE-12A", Verdict.PASS, "R-HPGE-12-fallback-exact-1.9"),
    (1.901, "R-HPGE-12B", Verdict.OUT_OF_SPEC, "R-HPGE-12-fallback-above-1.9"),
    (2.399, "R-HPGE-12B", Verdict.OUT_OF_SPEC, "R-HPGE-12-fallback-below-2.4"),
    (2.400, "R-HPGE-12B", Verdict.OUT_OF_SPEC, "R-HPGE-12-fallback-exact-2.4"),
    (2.401, "R-HPGE-12C", Verdict.FAILURE, "R-HPGE-12-fallback-above-2.4"),
])
def test_r_hpge_12_fwhm_fallback(measured: float, expected_id: str, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_12_fwhm(measured_fwhm_kev=measured, nominal_fwhm_kev=None)
    assert res.rule_id == expected_id
    assert res.verdict == expected_verdict


# ---------------------------------------------------------------------------
# R-HPGE-13 / 14: معامل شكل النبضة FWTM/FWHM (عتبة 1.90)
# الشرط: < 1.90 (PASS - R-HPGE-13)؛ >= 1.90 (OUT_OF_SPEC - R-HPGE-14)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("val, expected_id, expected_verdict, test_id", [
    (1.899, "R-HPGE-13", Verdict.PASS, "R-HPGE-13-just-below-1.90-pass"),
    (1.900, "R-HPGE-14", Verdict.OUT_OF_SPEC, "R-HPGE-14-exact-1.90-oos"),
    (1.901, "R-HPGE-14", Verdict.OUT_OF_SPEC, "R-HPGE-14-just-above-1.90-oos"),
])
def test_r_hpge_13_14_form_factor(val: float, expected_id: str, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_13_14_form_factor(val)
    assert res.rule_id == expected_id
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-HPGE-15: الكفاءة النسبية (مع nominal=20.0%: 95%=19.0%, 80%=16.0%)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("eff, expected_id, expected_verdict, test_id", [
    # حدود عتبة 95% (19.00%)
    (19.01, "R-HPGE-15A", Verdict.PASS, "R-HPGE-15-pass-above-95"),
    (19.00, "R-HPGE-15A", Verdict.PASS, "R-HPGE-15-pass-exact-95"),
    (18.99, "R-HPGE-15B", Verdict.OUT_OF_SPEC, "R-HPGE-15-oos-below-95"),
    # حدود عتبة 80% (16.00%)
    (16.01, "R-HPGE-15B", Verdict.OUT_OF_SPEC, "R-HPGE-15-oos-above-80"),
    (16.00, "R-HPGE-15B", Verdict.OUT_OF_SPEC, "R-HPGE-15-oos-exact-80"),
    (15.99, "R-HPGE-15C", Verdict.FAILURE, "R-HPGE-15-fail-below-80"),
])
def test_r_hpge_15_efficiency(eff: float, expected_id: str, expected_verdict: Verdict, test_id: str):
    res = eval_r_hpge_15_efficiency(measured_eff_pct=eff, nominal_eff_pct=20.0)
    assert res.rule_id == expected_id
    assert res.verdict == expected_verdict
    assert res.confirmed is False  # [TBC]


def test_r_hpge_15_invalid_nominal_efficiency():
    """رفض كفاءة اسمية غير موجبة."""
    with pytest.raises(ValueError):
        eval_r_hpge_15_efficiency(measured_eff_pct=20.0, nominal_eff_pct=0.0)



# ---------------------------------------------------------------------------
# R-NAI-01: استقرار جهد PMT لكواشف NaI (عتبة 2.0%)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drift, tol, expected_verdict, test_id", [
    (1.99, 2.0, Verdict.PASS, "R-NAI-01-pos-below-pass"),
    (2.00, 2.0, Verdict.PASS, "R-NAI-01-pos-exact-boundary-pass"),
    (2.01, 2.0, Verdict.OUT_OF_SPEC, "R-NAI-01-pos-above-oos"),
    (-1.99, 2.0, Verdict.PASS, "R-NAI-01-neg-below-pass"),
    (-2.00, 2.0, Verdict.PASS, "R-NAI-01-neg-exact-boundary-pass"),
    (-2.01, 2.0, Verdict.OUT_OF_SPEC, "R-NAI-01-neg-above-oos"),
    (1.5, None, Verdict.WARNING, "R-NAI-01-missing-tolerance-undefined"),
])
def test_r_nai_01_pmt_voltage(drift: float, tol: Optional[float], expected_verdict: Verdict, test_id: str):
    res = eval_r_nai_01_pmt_voltage(drift, tolerance_pct=tol)
    assert res.rule_id == "R-NAI-01"
    assert res.verdict == expected_verdict
    assert res.confirmed is (tol is not None)


# ---------------------------------------------------------------------------
# R-NAI-02: الدقة الطاقية %R لـ NaI 3x3 (عتبات 7.5% و 9.0%)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("res_pct, expected_id, expected_verdict, test_id", [
    # عتبة 7.5%
    (7.49, "R-NAI-02A", Verdict.PASS, "R-NAI-02-pass-below-7.5"),
    (7.50, "R-NAI-02A", Verdict.PASS, "R-NAI-02-pass-exact-7.5"),
    (7.51, "R-NAI-02B", Verdict.OUT_OF_SPEC, "R-NAI-02-oos-above-7.5"),
    # عتبة 9.0%
    (8.99, "R-NAI-02B", Verdict.OUT_OF_SPEC, "R-NAI-02-oos-below-9.0"),
    (9.00, "R-NAI-02B", Verdict.OUT_OF_SPEC, "R-NAI-02-oos-exact-9.0"),
    (9.01, "R-NAI-02C", Verdict.OUT_OF_SPEC, "R-NAI-02-oos-above-9.0"),
    (12.0, "R-NAI-02C", Verdict.OUT_OF_SPEC, "R-NAI-02-degraded-diagnosis-oos"),
])
def test_r_nai_02_resolution(res_pct: float, expected_id: str, expected_verdict: Verdict, test_id: str):
    res = eval_r_nai_02_resolution(res_pct)
    assert res.rule_id == expected_id
    assert res.verdict == expected_verdict
    assert res.verdict != Verdict.FAILURE  # Strictly no failure per Decision 15
    assert res.confirmed is False  # [TBC]


def test_r_nai_02_custom_dimensions_and_cert():
    """Test R-NAI-02 handling of non-3x3 crystal dimensions and certificate limits."""
    # Non 3x3 without certificate -> neutral WARNING
    res_non_3x3 = eval_r_nai_02_resolution(8.0, diameter_inch=2.0, height_inch=2.0)
    assert res_non_3x3.verdict == Verdict.WARNING
    assert "تختلف عن 3x3 بوصة" in res_non_3x3.message_ar

    # Custom certificate limit (e.g. 8.0%)
    res_cert_pass = eval_r_nai_02_resolution(7.9, nominal_resolution_pct=8.0)
    assert res_cert_pass.verdict == Verdict.PASS

    res_cert_oos = eval_r_nai_02_resolution(8.1, nominal_resolution_pct=8.0)
    assert res_cert_oos.verdict == Verdict.OUT_OF_SPEC


# ---------------------------------------------------------------------------
# R-NAI-03: انجراف قمة السيزيوم (عتبة 2.0 قنوات خلال ساعتين)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drift_pct, time_h, expected_verdict, test_id", [
    (0.99, 2.0, Verdict.PASS, "R-NAI-03-below-boundary-pass"),
    (1.00, 2.0, Verdict.PASS, "R-NAI-03-exact-boundary-pass"),
    (1.01, 2.0, Verdict.OUT_OF_SPEC, "R-NAI-03-above-boundary-oos"),
    (-1.01, 2.0, Verdict.OUT_OF_SPEC, "R-NAI-03-neg-above-boundary-oos"),
    (0.5, 1.5, Verdict.WAIT, "R-NAI-03-short-duration-wait"),
    (1.5, 1.5, Verdict.WAIT, "R-NAI-03-short-duration-wait-even-with-drift"),
    (0.5, 3.0, Verdict.PASS, "R-NAI-03-long-duration-stable-pass"),
    (1.2, 3.0, Verdict.OUT_OF_SPEC, "R-NAI-03-long-duration-drift-oos"),
])
def test_r_nai_03_gain_drift(drift_pct: float, time_h: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_nai_03_gain_drift(drift_pct=drift_pct, duration_hours=time_h)
    assert res.rule_id == "R-NAI-03"
    assert res.verdict == expected_verdict
    assert res.confirmed is True  # Decision 16: confirmed by engineer


# ---------------------------------------------------------------------------
# R-INST-01: سريان شهادة معايرة الجهاز
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exp_date, check_d, expected_verdict, test_id", [
    (date(2026, 10, 28), date(2026, 10, 27), Verdict.PASS, "R-INST-01-future-pass"),
    (date(2026, 10, 27), date(2026, 10, 27), Verdict.PASS, "R-INST-01-exact-today-pass"),
    (date(2026, 10, 26), date(2026, 10, 27), Verdict.WARNING, "R-INST-01-expired-yesterday-warning"),
])
def test_r_inst_01_calibration(exp_date: date, check_d: date, expected_verdict: Verdict, test_id: str):
    res = eval_r_inst_01_calibration(cal_expiry=exp_date, check_date=check_d)
    assert res.rule_id == "R-INST-01"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-INST-02: تموج مجهز الجهد العالي (100 mV) والتنظيم (0.01%)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ripple, reg, expected_verdict, test_id", [
    # حدود التموج
    (99.9, 0.005, Verdict.PASS, "R-INST-02-ripple-below-pass"),
    (100.0, 0.005, Verdict.PASS, "R-INST-02-ripple-exact-boundary-pass"),
    (100.1, 0.005, Verdict.OUT_OF_SPEC, "R-INST-02-ripple-above-oos"),
    # حدود التنظيم
    (50.0, 0.009, Verdict.PASS, "R-INST-02-reg-below-pass"),
    (50.0, 0.010, Verdict.PASS, "R-INST-02-reg-exact-boundary-pass"),
    (50.0, 0.011, Verdict.OUT_OF_SPEC, "R-INST-02-reg-above-oos"),
])
def test_r_inst_02_hv_power_supply(ripple: float, reg: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_inst_02_hv_power_supply(ripple_mv=ripple, regulation_pct=reg)
    assert res.rule_id == "R-INST-02"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-INST-03: زمن تشكيل المضخم (نطاق 3-12 us؛ فحص الجرمانيوم >= 4 us)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rng_min, rng_max, selected, expected_verdict, test_id", [
    (3.0, 12.0, 4.0, Verdict.PASS, "R-INST-03-exact-settings-pass"),
    (3.0, 12.0, 3.99, Verdict.BLOCK, "R-INST-03-selected-below-4-block"),
    (3.0, 12.0, 4.01, Verdict.PASS, "R-INST-03-selected-above-4-pass"),
    (3.1, 12.0, 4.0, Verdict.BLOCK, "R-INST-03-range-min-incapable-block"),
    (3.0, 11.9, 4.0, Verdict.BLOCK, "R-INST-03-range-max-incapable-block"),
])
def test_r_inst_03_amplifier_shaping(rng_min: float, rng_max: float, selected: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_inst_03_amplifier_shaping(
        shaping_range_min_us=rng_min,
        shaping_range_max_us=rng_max,
        selected_shaping_us=selected,
    )
    assert res.rule_id == "R-INST-03"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# R-SRC-01: فعالية مصدر المعايرة (37000 Bq ± 50%: من 18500 إلى 55500 Bq)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("act_bq, expected_verdict, test_id", [
    # الحد الأدنى 18500 Bq
    (18499.0, Verdict.WARNING, "R-SRC-01-below-lower-warning"),
    (18500.0, Verdict.PASS, "R-SRC-01-exact-lower-pass"),
    (18501.0, Verdict.PASS, "R-SRC-01-above-lower-pass"),
    # الحد الأقصى 55500 Bq
    (55499.0, Verdict.PASS, "R-SRC-01-below-upper-pass"),
    (55500.0, Verdict.PASS, "R-SRC-01-exact-upper-pass"),
    (55501.0, Verdict.WARNING, "R-SRC-01-above-upper-warning"),
])
def test_r_src_01_source_activity(act_bq: float, expected_verdict: Verdict, test_id: str):
    res = eval_r_src_01_source_activity(act_bq)
    assert res.rule_id == "R-SRC-01"
    assert res.verdict == expected_verdict
    assert res.confirmed is False  # [TBC]


# ---------------------------------------------------------------------------
# RULE-SEC-01: فصل المهام ومنع الاعتماد الذاتي
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tech_id, app_id, expected_verdict, test_id", [
    (1, 2, Verdict.PASS, "RULE-SEC-01-pass-different-users"),
    (1, 1, Verdict.BLOCK, "RULE-SEC-01-block-self-approval"),
])
def test_rule_sec_01_separation_of_duties(tech_id: int, app_id: int, expected_verdict: Verdict, test_id: str):
    res = eval_rule_sec_01_separation_of_duties(tech_id, app_id)
    assert res.rule_id == "RULE-SEC-01"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# RULE-SEC-02: حظر اعتماد مدير النظام التقني
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role, expected_verdict, test_id", [
    ("LAB_MANAGER", Verdict.PASS, "RULE-SEC-02-pass-lab-manager"),
    ("admin", Verdict.BLOCK, "RULE-SEC-02-block-admin"),
    ("ADMIN", Verdict.BLOCK, "RULE-SEC-02-block-admin-upper"),
])
def test_rule_sec_02_admin_approval_prohibition(role: str, expected_verdict: Verdict, test_id: str):
    res = eval_rule_sec_02_admin_approval_prohibition(role)
    assert res.rule_id == "RULE-SEC-02"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# RULE-SEC-03: تجميد السجل المعتمد
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status, is_mod, expected_verdict, test_id", [
    ("DRAFT", True, Verdict.PASS, "RULE-SEC-03-pass-draft-editable"),
    ("PENDING_APPROVAL", True, Verdict.PASS, "RULE-SEC-03-pass-pending-editable"),
    ("APPROVED", True, Verdict.BLOCK, "RULE-SEC-03-block-approved-modification"),
    ("APPROVED", False, Verdict.PASS, "RULE-SEC-03-pass-approved-read-only"),
])
def test_rule_sec_03_immutable_approved_record(status: str, is_mod: bool, expected_verdict: Verdict, test_id: str):
    res = eval_rule_sec_03_immutable_approved_record(status, is_mod)
    assert res.rule_id == "RULE-SEC-03"
    assert res.verdict == expected_verdict
    assert res.confirmed is True


# ---------------------------------------------------------------------------
# قاعدة التجميع الصارمة (Verdict Aggregation)
# ---------------------------------------------------------------------------

def test_aggregate_verdicts_hierarchy():
    """التحقق من التسلسل الهرمي الصارم للأحكام:
    FAILURE > BLOCK > WAIT > OUT_OF_SPEC > WARNING > PASS
    """
    def r(v: Verdict) -> RuleResult:
        return RuleResult(
            rule_id="R-TEST", verdict=v, message_ar="",
            measured=0, limit=0, confirmed=True
        )

    # قائمة فارغة
    assert aggregate_verdicts([]) == Verdict.PASS

    # كلها PASS
    assert aggregate_verdicts([r(Verdict.PASS), r(Verdict.PASS)]) == Verdict.PASS

    # PASS + WARNING -> WARNING
    assert aggregate_verdicts([r(Verdict.PASS), r(Verdict.WARNING)]) == Verdict.WARNING

    # PASS + WARNING + OUT_OF_SPEC -> OUT_OF_SPEC
    assert aggregate_verdicts([r(Verdict.PASS), r(Verdict.WARNING), r(Verdict.OUT_OF_SPEC)]) == Verdict.OUT_OF_SPEC

    # OUT_OF_SPEC + WAIT -> WAIT
    assert aggregate_verdicts([r(Verdict.OUT_OF_SPEC), r(Verdict.WAIT)]) == Verdict.WAIT

    # WAIT + BLOCK -> BLOCK
    assert aggregate_verdicts([r(Verdict.WAIT), r(Verdict.BLOCK)]) == Verdict.BLOCK

    # BLOCK + FAILURE -> FAILURE
    assert aggregate_verdicts([r(Verdict.BLOCK), r(Verdict.FAILURE)]) == Verdict.FAILURE

    # وجود عطل حرج يغلب أي حكم آخر دائماً
    assert aggregate_verdicts([
        r(Verdict.PASS), r(Verdict.OUT_OF_SPEC), r(Verdict.BLOCK), r(Verdict.FAILURE)
    ]) == Verdict.FAILURE


# ---------------------------------------------------------------------------
# اختبارات كائن العتبات والتحميل والتعديل (SystemThresholds)
# ---------------------------------------------------------------------------

def test_system_thresholds_serialization_and_override():
    """التحقق من تجميد كائن العتبات وتحميله وتعديل قيمه بسلاسة."""
    defaults = get_default_thresholds()
    assert defaults.hpge_cooling_min_hours.value == 6.0
    assert defaults.hpge_cooling_min_hours.confirmed is True

    # محاولة تعديل مباشر تفشل لأنه frozen
    with pytest.raises(Exception):
        defaults.hpge_cooling_min_hours.value = 8.0  # type: ignore

    # تعديل عبر قاموس
    custom = load_thresholds_from_dict({"hpge_cooling_min_hours": 8.0})
    assert custom.hpge_cooling_min_hours.value == 8.0
    # الحقول الأخرى بقيت على حالها
    assert custom.hpge_hv_ramp_max_rate_v_per_s.value == 10.0

    # تسلسل JSON واستعادته
    json_str = defaults.to_json()
    reloaded = SystemThresholds.model_validate_json(json_str)
    assert reloaded.hpge_cooling_min_hours.value == 6.0


def test_system_thresholds_json_loading_and_edge_cases():
    """اختبار تحميل العتبات من قاموس أو JSON وحالات التحقق."""
    from kashif.core.thresholds import load_thresholds_from_json
    defaults = get_default_thresholds()

    # قاموس فارغ يعيد الافتراضيات
    assert load_thresholds_from_dict({}).hpge_cooling_min_hours.value == 6.0

    # تعديل عبر كائن كامل
    full_dict = {
        "hpge_cooling_min_hours": {
            "value": 12.0,
            "confirmed": True,
            "source": "Custom Source",
            "description_ar": "Custom Desc",
        },
        "hpge_nim_voltage_tolerance_pct": "invalid_type",  # نوع غير مدعوم يُهمل
    }
    loaded = load_thresholds_from_dict(full_dict)
    assert loaded.hpge_cooling_min_hours.value == 12.0
    assert loaded.hpge_cooling_min_hours.source == "Custom Source"
    assert loaded.hpge_nim_voltage_tolerance_pct.value == 5.0

    # تحميل من نص JSON صحيح
    json_str = defaults.to_json()
    from_json = load_thresholds_from_json(json_str)
    assert from_json.hpge_cooling_min_hours.value == 6.0

    # تحميل من نص JSON ليس كائناً
    with pytest.raises(ValueError):
        load_thresholds_from_json('"just a string"')

# ---------------------------------------------------------------------------
# HV Apply Gate (evaluate_hv_apply_gate) Tests
# ---------------------------------------------------------------------------

def test_evaluate_hv_apply_gate_all_pass():
    """Test evaluate_hv_apply_gate when all conditions are fully met."""
    from kashif.core.models import HPGeDetector, SemiconductorType, VoltagePolarity
    from kashif.core.rules import evaluate_hv_apply_gate

    detector = HPGeDetector(
        code="HPGE-GATE-1",
        manufacturer="Canberra",
        model="GC2018",
        serial_number="SN-GATE-1",
        semiconductor_type=SemiconductorType.P_TYPE,
        certified_polarity=VoltagePolarity.POSITIVE,
        certificate_ref="CERT-2024-01",
        cooling_hours_cert=6.0,
        hv_ramp_max_v_per_s=10.0,
        nominal_voltage=3000.0,
        voltage_polarity=VoltagePolarity.POSITIVE,
    )

    verdict, results = evaluate_hv_apply_gate(
        detector=detector,
        applied_polarity=VoltagePolarity.POSITIVE,
        cooling_hours=6.5,
        ramp_rate_v_per_s=8.0,
    )
    assert verdict == Verdict.PASS
    assert len(results) == 3
    assert all(r.verdict == Verdict.PASS for r in results)


def test_evaluate_hv_apply_gate_blocks_on_polarity_mismatch():
    """Test evaluate_hv_apply_gate BLOCK on polarity violation."""
    from kashif.core.models import HPGeDetector, SemiconductorType, VoltagePolarity
    from kashif.core.rules import evaluate_hv_apply_gate

    detector = HPGeDetector(
        code="HPGE-GATE-2",
        manufacturer="Canberra",
        model="GC2018",
        serial_number="SN-GATE-2",
        semiconductor_type=SemiconductorType.P_TYPE,
        certified_polarity=VoltagePolarity.POSITIVE,
        certificate_ref="CERT-2024-02",
        cooling_hours_cert=6.0,
        hv_ramp_max_v_per_s=10.0,
        nominal_voltage=3000.0,
        voltage_polarity=VoltagePolarity.POSITIVE,
    )

    verdict, results = evaluate_hv_apply_gate(
        detector=detector,
        applied_polarity=VoltagePolarity.NEGATIVE,
        cooling_hours=7.0,
        ramp_rate_v_per_s=5.0,
    )
    assert verdict == Verdict.BLOCK
    pol_res = [r for r in results if r.rule_id == "R-HPGE-06"][0]
    assert pol_res.verdict == Verdict.BLOCK


def test_evaluate_hv_apply_gate_blocks_on_insufficient_cooling():
    """Test evaluate_hv_apply_gate BLOCK on insufficient cooling."""
    from kashif.core.models import HPGeDetector, SemiconductorType, VoltagePolarity
    from kashif.core.rules import evaluate_hv_apply_gate

    detector = HPGeDetector(
        code="HPGE-GATE-3",
        manufacturer="Ortec",
        model="GEM",
        serial_number="SN-GATE-3",
        semiconductor_type=SemiconductorType.N_TYPE,
        certified_polarity=VoltagePolarity.NEGATIVE,
        certificate_ref="CERT-2024-03",
        cooling_hours_cert=8.0,  # 8 hours required by cert
        hv_ramp_max_v_per_s=10.0,
        nominal_voltage=2500.0,
        voltage_polarity=VoltagePolarity.NEGATIVE,
    )

    verdict, results = evaluate_hv_apply_gate(
        detector=detector,
        applied_polarity=VoltagePolarity.NEGATIVE,
        cooling_hours=7.5,  # < 8.0h
        ramp_rate_v_per_s=5.0,
    )
    assert verdict == Verdict.BLOCK
    cooling_res = [r for r in results if r.rule_id == "R-HPGE-03"][0]
    assert cooling_res.verdict == Verdict.BLOCK


def test_evaluate_hv_apply_gate_blocks_on_ramp_exceeded():
    """Test evaluate_hv_apply_gate BLOCK on ramp rate limit violation."""
    from kashif.core.models import HPGeDetector, SemiconductorType, VoltagePolarity
    from kashif.core.rules import evaluate_hv_apply_gate

    detector = HPGeDetector(
        code="HPGE-GATE-4",
        manufacturer="Ortec",
        model="GEM",
        serial_number="SN-GATE-4",
        semiconductor_type=SemiconductorType.P_TYPE,
        certified_polarity=VoltagePolarity.POSITIVE,
        certificate_ref="CERT-2024-04",
        cooling_hours_cert=6.0,
        hv_ramp_max_v_per_s=5.0,  # Strict 5 V/s
        hv_ramp_reference="Manual section 4.1",
        nominal_voltage=3000.0,
        voltage_polarity=VoltagePolarity.POSITIVE,
    )

    verdict, results = evaluate_hv_apply_gate(
        detector=detector,
        applied_polarity=VoltagePolarity.POSITIVE,
        cooling_hours=6.5,
        ramp_rate_v_per_s=5.5,  # Exceeds 5.0 V/s
    )
    assert verdict == Verdict.BLOCK
    ramp_res = [r for r in results if r.rule_id == "R-HPGE-07"][0]
    assert ramp_res.verdict == Verdict.BLOCK


def test_evaluate_hv_apply_gate_warning_on_incomplete_cert():
    """Test evaluate_hv_apply_gate returns WARNING when certificate is incomplete but nominal matches."""
    from kashif.core.models import HPGeDetector, SemiconductorType, VoltagePolarity
    from kashif.core.rules import evaluate_hv_apply_gate

    detector = HPGeDetector(
        code="HPGE-GATE-5",
        manufacturer="Canberra",
        model="GC2018",
        serial_number="SN-GATE-5",
        semiconductor_type=SemiconductorType.P_TYPE,
        certified_polarity=None,  # Missing cert polarity
        nominal_voltage=3000.0,
        voltage_polarity=VoltagePolarity.POSITIVE,
    )

    verdict, results = evaluate_hv_apply_gate(
        detector=detector,
        applied_polarity=VoltagePolarity.POSITIVE,
        cooling_hours=6.5,
        ramp_rate_v_per_s=5.0,
    )
    assert verdict == Verdict.WARNING
    pol_res = [r for r in results if r.rule_id == "R-HPGE-06"][0]
    assert pol_res.verdict == Verdict.WARNING

