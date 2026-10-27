"""كائن إعدادات العتبات الرقمية لمنظومة كاشف (System Thresholds).

Immutable configuration object carrying all technical thresholds,
their default proposed values, confirmation status (CONFIRMED / [TBC]),
and IAEA / industry standard citations.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class ThresholdEntry(BaseModel):
    """عنصر عتبة فنية موثق بالمصدر وحالة التأكيد."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(..., description="القيمة العددية للعتبة")
    confirmed: bool = Field(..., description="هل العتبة مؤكدة بنص صريح أم [TBC]")
    source: str = Field(..., description="مرجع العتبة في وثيقة IAEA أو المواصفة القياسية")
    description_ar: str = Field(default="", description="وصف العتبة باللغة العربية")


class SystemThresholds(BaseModel):
    """كائن إعدادات العتبات الفنية غير القابل للتعديل بعد الإنشاء (Frozen)."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    # R-HPGE-03
    hpge_cooling_min_hours: ThresholdEntry = ThresholdEntry(
        value=6.0,
        confirmed=True,
        source="IAEA MRNI-507 (Rev. D0) p. 7 Section 3.4.3",
        description_ar="الحد الأدنى لزمن تبريد كاشف الجرمانيوم بالنيتروجين السائل (ساعات)",
    )

    # R-HPGE-05
    hpge_nim_voltage_tolerance_pct: ThresholdEntry = ThresholdEntry(
        value=5.0,
        confirmed=False,
        source="IAEA MRNI-507 p. 9 Section 3.5.1 [TBC: يحتاج تأكيد المهندس]",
        description_ar="سماحية جهود سلة NIM المسموحة (نسبة مئوية)",
    )

    # R-HPGE-07
    hpge_hv_ramp_max_rate_v_per_s: ThresholdEntry = ThresholdEntry(
        value=10.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 11 Section 3.5.14",
        description_ar="الحد الأقصى لمعدل رفع الجهد العالي (فولت/ثانية؛ 100V كل 10 ثوانٍ)",
    )

    # R-HPGE-10
    hpge_mca_max_total_cps: ThresholdEntry = ThresholdEntry(
        value=2000.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 14 Section 3.7.2",
        description_ar="الحد الأقصى لمعدل العد الإجمالي في MCA (نبضة/ثانية)",
    )
    hpge_mca_max_dead_time_pct: ThresholdEntry = ThresholdEntry(
        value=5.0,
        confirmed=False,
        source="IAEA MRNI-507 p. 14 [TBC: يحتاج تأكيد المهندس]",
        description_ar="الحد الأقصى المسموح به لنسبة زمن الموت (%)",
    )

    # R-HPGE-11
    hpge_peak_min_net_counts: ThresholdEntry = ThresholdEntry(
        value=10000.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 14 Section 3.7.3",
        description_ar="الحد الأدنى لصافي نبضات القمة لتحقيق ارتياب نسبي أقل من 1%",
    )

    # R-HPGE-12
    hpge_fwhm_nominal_pass_ratio: ThresholdEntry = ThresholdEntry(
        value=1.10,
        confirmed=False,
        source="IAEA MRNI-507 p. 15 Section 3.7.7 [TBC: يحتاج تأكيد المهندس]",
        description_ar="نسبة FWHM المقاس إلى الاسمي للنجاح PASS (110%)",
    )
    hpge_fwhm_nominal_out_of_spec_ratio: ThresholdEntry = ThresholdEntry(
        value=1.25,
        confirmed=False,
        source="IAEA MRNI-507 p. 15 Section 3.7.7 [TBC: يحتاج تأكيد المهندس]",
        description_ar="نسبة FWHM المقاس إلى الاسمي للخروج عن المواصفات OUT_OF_SPEC (125%)",
    )
    hpge_fwhm_fallback_pass_kev: ThresholdEntry = ThresholdEntry(
        value=1.9,
        confirmed=False,
        source="IAEA MRNI-507 p. 15 Section 3.7.7 [TBC: يحتاج تأكيد المهندس]",
        description_ar="عتبة FWHM للنجاح عند غياب القيمة الاسمية (keV)",
    )
    hpge_fwhm_fallback_out_of_spec_kev: ThresholdEntry = ThresholdEntry(
        value=2.4,
        confirmed=False,
        source="IAEA MRNI-507 p. 15 Section 3.7.7 [TBC: يحتاج تأكيد المهندس]",
        description_ar="عتبة FWHM للخروج عن المواصفات عند غياب القيمة الاسمية (keV)",
    )

    # R-HPGE-13 / 14
    hpge_form_factor_max_ratio: ThresholdEntry = ThresholdEntry(
        value=1.90,
        confirmed=True,
        source="IAEA MRNI-507 p. 15 Section 3.7.8",
        description_ar="الحد الأقصى لمعامل شكل النبضة FWTM/FWHM للنجاح",
    )

    # R-HPGE-15
    hpge_efficiency_pass_ratio: ThresholdEntry = ThresholdEntry(
        value=0.95,
        confirmed=False,
        source="IAEA MRNI-507 p. 16 Section 3.8 [TBC: يحتاج تأكيد المهندس]",
        description_ar="النسبة الدنيا للكفاءة المقاسة مقارنة بالاسمية للنجاح PASS (95%)",
    )
    hpge_efficiency_out_of_spec_ratio: ThresholdEntry = ThresholdEntry(
        value=0.80,
        confirmed=False,
        source="IAEA MRNI-507 p. 16 Section 3.8 [TBC: يحتاج تأكيد المهندس]",
        description_ar="النسبة الدنيا للكفاءة المقاسة مقارنة بالاسمية لتفادي العطل (80%)",
    )

    # R-NAI-01
    nai_pmt_voltage_tolerance_pct: ThresholdEntry = ThresholdEntry(
        value=2.0,
        confirmed=False,
        source="IAEA MRNI-504 Section 3.2.1 [TBC: يحتاج تأكيد المهندس]",
        description_ar="سماحية تذبذب جهد تغذية PMT المسموحة (%)",
    )

    # R-NAI-02
    nai_resolution_pass_pct: ThresholdEntry = ThresholdEntry(
        value=7.5,
        confirmed=False,
        source="معيار الصناعة القياسي لكواشف الوميض [TBC: يحتاج تأكيد المهندس]",
        description_ar="الحد الأقصى للدقة الطاقية %R لنجاح كاشف NaI 3x3",
    )
    nai_resolution_out_of_spec_pct: ThresholdEntry = ThresholdEntry(
        value=9.0,
        confirmed=False,
        source="معيار الصناعة القياسي لكواشف الوميض [TBC: يحتاج تأكيد المهندس]",
        description_ar="الحد الأقصى للدقة الطاقية %R لخروج كاشف NaI 3x3 عن المواصفات",
    )

    # R-NAI-03
    nai_gain_drift_max_channels: ThresholdEntry = ThresholdEntry(
        value=2.0,
        confirmed=False,
        source="معايير استقرار مطيافية NaI [TBC: يحتاج تأكيد المهندس]",
        description_ar="الحد الأقصى لانجراف قمة السيزيوم (قنوات)",
    )
    nai_gain_drift_time_hours: ThresholdEntry = ThresholdEntry(
        value=2.0,
        confirmed=False,
        source="معايير استقرار مطيافية NaI [TBC: يحتاج تأكيد المهندس]",
        description_ar="المدة الزمنية المعيارية لمراقبة انجراف القمة (ساعات)",
    )

    # R-INST-02
    inst_hv_max_ripple_mv: ThresholdEntry = ThresholdEntry(
        value=100.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 6 Section 3.2.2",
        description_ar="الحد الأقصى لتموج مجهز الجهد العالي (mV)",
    )
    inst_hv_max_regulation_pct: ThresholdEntry = ThresholdEntry(
        value=0.01,
        confirmed=True,
        source="IAEA MRNI-507 p. 6 Section 3.2.2",
        description_ar="الحد الأقصى لتنظيم جهد مجهز الجهد العالي (%)",
    )

    # R-INST-03
    inst_amp_shaping_min_us: ThresholdEntry = ThresholdEntry(
        value=3.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 6 Section 3.2.3",
        description_ar="الحد الأدنى لنطاق زمن تشكيل المضخم الطيفي (ميكروثانية)",
    )
    inst_amp_shaping_max_us: ThresholdEntry = ThresholdEntry(
        value=12.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 6 Section 3.2.3",
        description_ar="الحد الأقصى لنطاق زمن تشكيل المضخم الطيفي (ميكروثانية)",
    )
    inst_amp_hpge_shaping_min_us: ThresholdEntry = ThresholdEntry(
        value=4.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 12 Section 3.6.2",
        description_ar="الحد الأدنى لزمن تشكيل المضخم عند فحص الجرمانيوم (ميكروثانية)",
    )

    # R-SRC-01
    source_activity_tolerance_pct: ThresholdEntry = ThresholdEntry(
        value=50.0,
        confirmed=False,
        source="IAEA MRNI-507 p. 7 Section 3.3 [TBC: يحتاج تأكيد المهندس]",
        description_ar="سماحية ابتعاد فعالية مصدر المعايرة عن القيمة الموصى بها (%)",
    )
    source_recommended_activity_bq: ThresholdEntry = ThresholdEntry(
        value=37000.0,
        confirmed=True,
        source="IAEA MRNI-507 p. 7 Section 3.3",
        description_ar="الفعالية الاسمية الموصى بها لمصدر المعايرة (Bq)",
    )

    def to_dict(self) -> Dict[str, Any]:
        """تحويل كائن العتبات إلى قاموس قابل للتسلسل."""
        return self.model_dump(mode="json")

    def to_json(self) -> str:
        """تحويل كائن العتبات إلى سلسلة JSON حتمية."""
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# دوال التحميل والمصنع
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLDS = SystemThresholds()


def get_default_thresholds() -> SystemThresholds:
    """الحصول على كائن العتبات الافتراضية المعتمدة."""
    return _DEFAULT_THRESHOLDS


def load_thresholds_from_dict(data: Dict[str, Any]) -> SystemThresholds:
    """تحميل كائن العتبات من قاموس.
    
    يدعم النمطين:
    1. قاموس مهيكل كامل يحتوي على كائنات ThresholdEntry.
    2. قاموس قيم عددية مبسطة لتعديل القيم الافتراضية بسهولة.
    """
    if not data:
        return get_default_thresholds()

    defaults = get_default_thresholds().model_dump()
    new_data: Dict[str, Any] = {}

    for k, default_entry in defaults.items():
        if k in data:
            incoming = data[k]
            if isinstance(incoming, dict):
                # دمج الحقول مع الحفاظ على الافتراضيات
                entry = dict(default_entry)
                entry.update(incoming)
                new_data[k] = entry
            elif isinstance(incoming, (int, float)):
                # تعديل القيمة فقط مع الحفاظ على المصدر والتأكيد
                entry = dict(default_entry)
                entry["value"] = float(incoming)
                new_data[k] = entry
            else:
                new_data[k] = default_entry
        else:
            new_data[k] = default_entry

    return SystemThresholds(**new_data)


def load_thresholds_from_json(json_str: str) -> SystemThresholds:
    """تحميل كائن العتبات من نص JSON."""
    data = json.loads(json_str)
    if not isinstance(data, dict):
        raise ValueError("بيانات JSON يجب أن تكون كائناً (قاموساً)")
    return load_thresholds_from_dict(data)
