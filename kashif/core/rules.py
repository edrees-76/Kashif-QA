"""محرك قواعد القرار والتحقق الفني الصريح لمنظومة كاشف (Rules Engine).

Pure decision functions implementing all rules from docs/decision-rules.md:
- R-HPGE-01 to R-HPGE-15
- R-NAI-01 to R-NAI-03
- R-INST-01 to R-INST-03
- R-SRC-01
- RULE-SEC-01 to RULE-SEC-03
- Verdict aggregation according to strict severity hierarchy.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from kashif.core.models import (
    HPGeDetector,
    Verdict,
    VoltagePolarity,
    WindowStatus,
)
from kashif.core.thresholds import (
    SystemThresholds,
    get_default_thresholds,
)


class RuleResult(BaseModel):
    """نتيجة تقييم قاعدة فنية أو إجرائية."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: str = Field(..., description="المعرّف الرسمي للقاعدة (مثال: R-HPGE-01)")
    verdict: Verdict = Field(..., description="الحكم الفني الناتج (PASS, OUT_OF_SPEC, FAILURE, WAIT, BLOCK, WARNING)")
    message_ar: str = Field(..., description="الرسالة الفنية والإجراء المطلوب باللغة العربية")
    measured: Any = Field(..., description="القيمة المقاسة أو الحالة الفعلية المفحوصة")
    limit: Any = Field(..., description="الحد المعياري أو الشرط المحدد في القاعدة")
    confirmed: bool = Field(..., description="هل عتبة القاعدة مؤكدة بنص صريح أم [TBC]")


# ---------------------------------------------------------------------------
# قواعد كواشف الجرمانيوم عالي النقاء (HPGe Rules)
# ---------------------------------------------------------------------------

def eval_r_hpge_01_window_integrity(
    window_status: WindowStatus,
    cryostat_struck: bool = False,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-01: سلامة الحاوية ونافذة البريليوم.
    
    الشرط: نافذة مكسورة أو الحاوية مصدومة بعنف.
    الحكم: FAILURE
    المرجع: IAEA MRNI-507 ص 7 البند 3.4.2 والشكل 2 (CONFIRMED).
    """
    is_failed = (window_status == WindowStatus.BROKEN) or cryostat_struck
    if is_failed:
        return RuleResult(
            rule_id="R-HPGE-01",
            verdict=Verdict.FAILURE,
            message_ar="نافذة البريليوم مكسورة أو الحاوية مصدومة بعنف؛ إيقاف الفحص فوراً وعدم تشغيل الجهد وإحالة الكاشف للصيانة وتثبيت ملصق عطل (FAILURE)",
            measured={"window_status": window_status.value, "cryostat_struck": cryostat_struck},
            limit="window_status != BROKEN and not cryostat_struck",
            confirmed=True,
        )
    return RuleResult(
        rule_id="R-HPGE-01",
        verdict=Verdict.PASS,
        message_ar="سلامة نافذة البريليوم والحاوية محققة",
        measured={"window_status": window_status.value, "cryostat_struck": cryostat_struck},
        limit="window_status != BROKEN and not cryostat_struck",
        confirmed=True,
    )


def eval_r_hpge_02_window_scratch(
    window_status: WindowStatus,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-02: سلامة نافذة البريليوم (خدش سطحي).
    
    الشرط: خدش سطحي غير نافذ (SCRATCHED).
    الحكم: OUT_OF_SPEC
    المرجع: IAEA MRNI-507 ص 7 البند 3.4.2 ([TBC]).
    """
    if window_status == WindowStatus.SCRATCHED:
        return RuleResult(
            rule_id="R-HPGE-02",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar="خدش سطحي غير نافذ في نافذة البريليوم؛ مراقبة ضغط التفريغ بدقة والتأكد من عدم تسرب الهواء",
            measured=window_status.value,
            limit="window_status != SCRATCHED",
            confirmed=False,
        )
    return RuleResult(
        rule_id="R-HPGE-02",
        verdict=Verdict.PASS,
        message_ar="نافذة البريليوم خالية من الخدوش السطحية",
        measured=window_status.value,
        limit="window_status != SCRATCHED",
        confirmed=False,
    )


def eval_r_hpge_03_cooling_time(
    cooling_hours: float,
    cooling_hours_cert: Optional[float] = None,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-03: مدة التبريد بالنيتروجين السائل.
    
    الشرط: cooling_hours < max(6.0, cooling_hours_cert)
    الحكم: BLOCK لرفع الجهد
    المرجع: IAEA MRNI-507 ص 7 البند 3.4.3 والقرار 15-2 (CONFIRMED).
    """
    t = thresholds or get_default_thresholds()
    default_min = t.hpge_cooling_min_hours.value

    if cooling_hours_cert is not None and cooling_hours_cert > default_min:
        effective_limit = cooling_hours_cert
        source_desc = f"شهادة الكاشف ({cooling_hours_cert:.1f} h)"
    else:
        effective_limit = default_min
        source_desc = f"الحد الأدنى الافتراضي ({default_min:.1f} h)"

    if cooling_hours < effective_limit:
        return RuleResult(
            rule_id="R-HPGE-03",
            verdict=Verdict.BLOCK,
            message_ar=f"مدة التبريد بالنيتروجين ({cooling_hours:.2f} h) أقل من الحد المطلوب ({effective_limit:.1f} h حسب {source_desc})؛ حظر رفع الجهد العالي والانتظار حتى استكمال التبريد",
            measured=cooling_hours,
            limit=f">= {effective_limit:.1f} h ({source_desc})",
            confirmed=t.hpge_cooling_min_hours.confirmed,
        )
    return RuleResult(
        rule_id="R-HPGE-03",
        verdict=Verdict.PASS,
        message_ar=f"مدة التبريد كافية ({cooling_hours:.2f} h >= {effective_limit:.1f} h حسب {source_desc})",
        measured=cooling_hours,
        limit=f">= {effective_limit:.1f} h ({source_desc})",
        confirmed=t.hpge_cooling_min_hours.confirmed,
    )


def eval_r_hpge_04_cryostat_vacuum(
    cryostat_wet_or_frozen: bool,
    vacuum_leak_detected: bool = False,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-04: التفريغ الفراغي للحاوية.
    
    الشرط: ظهور تندّي مائي أو تكون طبقة ثلج/صقيع خارجي على الحاوية.
    الحكم: FAILURE
    المرجع: IAEA MRNI-507 ص 7 البند 3.4.3 والشكل 4 (CONFIRMED).
    """
    has_leak = cryostat_wet_or_frozen or vacuum_leak_detected
    if has_leak:
        return RuleResult(
            rule_id="R-HPGE-04",
            verdict=Verdict.FAILURE,
            message_ar="ظهور تندّي مائي أو تكون طبقة ثلج/صقيع خارجي على الحاوية؛ وجود تسريب فراغي خطير ومنع تشغيل الجهد فوراً وتثبيت ملصق عطل (FAILURE)",
            measured={"cryostat_wet_or_frozen": cryostat_wet_or_frozen, "vacuum_leak_detected": vacuum_leak_detected},
            limit="not wet_or_frozen and not vacuum_leak",
            confirmed=True,
        )
    return RuleResult(
        rule_id="R-HPGE-04",
        verdict=Verdict.PASS,
        message_ar="العزل الفراغي للحاوية سليم وخالٍ من الصقيع والتكثف",
        measured={"cryostat_wet_or_frozen": cryostat_wet_or_frozen, "vacuum_leak_detected": vacuum_leak_detected},
        limit="not wet_or_frozen and not vacuum_leak",
        confirmed=True,
    )


def eval_r_hpge_05_nim_voltages(
    v_pos_24: float,
    v_neg_24: float,
    v_pos_12: float,
    v_neg_12: float,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-05: جهود سلة NIM القياسية (±24V, ±12V).
    
    الشرط: أي جهد يخرج عن سماحية ±5% من القيمة الاسمية.
    الحكم: OUT_OF_SPEC
    المرجع: IAEA MRNI-507 ص 9 البند 3.5.1 ([TBC]).
    """
    t = thresholds or get_default_thresholds()
    entry = t.hpge_nim_voltage_tolerance_pct
    tol_frac = entry.value / 100.0

    nominal_checks = [
        ("v_pos_24", v_pos_24, 24.0),
        ("v_neg_24", v_neg_24, -24.0),
        ("v_pos_12", v_pos_12, 12.0),
        ("v_neg_12", v_neg_12, -12.0),
    ]

    out_of_spec_items = []
    measured_dict = {}

    for name, val, nom in nominal_checks:
        measured_dict[name] = val
        diff_pct = abs(val - nom) / abs(nom) * 100.0
        if diff_pct > entry.value:
            out_of_spec_items.append(f"{name}={val:.2f}V (nom={nom:+.1f}V, diff={diff_pct:.1f}%)")

    if out_of_spec_items:
        return RuleResult(
            rule_id="R-HPGE-05",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar=f"جهود سلة NIM خارج السماحية المسموحة (±{entry.value:.1f}%): " + ", ".join(out_of_spec_items),
            measured=measured_dict,
            limit=f"±{entry.value:.1f}% of nominal (±24V, ±12V)",
            confirmed=entry.confirmed,
        )

    return RuleResult(
        rule_id="R-HPGE-05",
        verdict=Verdict.PASS,
        message_ar=f"كافة جهود سلة NIM ضمن السماحية المقبولة (±{entry.value:.1f}%)",
        measured=measured_dict,
        limit=f"±{entry.value:.1f}% of nominal (±24V, ±12V)",
        confirmed=entry.confirmed,
    )


def eval_r_hpge_06_hv_polarity(
    applied_polarity: VoltagePolarity,
    nominal_polarity: Optional[VoltagePolarity] = None,
    certified_polarity: Optional[VoltagePolarity] = None,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-06: قطبية الجهد العالي.
    
    يقارن قطبية المجهز المسجلة بـ certified_polarity عند وجودها.
    إن لم توجد شهادة موثقة فالحكم توثيق ناقص (WARNING) مع مقارنة الاسمية.
    أي عدم تطابق يمثل حظراً صارماً (BLOCK).
    المرجع: IAEA MRNI-507 ص 10 البند 3.5.5 والقرار 15-3 (CONFIRMED).
    """
    has_cert = certified_polarity is not None

    if not has_cert:
        # توثيق ناقص
        if nominal_polarity is not None and applied_polarity != nominal_polarity:
            return RuleResult(
                rule_id="R-HPGE-06",
                verdict=Verdict.BLOCK,
                message_ar=f"قطبية مجهز الجهد ({applied_polarity.value}) تخالف القطبية الاسمية للكاشف ({nominal_polarity.value}) مع نقص توثيق قطبية الشهادة؛ حظر رفع الجهد منعاً لتلف بلورة الجرمانيوم",
                measured=applied_polarity.value,
                limit=nominal_polarity.value,
                confirmed=True,
            )
        return RuleResult(
            rule_id="R-HPGE-06",
            verdict=Verdict.WARNING,
            message_ar=f"توثيق ناقص: لم تُسجل القطبية المعتمدة في شهادة الكاشف؛ تم التحقق مقابل القطبية الاسمية ({applied_polarity.value}) مع وجوب التأكد من ملصق الكاشف قبل رفع الجهد",
            measured=applied_polarity.value,
            limit=nominal_polarity.value if nominal_polarity else "UNKNOWN",
            confirmed=False,
        )

    if applied_polarity != certified_polarity:
        return RuleResult(
            rule_id="R-HPGE-06",
            verdict=Verdict.BLOCK,
            message_ar=f"قطبية مجهز الجهد ({applied_polarity.value}) لا تطابق القطبية المعتمدة في شهادة الكاشف ({certified_polarity.value})؛ حظر رفع الجهد منعاً لتلف بلورة الجرمانيوم نهائياً",
            measured=applied_polarity.value,
            limit=certified_polarity.value,
            confirmed=True,
        )

    return RuleResult(
        rule_id="R-HPGE-06",
        verdict=Verdict.PASS,
        message_ar=f"قطبية الجهد العالي مطابقة لشهادة الكاشف المعتمدة ({applied_polarity.value})",
        measured=applied_polarity.value,
        limit=certified_polarity.value,
        confirmed=True,
    )


def eval_r_hpge_07_hv_ramp_rate(
    ramp_rate_v_per_s: float,
    max_rate_limit: Optional[float] = None,
    reference: Optional[str] = None,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-07: معدل رفع الجهد العالي.
    
    الحد الأقصى المسموح يُحفظ لكل كاشف من دليل المصنّع (الافتراضي 10.0 V/s = 100V/10s).
    تجاوز المعدل حظر صارم (BLOCK) يمنع الانتقال للخطوة التالية.
    المرجع: IAEA MRNI-507 ص 11 البند 3.5.14 والقرار 15-1 (CONFIRMED).
    """
    t = thresholds or get_default_thresholds()
    limit_val = max_rate_limit if (max_rate_limit is not None and max_rate_limit > 0) else t.hpge_hv_ramp_max_rate_v_per_s.value
    ref_desc = f" (مرجع: {reference})" if reference else ""

    if ramp_rate_v_per_s > limit_val:
        return RuleResult(
            rule_id="R-HPGE-07",
            verdict=Verdict.BLOCK,
            message_ar=f"معدل رفع الجهد العالي ({ramp_rate_v_per_s:.2f} V/s) يتجاوز الحد الأقصى المسموح للكاشف ({limit_val:.2f} V/s{ref_desc})؛ حظر إكمال الفحص والتأكيد على التدرج الآمن",
            measured=ramp_rate_v_per_s,
            limit=f"<= {limit_val:.2f} V/s{ref_desc}",
            confirmed=t.hpge_hv_ramp_max_rate_v_per_s.confirmed,
        )
    return RuleResult(
        rule_id="R-HPGE-07",
        verdict=Verdict.PASS,
        message_ar=f"معدل رفع الجهد العالي آمن وضمن الحدود المسموحة للكاشف ({ramp_rate_v_per_s:.2f} V/s <= {limit_val:.2f} V/s{ref_desc})",
        measured=ramp_rate_v_per_s,
        limit=f"<= {limit_val:.2f} V/s{ref_desc}",
        confirmed=t.hpge_hv_ramp_max_rate_v_per_s.confirmed,
    )


def evaluate_hv_apply_gate(
    detector: HPGeDetector,
    applied_polarity: VoltagePolarity,
    cooling_hours: float,
    ramp_rate_v_per_s: float,
    thresholds: Optional[SystemThresholds] = None,
) -> Tuple[Verdict, List[RuleResult]]:
    """بوابة تطبيق الجهد العالي لكاشف الجرمانيوم (HV Apply Gate).
    
    دالة نقية تجمع نتائج R-HPGE-03 و R-HPGE-06 و R-HPGE-07 مع حكم مجمّع.
    أي حكم BLOCK يعني حظر تطبيق الجهد فوراً ولا يتجاوزه تأكيد المستخدم.
    """
    res_cooling = eval_r_hpge_03_cooling_time(
        cooling_hours=cooling_hours,
        cooling_hours_cert=detector.cooling_hours_cert,
        thresholds=thresholds,
    )
    res_polarity = eval_r_hpge_06_hv_polarity(
        applied_polarity=applied_polarity,
        nominal_polarity=detector.voltage_polarity,
        certified_polarity=detector.certified_polarity,
        thresholds=thresholds,
    )
    res_ramp = eval_r_hpge_07_hv_ramp_rate(
        ramp_rate_v_per_s=ramp_rate_v_per_s,
        max_rate_limit=detector.hv_ramp_max_v_per_s,
        reference=detector.hv_ramp_reference,
        thresholds=thresholds,
    )

    results = [res_cooling, res_polarity, res_ramp]

    if any(r.verdict == Verdict.BLOCK for r in results):
        agg_verdict = Verdict.BLOCK
    elif any(r.verdict == Verdict.WARNING for r in results):
        agg_verdict = Verdict.WARNING
    else:
        agg_verdict = Verdict.PASS

    return agg_verdict, results


def eval_r_hpge_08_preamp_signal(
    has_preamp_signal: bool,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-08: إشارة المضخم الأولي.
    
    الشرط: عدم ظهور أي إشارة على راسم الذبذبات مع الجهد أو بدونه.
    الحكم: FAILURE
    المرجع: IAEA MRNI-507 ص 11 البند 3.5.12 و 3.5.13 (CONFIRMED).
    """
    if not has_preamp_signal:
        return RuleResult(
            rule_id="R-HPGE-08",
            verdict=Verdict.FAILURE,
            message_ar="عدم ظهور أي إشارة للمضخم الأولي على راسم الذبذبات؛ المضخم لا يعمل وإحالته للصيانة",
            measured=has_preamp_signal,
            limit=True,
            confirmed=True,
        )
    return RuleResult(
        rule_id="R-HPGE-08",
        verdict=Verdict.PASS,
        message_ar="إشارة المضخم الأولي ظاهرة وسليمة على راسم الذبذبات",
        measured=has_preamp_signal,
        limit=True,
        confirmed=True,
    )


def eval_r_hpge_09_pole_zero(
    has_pz_distortion: bool,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-09: تعويض القطب والصفر (P/Z).
    
    الشرط: وجود تشوه في نبضة المضخم (Overshoot أو Undershoot).
    الحكم: OUT_OF_SPEC
    المرجع: IAEA MRNI-507 ص 14 البند 3.6.7 والشكل 10 (CONFIRMED).
    """
    if has_pz_distortion:
        return RuleResult(
            rule_id="R-HPGE-09",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar="تشوه في نبضة المضخم (ارتداد علوي أو سفلي)؛ ضبط مقاومة الـ Pole-Zero حتى استواء خط الأساس",
            measured="DISTORTED",
            limit="UNDISTORTED",
            confirmed=True,
        )
    return RuleResult(
        rule_id="R-HPGE-09",
        verdict=Verdict.PASS,
        message_ar="نبضة المضخم مستوية وسليمة دون تشوه Pole-Zero",
        measured="CLEAN",
        limit="UNDISTORTED",
        confirmed=True,
    )


def eval_r_hpge_10_mca_rates(
    total_cps: float,
    dead_time_pct: float,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-10: معدل العد الإجمالي في MCA وزمن الموت.
    
    الشرط: total_cps > 2000 أو dead_time_pct > 5%.
    الحكم: OUT_OF_SPEC
    المرجع: IAEA MRNI-507 ص 14 البند 3.7.2 (2000 cps CONFIRMED; dead time 5% [TBC]).
    """
    t = thresholds or get_default_thresholds()
    cps_entry = t.hpge_mca_max_total_cps
    dt_entry = t.hpge_mca_max_dead_time_pct

    violates_cps = total_cps > cps_entry.value
    violates_dt = dead_time_pct > dt_entry.value

    if violates_cps or violates_dt:
        reasons = []
        if violates_cps:
            reasons.append(f"معدل النبضات ({total_cps:.1f} cps > {cps_entry.value:.0f} cps)")
        if violates_dt:
            reasons.append(f"زمن الموت ({dead_time_pct:.1f}% > {dt_entry.value:.1f}%)")
        return RuleResult(
            rule_id="R-HPGE-10",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar="خروج عن الحدود المسموحة: " + " و ".join(reasons) + "؛ يلزم زيادة المسافة بين المصدر والكاشف لتقليل معدل العد وتفادي تراكم النبضات",
            measured={"total_cps": total_cps, "dead_time_pct": dead_time_pct},
            limit=f"cps <= {cps_entry.value:.0f} and dead_time <= {dt_entry.value:.1f}%",
            confirmed=False,  # بسبب وجود حد زمن الموت TBC
        )

    return RuleResult(
        rule_id="R-HPGE-10",
        verdict=Verdict.PASS,
        message_ar=f"معدل العد الإجمالي ({total_cps:.1f} cps) وزمن الموت ({dead_time_pct:.1f}%) ضمن الحدود المقبولة",
        measured={"total_cps": total_cps, "dead_time_pct": dead_time_pct},
        limit=f"cps <= {cps_entry.value:.0f} and dead_time <= {dt_entry.value:.1f}%",
        confirmed=False,
    )


def eval_r_hpge_11_peak_counts(
    net_counts: float,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-11: إحصاء مساحة القمة الطيفية لـ 1332.5 keV.
    
    الشرط: net_counts < 10000 نبضة.
    الحكم: WAIT
    المرجع: IAEA MRNI-507 ص 14 البند 3.7.3 (CONFIRMED).
    """
    t = thresholds or get_default_thresholds()
    entry = t.hpge_peak_min_net_counts
    if net_counts < entry.value:
        return RuleResult(
            rule_id="R-HPGE-11",
            verdict=Verdict.WAIT,
            message_ar=f"المساحة الصافية لقمة الكوبالت ({net_counts:.0f} counts) أقل من {entry.value:.0f} نبضة؛ تمديد زمن القياس الحي لتقليل الشك الإحصائي (< 1%)",
            measured=net_counts,
            limit=f">= {entry.value:.0f}",
            confirmed=entry.confirmed,
        )
    return RuleResult(
        rule_id="R-HPGE-11",
        verdict=Verdict.PASS,
        message_ar=f"المساحة الصافية كافية إحصائياً ({net_counts:.0f} >= {entry.value:.0f})",
        measured=net_counts,
        limit=f">= {entry.value:.0f}",
        confirmed=entry.confirmed,
    )


def eval_r_hpge_12_fwhm(
    measured_fwhm_kev: float,
    nominal_fwhm_kev: Optional[float] = None,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-12: دقة التحليل الطاقي (FWHM) لقمة 1332.5 keV لكاشف الجرمانيوم.
    
    الحدود (في حال توفر القيمة الاسمية):
    - R-HPGE-12A (PASS): measured <= 1.10 * nominal
    - R-HPGE-12B (OUT_OF_SPEC): 1.10 * nominal < measured <= 1.25 * nominal
    - R-HPGE-12C (FAILURE): measured > 1.25 * nominal
    
    الحدود البديلة (عند عدم توفر الاسمي):
    - R-HPGE-12A (PASS): measured <= 1.9 keV
    - R-HPGE-12B (OUT_OF_SPEC): 1.9 < measured <= 2.4 keV
    - R-HPGE-12C (FAILURE): measured > 2.4 keV
    
    المرجع: IAEA MRNI-507 ص 15 البند 3.7.7 وص 17 البند 5.2 ([TBC]).
    """
    t = thresholds or get_default_thresholds()

    if nominal_fwhm_kev is not None and nominal_fwhm_kev > 0:
        pass_lim = t.hpge_fwhm_nominal_pass_ratio.value * nominal_fwhm_kev
        oos_lim = t.hpge_fwhm_nominal_out_of_spec_ratio.value * nominal_fwhm_kev
        limit_desc = f"pass <= {pass_lim:.3f} keV (110%), oos <= {oos_lim:.3f} keV (125%)"

        if measured_fwhm_kev <= pass_lim:
            return RuleResult(
                rule_id="R-HPGE-12A",
                verdict=Verdict.PASS,
                message_ar=f"دقة التحليل الطاقي ممتازة ومطابقة ({measured_fwhm_kev:.3f} keV <= {pass_lim:.3f} keV)",
                measured=measured_fwhm_kev,
                limit=limit_desc,
                confirmed=False,
            )
        elif measured_fwhm_kev <= oos_lim:
            return RuleResult(
                rule_id="R-HPGE-12B",
                verdict=Verdict.OUT_OF_SPEC,
                message_ar=f"دقة التحليل الطاقي خرجت عن المواصفات ({measured_fwhm_kev:.3f} keV بين {pass_lim:.3f} و {oos_lim:.3f} keV)؛ يلزم خفض الجهد وتجفيف المضخم وإعادة القياس",
                measured=measured_fwhm_kev,
                limit=limit_desc,
                confirmed=False,
            )
        else:
            return RuleResult(
                rule_id="R-HPGE-12C",
                verdict=Verdict.FAILURE,
                message_ar=f"دقة التحليل الطاقي في حالة عطل فني ({measured_fwhm_kev:.3f} keV > {oos_lim:.3f} keV)؛ تدهور جسيم في بلورة الكاشف أو الضجيج الإلكتروني وتثبيت ملصق عطل",
                measured=measured_fwhm_kev,
                limit=limit_desc,
                confirmed=False,
            )
    else:
        # Fallback values
        pass_lim = t.hpge_fwhm_fallback_pass_kev.value
        oos_lim = t.hpge_fwhm_fallback_out_of_spec_kev.value
        limit_desc = f"pass <= {pass_lim:.2f} keV, oos <= {oos_lim:.2f} keV"

        if measured_fwhm_kev <= pass_lim:
            return RuleResult(
                rule_id="R-HPGE-12A",
                verdict=Verdict.PASS,
                message_ar=f"دقة التحليل الطاقي ممتازة ومطابقة للحد المرجعي العام ({measured_fwhm_kev:.3f} keV <= {pass_lim:.2f} keV)",
                measured=measured_fwhm_kev,
                limit=limit_desc,
                confirmed=False,
            )
        elif measured_fwhm_kev <= oos_lim:
            return RuleResult(
                rule_id="R-HPGE-12B",
                verdict=Verdict.OUT_OF_SPEC,
                message_ar=f"دقة التحليل الطاقي خرجت عن الحد العام ({measured_fwhm_kev:.3f} keV بين {pass_lim:.2f} و {oos_lim:.2f} keV)؛ تجفيف المضخم وإعادة القياس",
                measured=measured_fwhm_kev,
                limit=limit_desc,
                confirmed=False,
            )
        else:
            return RuleResult(
                rule_id="R-HPGE-12C",
                verdict=Verdict.FAILURE,
                message_ar=f"دقة التحليل الطاقي عطل ({measured_fwhm_kev:.3f} keV > {oos_lim:.2f} keV)؛ تثبيت ملصق عطل",
                measured=measured_fwhm_kev,
                limit=limit_desc,
                confirmed=False,
            )


def eval_r_hpge_13_14_form_factor(
    form_factor_value: float,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-13 / R-HPGE-14: معامل شكل النبضة (FWTM/FWHM).
    
    الحدود:
    - R-HPGE-13 (PASS): FWTM/FWHM < 1.90
    - R-HPGE-14 (OUT_OF_SPEC): FWTM/FWHM >= 1.90
    
    المرجع: IAEA MRNI-507 ص 15 البند 3.7.8 (CONFIRMED).
    """
    t = thresholds or get_default_thresholds()
    entry = t.hpge_form_factor_max_ratio

    if form_factor_value < entry.value:
        return RuleResult(
            rule_id="R-HPGE-13",
            verdict=Verdict.PASS,
            message_ar=f"معامل شكل القمة غاوسي متناسق وسليم ({form_factor_value:.3f} < {entry.value:.2f})",
            measured=form_factor_value,
            limit=f"< {entry.value:.2f}",
            confirmed=entry.confirmed,
        )
    return RuleResult(
        rule_id="R-HPGE-14",
        verdict=Verdict.OUT_OF_SPEC,
        message_ar=f"معامل شكل القمة غير متناسق ({form_factor_value:.3f} >= {entry.value:.2f})؛ وجود ذيل غير متماثل (Peak Tailing/Incomplete charge collection)",
        measured=form_factor_value,
        limit=f"< {entry.value:.2f}",
        confirmed=entry.confirmed,
    )


def eval_r_hpge_15_efficiency(
    measured_eff_pct: float,
    nominal_eff_pct: float,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-HPGE-15: الكفاءة النسبية لكاشف الجرمانيوم.
    
    الحدود:
    - R-HPGE-15A (PASS): measured >= 0.95 * nominal
    - R-HPGE-15B (OUT_OF_SPEC): 0.80 * nominal <= measured < 0.95 * nominal
    - R-HPGE-15C (FAILURE): measured < 0.80 * nominal
    
    المرجع: IAEA MRNI-507 ص 16 البند 3.8 وص 17 البند 5.2 ([TBC]).
    """
    if nominal_eff_pct <= 0:
        raise ValueError("الكفاءة الاسمية للكاشف يجب أن تكون أكبر من الصفر")

    t = thresholds or get_default_thresholds()
    pass_lim = t.hpge_efficiency_pass_ratio.value * nominal_eff_pct
    oos_lim = t.hpge_efficiency_out_of_spec_ratio.value * nominal_eff_pct
    limit_desc = f"pass >= {pass_lim:.2f}% (95%), oos >= {oos_lim:.2f}% (80%)"

    if measured_eff_pct >= pass_lim:
        return RuleResult(
            rule_id="R-HPGE-15A",
            verdict=Verdict.PASS,
            message_ar=f"الكفاءة النسبية ممتازة ومطابقة للمواصفات ({measured_eff_pct:.2f}% >= {pass_lim:.2f}%)",
            measured=measured_eff_pct,
            limit=limit_desc,
            confirmed=False,
        )
    elif measured_eff_pct >= oos_lim:
        return RuleResult(
            rule_id="R-HPGE-15B",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar=f"انخفاض طفيف في الكفاءة النسبية ({measured_eff_pct:.2f}% بين {oos_lim:.2f}% و {pass_lim:.2f}%)؛ التحقق من دقة المسافة الهندسية (25 cm)",
            measured=measured_eff_pct,
            limit=limit_desc,
            confirmed=False,
        )
    else:
        return RuleResult(
            rule_id="R-HPGE-15C",
            verdict=Verdict.FAILURE,
            message_ar=f"انخفاض حاد في الكفاءة النسبية ({measured_eff_pct:.2f}% < {oos_lim:.2f}%)؛ زيادة في حجم الطبقة الميتة وتثبيت ملصق عطل",
            measured=measured_eff_pct,
            limit=limit_desc,
            confirmed=False,
        )


# ---------------------------------------------------------------------------
# قواعد كواشف يوديد الصوديوم الوميضية (NaI Rules)
# ---------------------------------------------------------------------------

def eval_r_nai_01_pmt_voltage(
    drift_pct: float,
    tolerance_pct: Optional[float] = None,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-NAI-01: استقرار مجهز الجهد العالي للأنبوب المضاعف الضوئي (PMT).
    
    حُذفت سماحية 2% المنسوبة وفق القرار 15-7.
    سماحية جهد تشغيل الكاشف حقل اختياري من مواصفة الكاشف؛
    وإن غابت فالنتيجة «غير محددة» محايدة (WARNING).
    المرجع: IAEA MRNI-504 والقرار 15-7.
    """
    abs_drift = abs(drift_pct)
    if tolerance_pct is None:
        return RuleResult(
            rule_id="R-NAI-01",
            verdict=Verdict.WARNING,
            message_ar=f"سماحية جهد تشغيل PMT غير محددة في مواصفة الكاشف (الانحراف المقاس: {abs_drift:.2f}%)؛ يلزم مراجعة وثيقة الكاشف",
            measured=drift_pct,
            limit="غير محددة في مواصفة الكاشف",
            confirmed=False,
        )

    if abs_drift > tolerance_pct:
        return RuleResult(
            rule_id="R-NAI-01",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar=f"تذبذب جهد التغذية للأنبوب المضاعف الضوئي ({abs_drift:.2f}%) يخرج عن سماحية الكاشف المعتمدة ({tolerance_pct:.2f}%)؛ فحص وضبط وحدة الجهد العالي",
            measured=drift_pct,
            limit=f"<= {tolerance_pct:.2f}%",
            confirmed=True,
        )
    return RuleResult(
        rule_id="R-NAI-01",
        verdict=Verdict.PASS,
        message_ar=f"جهد PMT مستقر ضمن سماحية الكاشف المعتمدة ({abs_drift:.2f}% <= {tolerance_pct:.2f}%)",
        measured=drift_pct,
        limit=f"<= {tolerance_pct:.2f}%",
        confirmed=True,
    )


def eval_r_nai_02_resolution(
    resolution_pct: float,
    nominal_resolution_pct: Optional[float] = None,
    diameter_inch: float = 3.0,
    height_inch: float = 3.0,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-NAI-02: الدقة الطاقية (%R) عند قمة السيزيوم 661.5 keV (MRNI-504 البند 3.5.4 والقرار 15-5).
    
    - حد الاجتياز من شهادة الكاشف (nominal_resolution_pct إن وجد).
    - وإلا 7.5% (PASS) و9.0% الافتراضيان لبلورة 3"x3" فقط.
    - لبلورة بأبعاد أخرى وبلا حد شهادة: نتيجة «غير محدد» محايدة (WARNING).
    - فوق الحد الأعلى = OUT_OF_SPEC برسالة «تدهور يحتاج تشخيصاً»، وليس FAILURE تلقائياً.
    - لا مسار يعيد FAILURE في R-NAI-02.
    """
    t = thresholds or get_default_thresholds()
    is_standard_3x3 = (abs(diameter_inch - 3.0) < 1e-4 and abs(height_inch - 3.0) < 1e-4)

    if nominal_resolution_pct is not None and nominal_resolution_pct > 0:
        pass_lim = nominal_resolution_pct
        oos_lim = nominal_resolution_pct * 1.20
        limit_desc = f"حد الشهادة <= {pass_lim:.2f}%"
        source_note = "حسب شهادة الكاشف"
    elif is_standard_3x3:
        pass_lim = t.nai_resolution_pass_pct.value
        oos_lim = t.nai_resolution_out_of_spec_pct.value
        limit_desc = f"pass <= {pass_lim:.1f}%, oos <= {oos_lim:.1f}%"
        source_note = "معياري لبلورة 3″×3″"
    else:
        return RuleResult(
            rule_id="R-NAI-02",
            verdict=Verdict.WARNING,
            message_ar=f"أبعاد البلورة ({diameter_inch:.1f} in x {height_inch:.1f} in) تختلف عن 3x3 بوصة ولا تتوفر قيمة اسمية في شهادة الكاشف؛ النتيجة غير محددة وتتطلب مراجعة مواصفة المصنّع",
            measured=resolution_pct,
            limit="غير محدد لبلورات غير 3″×3″ دون شهادة",
            confirmed=False,
        )

    if resolution_pct <= pass_lim:
        return RuleResult(
            rule_id="R-NAI-02A",
            verdict=Verdict.PASS,
            message_ar=f"الدقة الطاقية لكاشف الوميض ممتازة ومطابقة للمواصفات ({resolution_pct:.2f}% <= {pass_lim:.2f}% {source_note})",
            measured=resolution_pct,
            limit=limit_desc,
            confirmed=False,
        )
    elif resolution_pct <= oos_lim:
        return RuleResult(
            rule_id="R-NAI-02B",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar=f"تراجع في الدقة الطاقية ({resolution_pct:.2f}% بين {pass_lim:.2f}% و {oos_lim:.2f}% {source_note})؛ إعادة معايرة الجهد والتحقق من التوصيل البصري",
            measured=resolution_pct,
            limit=limit_desc,
            confirmed=False,
        )
    else:
        return RuleResult(
            rule_id="R-NAI-02C",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar=f"دقة الطاقة خارج المواصفات ({resolution_pct:.2f}% > {oos_lim:.2f}% {source_note})؛ تدهور يحتاج تشخيصاً في بلورة الوميض أو الأنبوب الضوئي",
            measured=resolution_pct,
            limit=limit_desc,
            confirmed=False,
        )


def eval_r_nai_03_gain_drift(
    drift_pct: float,
    duration_hours: float = 2.0,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-NAI-03: استقرار الكسب الطيفي والانجراف (Gain Drift).
    
    النسبة المئوية من طاقة قمة السيزيوم (Cs-137 661.5 keV) وفق القرارين 15-6 و16.
    الحد المعياري <= 1.0% خلال ساعتين (confirmed=True وفق القرار 16).
    إذا كانت مدة القياس أقل من ساعتين: مراقبة غير كافية (WAIT).
    إذا تجاوز الانجراف 1.0%: OUT_OF_SPEC.
    المرجع: معايير استقرار مطيافية NaI وتأكيد المهندس (القرار 16).
    """
    t = thresholds or get_default_thresholds()
    entry_drift = t.nai_gain_drift_max_pct
    entry_time = t.nai_gain_drift_time_hours
    max_drift_pct = entry_drift.value
    min_time_hours = entry_time.value
    abs_drift = abs(drift_pct)
    is_confirmed = entry_drift.confirmed and entry_time.confirmed

    if duration_hours < min_time_hours:
        return RuleResult(
            rule_id="R-NAI-03",
            verdict=Verdict.WAIT,
            message_ar=f"مراقبة غير كافية: مدة قياس استقرار الكسب ({duration_hours:.2f} h) أقل من المدة المعيارية ({min_time_hours:.1f} h)؛ يلزم استكمال القياس ساعتين على الأقل",
            measured={"drift_pct": drift_pct, "duration_hours": duration_hours},
            limit=f"<= {max_drift_pct:.1f}% in >= {min_time_hours:.1f} h",
            confirmed=is_confirmed,
        )

    if abs_drift > max_drift_pct:
        return RuleResult(
            rule_id="R-NAI-03",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar=f"انجراف طاقة قمة السيزيوم ({abs_drift:.2f}%) يتجاوز الحد المسموح ({max_drift_pct:.1f}%) خلال {duration_hours:.1f} ساعات؛ فحص التثبيت الحراري واستقرار قاعدة الأنبوب الضوئي",
            measured={"drift_pct": drift_pct, "duration_hours": duration_hours},
            limit=f"<= {max_drift_pct:.1f}% in {min_time_hours:.1f} h",
            confirmed=is_confirmed,
        )

    return RuleResult(
        rule_id="R-NAI-03",
        verdict=Verdict.PASS,
        message_ar=f"استقرار الكسب الطيفي ممتاز (الانجراف {abs_drift:.2f}% <= {max_drift_pct:.1f}% خلال {duration_hours:.1f} h)",
        measured={"drift_pct": drift_pct, "duration_hours": duration_hours},
        limit=f"<= {max_drift_pct:.1f}% in {min_time_hours:.1f} h",
        confirmed=is_confirmed,
    )


# ---------------------------------------------------------------------------
# قواعد صلاحية الأجهزة والمصادر (Instruments & Sources Rules)
# ---------------------------------------------------------------------------

def eval_r_inst_01_calibration(
    cal_expiry: date,
    check_date: Optional[date] = None,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-INST-01: سريان شهادة معايرة الجهاز.
    
    الشرط: cal_expiry < check_date
    الحكم: WARNING (تحذير شديد مع إلزام الفني بتسجيل تبرير فني)
    المرجع: IAEA MRNI-507 ص 6 البند 3.2 وقرار مدير المشروع رقم 5 (CONFIRMED).
    """
    curr = check_date or date.today()
    if cal_expiry < curr:
        return RuleResult(
            rule_id="R-INST-01",
            verdict=Verdict.WARNING,
            message_ar=f"شهادة معايرة الجهاز منتهية بتاريخ {cal_expiry} (اليوم: {curr})؛ يلزم تسجيل تبرير فني وإرفاقه بالتقرير",
            measured=cal_expiry.isoformat(),
            limit=f">= {curr.isoformat()}",
            confirmed=True,
        )
    return RuleResult(
        rule_id="R-INST-01",
        verdict=Verdict.PASS,
        message_ar=f"شهادة معايرة الجهاز سارية الصلاحية حتى {cal_expiry}",
        measured=cal_expiry.isoformat(),
        limit=f">= {curr.isoformat()}",
        confirmed=True,
    )


def eval_r_inst_02_hv_power_supply(
    ripple_mv: float,
    regulation_pct: float,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-INST-02: تموج وتنظيم مجهز الجهد العالي.
    
    الشرط: ripple_mv > 100 mV أو regulation_pct > 0.01%.
    الحكم: OUT_OF_SPEC
    المرجع: IAEA MRNI-507 ص 6 البند 3.2.2 (CONFIRMED).
    """
    t = thresholds or get_default_thresholds()
    max_rip = t.inst_hv_max_ripple_mv.value
    max_reg = t.inst_hv_max_regulation_pct.value

    rip_fail = ripple_mv > max_rip
    reg_fail = regulation_pct > max_reg

    if rip_fail or reg_fail:
        reasons = []
        if rip_fail:
            reasons.append(f"التموج ({ripple_mv:.1f} mV > {max_rip:.1f} mV)")
        if reg_fail:
            reasons.append(f"التنظيم ({regulation_pct:.4f}% > {max_reg:.2f}%)")
        return RuleResult(
            rule_id="R-INST-02",
            verdict=Verdict.OUT_OF_SPEC,
            message_ar="مجهز الجهد العالي خارج المواصفات: " + " و ".join(reasons),
            measured={"ripple_mv": ripple_mv, "regulation_pct": regulation_pct},
            limit=f"ripple <= {max_rip:.1f} mV and reg <= {max_reg:.2f}%",
            confirmed=True,
        )

    return RuleResult(
        rule_id="R-INST-02",
        verdict=Verdict.PASS,
        message_ar=f"مجهز الجهد العالي مطابق للمواصفات القياسية (تموج {ripple_mv:.1f} mV وتنظيم {regulation_pct:.4f}%)",
        measured={"ripple_mv": ripple_mv, "regulation_pct": regulation_pct},
        limit=f"ripple <= {max_rip:.1f} mV and reg <= {max_reg:.2f}%",
        confirmed=True,
    )


def eval_r_inst_03_amplifier_shaping(
    shaping_range_min_us: float,
    shaping_range_max_us: float,
    selected_shaping_us: Optional[float] = None,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-INST-03: نطاق زمن تشكيل المضخم الطيفي.
    
    الشرط: المضخم لا يدعم المدى 3 - 12 us، أو تم ضبطه على أقل من 4 us لفحص الجرمانيوم.
    الحكم: BLOCK
    المرجع: IAEA MRNI-507 ص 6 البند 3.2.3 وص 12 البند 3.6.2 (CONFIRMED).
    """
    t = thresholds or get_default_thresholds()
    min_cap = t.inst_amp_shaping_min_us.value
    max_cap = t.inst_amp_shaping_max_us.value
    hpge_min = t.inst_amp_hpge_shaping_min_us.value

    # التحقق من قدرة الجهاز على تغطية نطاق 3-12 us
    incapable = (shaping_range_min_us > min_cap) or (shaping_range_max_us < max_cap)
    setting_invalid = (selected_shaping_us is not None) and (selected_shaping_us < hpge_min)

    if incapable or setting_invalid:
        reasons = []
        if incapable:
            reasons.append(f"نطاق المضخم ({shaping_range_min_us}-{shaping_range_max_us} us) لا يغطي النطاق المطلوب ({min_cap:.0f}-{max_cap:.0f} us)")
        if setting_invalid:
            reasons.append(f"الزمن المضبوط ({selected_shaping_us:.1f} us) أقل من الحد الأدنى لفحص الجرمانيوم ({hpge_min:.0f} us)")
        return RuleResult(
            rule_id="R-INST-03",
            verdict=Verdict.BLOCK,
            message_ar="حظر استخدام إعدادات المضخم: " + " و ".join(reasons),
            measured={
                "range_min": shaping_range_min_us,
                "range_max": shaping_range_max_us,
                "selected": selected_shaping_us,
            },
            limit=f"range [{min_cap:.0f}, {max_cap:.0f}] us, hpge setting >= {hpge_min:.0f} us",
            confirmed=True,
        )

    return RuleResult(
        rule_id="R-INST-03",
        verdict=Verdict.PASS,
        message_ar="زمن تشكيل المضخم ونطاق التشغيل مطابقان للمواصفات",
        measured={
            "range_min": shaping_range_min_us,
            "range_max": shaping_range_max_us,
            "selected": selected_shaping_us,
        },
        limit=f"range [{min_cap:.0f}, {max_cap:.0f}] us, hpge setting >= {hpge_min:.0f} us",
        confirmed=True,
    )


def eval_r_src_01_source_activity(
    activity_bq: float,
    thresholds: Optional[SystemThresholds] = None,
) -> RuleResult:
    """R-SRC-01: فعالية مصدر المعايرة مقارنة بالقيمة الموصى بها (37000 Bq ± 50%).
    
    الشرط: الفعالية المحسوبة تبتعد كثيراً عن القيمة الموصى بها (خارج نطاق 18500 إلى 55500 Bq).
    الحكم: WARNING
    المرجع: IAEA MRNI-507 ص 7 البند 3.3 ([TBC]).
    """
    t = thresholds or get_default_thresholds()
    rec_act = t.source_recommended_activity_bq.value
    tol_pct = t.source_activity_tolerance_pct.value

    min_act = rec_act * (1.0 - tol_pct / 100.0)
    max_act = rec_act * (1.0 + tol_pct / 100.0)

    if activity_bq < min_act or activity_bq > max_act:
        return RuleResult(
            rule_id="R-SRC-01",
            verdict=Verdict.WARNING,
            message_ar=f"فعالية مصدر المعايرة ({activity_bq:.0f} Bq) خارج النطاق الموصى به ({min_act:.0f} - {max_act:.0f} Bq؛ 37000 Bq ± {tol_pct:.0f}%)؛ قد تؤثر على زمن الموت أو الشك الإحصائي",
            measured=activity_bq,
            limit=f"[{min_act:.0f}, {max_act:.0f}] Bq",
            confirmed=False,
        )

    return RuleResult(
        rule_id="R-SRC-01",
        verdict=Verdict.PASS,
        message_ar=f"فعالية مصدر المعايرة ممتازة وضمن النطاق الموصى به ({activity_bq:.0f} Bq)",
        measured=activity_bq,
        limit=f"[{min_act:.0f}, {max_act:.0f}] Bq",
        confirmed=False,
    )


# ---------------------------------------------------------------------------
# قواعد الأمان وفصل المهام (Security & Governance Rules)
# ---------------------------------------------------------------------------

def eval_rule_sec_01_separation_of_duties(
    technician_id: int,
    approver_id: int,
) -> RuleResult:
    """RULE-SEC-01: منع الاعتماد الذاتي للفحص.
    
    الشرط: technician_id == approver_id
    الحكم: BLOCK
    المرجع: IAEA MRNI-507 Annex I وقرار فصل المهام (CONFIRMED).
    """
    if technician_id == approver_id:
        return RuleResult(
            rule_id="RULE-SEC-01",
            verdict=Verdict.BLOCK,
            message_ar="حظر الاعتماد الذاتي: لا يجوز للمستخدم الذي أجرى الفحص الفني أن يقوم باعتماده",
            measured={"technician_id": technician_id, "approver_id": approver_id},
            limit="technician_id != approver_id",
            confirmed=True,
        )
    return RuleResult(
        rule_id="RULE-SEC-01",
        verdict=Verdict.PASS,
        message_ar="مبدأ فصل المهام بين القائم بالفحص والمعتمد محقق بنجاح",
        measured={"technician_id": technician_id, "approver_id": approver_id},
        limit="technician_id != approver_id",
        confirmed=True,
    )


def eval_rule_sec_02_admin_approval_prohibition(
    approver_role: str,
) -> RuleResult:
    """RULE-SEC-02: حظر اعتماد مدير النظام التقني.
    
    الشرط: دور المعتمد ADMIN فقط دون أن يكون LAB_MANAGER.
    الحكم: BLOCK
    المرجع: ميثاق الأدوار والصلاحيات (CONFIRMED).
    """
    if str(approver_role).upper() == "ADMIN":
        return RuleResult(
            rule_id="RULE-SEC-02",
            verdict=Verdict.BLOCK,
            message_ar="حظر اعتماد مدير النظام: لا يملك مدير النظام التقني صلاحية الاعتماد الفني للفحوصات المعملية",
            measured=approver_role,
            limit="approver_role in ('LAB_MANAGER',)",
            confirmed=True,
        )
    return RuleResult(
        rule_id="RULE-SEC-02",
        verdict=Verdict.PASS,
        message_ar="دور المعتمد مصرح له بالاعتماد الفني",
        measured=approver_role,
        limit="approver_role in ('LAB_MANAGER',)",
        confirmed=True,
    )


def eval_rule_sec_03_immutable_approved_record(
    current_status: str,
    is_modification_attempt: bool = True,
) -> RuleResult:
    """RULE-SEC-03: تجميد السجل المعتمد.
    
    الشرط: محاولة تعديل أو حذف فحص حالته APPROVED.
    الحكم: BLOCK
    المرجع: القرار المعماري ADR-0002 و IAEA MRNI-507 (CONFIRMED).
    """
    if str(current_status).upper() == "APPROVED" and is_modification_attempt:
        return RuleResult(
            rule_id="RULE-SEC-03",
            verdict=Verdict.BLOCK,
            message_ar="تجميد السجل المعتمد: بمجرد اعتماد الفحص يُقفل السجل نهائياً ويُمنع أي تعديل أو حذف",
            measured={"status": current_status, "is_modification": is_modification_attempt},
            limit="status != APPROVED or not modification",
            confirmed=True,
        )
    return RuleResult(
        rule_id="RULE-SEC-03",
        verdict=Verdict.PASS,
        message_ar="السجل قابل للتعديل (مسودة أو قيد المراجعة)",
        measured={"status": current_status, "is_modification": is_modification_attempt},
        limit="status != APPROVED or not modification",
        confirmed=True,
    )


# ---------------------------------------------------------------------------
# قاعدة التجميع الصارمة (Verdict Aggregation)
# ---------------------------------------------------------------------------

def aggregate_verdicts(results: List[RuleResult]) -> Verdict:
    """قاعدة التجميع الصارمة: أسوأ حكم يحكم.
    
    التسلسل الهرمي للشدة:
    1. FAILURE: أي عطل حرج يؤدي لرسوب الفحص وإيقافه فوراً.
    2. BLOCK: أي حظر أمان يمنع الانتقال للخطوة التالية.
    3. WAIT: تبريد غير كافٍ أو إحصاء غير كافٍ يتطلب الانتظار.
    4. OUT_OF_SPEC: خروج عن المواصفات يتطلب إجراءً تصحيحياً أو قبولاً مشروطاً.
    5. WARNING: تحذير تشغيلي (مثل معايرة منتهية أو فعالية بعيدة).
    6. PASS: جميع القواعد مطابقة.
    """
    if not results:
        return Verdict.PASS

    verdicts = {r.verdict for r in results}

    if Verdict.FAILURE in verdicts:
        return Verdict.FAILURE
    if Verdict.BLOCK in verdicts:
        return Verdict.BLOCK
    if Verdict.WAIT in verdicts:
        return Verdict.WAIT
    if Verdict.OUT_OF_SPEC in verdicts:
        return Verdict.OUT_OF_SPEC
    if Verdict.WARNING in verdicts:
        return Verdict.WARNING
    return Verdict.PASS
