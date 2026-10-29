"""حاسبة الاضمحلال الإشعاعي والتحويل الموحد للوحدات النووية.

Radioactive decay calculator and unit conversions for calibration sources.
Supports Co-60 and Cs-137 according to IAEA and nuclear data standards.
Pure functions with strict input validation.
"""

from __future__ import annotations

from datetime import date
import math
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# الثوابت النووية المعتمدة
# ---------------------------------------------------------------------------

DAYS_PER_YEAR: float = 365.25
"""عدد الأيام في السنة المعتمد في الحسابات النووية (سنة يوليانية).
حالة التأكيد: [TBC: يحتاج تأكيد المهندس - اقتراح: 365.25 يوم/سنة كما في tasks.md TASK-1.3]
"""

SECONDS_PER_DAY: float = 86400.0
MINUTES_PER_DAY: float = 1440.0
HOURS_PER_DAY: float = 24.0

HALF_LIFE_CO60_DAYS: float = 1925.269275
"""نصف عمر الكوبالت-60 بالأيام (5.2711 سنة * 365.25 يوم/سنة = 1925.269275 يوماً).
المصدر: LNHB/DDEP - Table de Radionucléides (Co-60) / عدم اليقين: 0.0008 سنة.
رابط الوثيقة: https://www.lnhb.fr/nuclides/Co-60_tables.pdf
حالة التأكيد: CONFIRMED
"""

HALF_LIFE_CS137_DAYS: float = 10964.0745
"""نصف عمر السيزيوم-137 بالأيام (30.018 سنة * 365.25 يوم/سنة = 10964.0745 يوماً).
المصدر: LNHB/DDEP - Table de Radionucléides (Cs-137) / عدم اليقين: 0.022 سنة.
رابط الوثيقة: https://www.lnhb.fr/nuclides/Cs-137_tables.pdf
حالة التأكيد: CONFIRMED
"""

BQ_PER_UCI: float = 37000.0
"""معامل التحويل الدقيق من ميكروكوري إلى بيكرل (1 uCi = 3.7e4 Bq).
المصدر: النظام الدولي للوحدات (SI) وتعريف الكوري القياسي المعتمد في BIPM وNIST.
حالة التأكيد: CONFIRMED
"""

BQ_PER_MCI: float = 37000000.0
"""معامل التحويل من ميللي كوري إلى بيكرل (1 mCi = 3.7e7 Bq).
حالة التأكيد: CONFIRMED
"""

BQ_PER_CI: float = 37000000000.0
"""معامل التحويل من كوري إلى بيكرل (1 Ci = 3.7e10 Bq).
حالة التأكيد: CONFIRMED
"""

BQ_PER_TBQ: float = 1000000000000.0
"""معامل التحويل من تيرا بيكرل إلى بيكرل (1 TBq = 1e12 Bq).
حالة التأكيد: CONFIRMED
"""

# قائمة تهيئة وحدات النشاط الإشعاعي (الرمز، الاسم العربي، المعامل إلى Bq)
ACTIVITY_UNITS: Dict[str, Dict[str, Any]] = {
    "Bq": {"symbol": "Bq", "name_ar": "بيكرل (Bq)", "factor": 1.0},
    "kBq": {"symbol": "kBq", "name_ar": "كيلو بيكرل (kBq)", "factor": 1000.0},
    "MBq": {"symbol": "MBq", "name_ar": "ميغا بيكرل (MBq)", "factor": 1000000.0},
    "GBq": {"symbol": "GBq", "name_ar": "غيغا بيكرل (GBq)", "factor": 1000000000.0},
    "TBq": {"symbol": "TBq", "name_ar": "تيرا بيكرل (TBq)", "factor": 1000000000000.0},
    "uCi": {"symbol": "µCi", "name_ar": "ميكروكوري (µCi)", "factor": 37000.0},
    "mCi": {"symbol": "mCi", "name_ar": "ميللي كوري (mCi)", "factor": 37000000.0},
    "Ci": {"symbol": "Ci", "name_ar": "كوري (Ci)", "factor": 37000000000.0},
}

# قائمة تهيئة وحدات عمر النصف (ثانية، دقيقة، ساعة، يوم، سنة)
HALF_LIFE_UNITS: Dict[str, Dict[str, Any]] = {
    "s": {"symbol": "s", "name_ar": "ثانية", "days_factor": 1.0 / 86400.0},
    "m": {"symbol": "m", "name_ar": "دقيقة", "days_factor": 1.0 / 1440.0},
    "h": {"symbol": "h", "name_ar": "ساعة", "days_factor": 1.0 / 24.0},
    "d": {"symbol": "d", "name_ar": "يوم", "days_factor": 1.0},
    "y": {"symbol": "y", "name_ar": "سنة", "days_factor": 365.25},
}


# ---------------------------------------------------------------------------
# دوال حساب الاضمحلال الإشعاعي
# ---------------------------------------------------------------------------

def decay_activity(activity_0: float, elapsed_days: float, half_life_days: float) -> float:
    """حساب الفعالية الإشعاعية اللحظية A(t) بناءً على قانون الاضمحلال الأسي.
    
    المعادلة:
        A(t) = A_0 * exp(-lambda * t) = A_0 * (0.5)^(t / T_1/2)
        حيث:
        lambda = ln(2) / T_1/2
    
    المعاملات:
        activity_0: الفعالية الابتدائية (A_0) عند تاريخ المعايرة (>= 0).
        elapsed_days: الزمن المنقضي بالأيام (t >= 0).
        half_life_days: عمر النصف للنظير بالأيام (T_1/2 > 0).
    
    العائد:
        الفعالية الإشعاعية اللحظية بنفس وحدة الفعالية الابتدائية.
    
    استثناءات:
        ValueError: في حال تمرير زمن سالب، فعالية سالبة، أو عمر نصف غير موجب.
    """
    if activity_0 < 0:
        raise ValueError("الفعالية الإشعاعية الابتدائية لا يجوز أن تكون سالبة")
    if elapsed_days < 0:
        raise ValueError("الزمن المنقضي لا يجوز أن يكون سالباً")
    if half_life_days <= 0:
        raise ValueError("عمر النصف يجب أن يكون قيمة موجبة تماماً")
    
    if activity_0 == 0.0:
        return 0.0

    decay_constant = math.log(2.0) / half_life_days
    return float(activity_0 * math.exp(-decay_constant * elapsed_days))


def decay_activity_co60(activity_0: float, elapsed_days: float) -> float:
    """حساب الفعالية اللحظية لمصدر الكوبالت-60 (Co-60).
    
    المعاملات:
        activity_0: الفعالية الابتدائية (>= 0).
        elapsed_days: الزمن المنقضي بالأيام (>= 0).
    """
    return decay_activity(activity_0, elapsed_days, HALF_LIFE_CO60_DAYS)


def decay_activity_cs137(activity_0: float, elapsed_days: float) -> float:
    """حساب الفعالية اللحظية لمصدر السيزيوم-137 (Cs-137).
    
    المعاملات:
        activity_0: الفعالية الابتدائية (>= 0).
        elapsed_days: الزمن المنقضي بالأيام (>= 0).
    """
    return decay_activity(activity_0, elapsed_days, HALF_LIFE_CS137_DAYS)


def decay_activity_between_dates(
    activity_0: float,
    cert_date: date,
    target_date: date,
    half_life_days: float,
) -> float:
    """حساب الفعالية اللحظية بين تاريخين محددين (تاريخ الشهادة وتاريخ الفحص).
    
    المعاملات:
        activity_0: الفعالية الابتدائية المدونة بالشهادة (>= 0).
        cert_date: تاريخ شهادة المعايرة.
        target_date: تاريخ القياس الحالي (يجب أن يكون >= cert_date).
        half_life_days: عمر النصف للنظير بالأيام (> 0).
    """
    if target_date < cert_date:
        raise ValueError("تاريخ القياس لا يجوز أن يسبق تاريخ شهادة المعايرة (زمن منقضٍ سالب)")
    
    delta_days = float((target_date - cert_date).days)
    return decay_activity(activity_0, delta_days, half_life_days)


# ---------------------------------------------------------------------------
# دوال تحويل وحدات النشاط الإشعاعي (Curie <-> Becquerel)
# ---------------------------------------------------------------------------

def uci_to_bq(uci: float) -> float:
    """تحويل الفعالية من ميكروكوري إلى بيكرل (1 uCi = 37,000 Bq)."""
    if uci < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(uci * BQ_PER_UCI)


def bq_to_uci(bq: float) -> float:
    """تحويل الفعالية من بيكرل إلى ميكروكوري."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / BQ_PER_UCI)


def mci_to_bq(mci: float) -> float:
    """تحويل الفعالية من ميللي كوري إلى بيكرل (1 mCi = 3.7e7 Bq)."""
    if mci < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(mci * BQ_PER_MCI)


def bq_to_mci(bq: float) -> float:
    """تحويل الفعالية من بيكرل إلى ميللي كوري."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / BQ_PER_MCI)


def ci_to_bq(ci: float) -> float:
    """تحويل الفعالية من كوري إلى بيكرل (1 Ci = 3.7e10 Bq)."""
    if ci < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(ci * BQ_PER_CI)


def bq_to_ci(bq: float) -> float:
    """تحويل الفعالية من بيكرل إلى كوري."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / BQ_PER_CI)


def bq_to_kbq(bq: float) -> float:
    """تحويل من بيكرل إلى كيلو بيكرل (1 kBq = 1,000 Bq)."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / 1000.0)


def kbq_to_bq(kbq: float) -> float:
    """تحويل من كيلو بيكرل إلى بيكرل."""
    if kbq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(kbq * 1000.0)


def bq_to_mbq(bq: float) -> float:
    """تحويل من بيكرل إلى ميغا بيكرل (1 MBq = 1,000,000 Bq)."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / 1000000.0)


def mbq_to_bq(mbq: float) -> float:
    """تحويل من ميغا بيكرل إلى بيكرل."""
    if mbq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(mbq * 1000000.0)


def bq_to_gbq(bq: float) -> float:
    """تحويل من بيكرل إلى غيغا بيكرل (1 GBq = 1e9 Bq)."""
    if bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(bq / 1000000000.0)


def gbq_to_bq(gbq: float) -> float:
    """تحويل من غيغا بيكرل إلى بيكرل."""
    if gbq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة")
    return float(gbq * 1000000000.0)


def bq_to_tbq(bq: float) -> float:
    """تحويل من بيكرل إلى تيرا بيكرل (1 TBq = 1e12 Bq)."""
    if not math.isfinite(bq) or bq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة أو غير منتهية")
    return float(bq / BQ_PER_TBQ)


def tbq_to_bq(tbq: float) -> float:
    """تحويل من تيرا بيكرل إلى بيكرل."""
    if not math.isfinite(tbq) or tbq < 0:
        raise ValueError("الفعالية لا يجوز أن تكون سالبة أو غير منتهية")
    return float(tbq * BQ_PER_TBQ)


def convert_activity_to_bq(value: float, unit: str) -> float:
    """تحويل أي قيمة نشاط إشعاعي بأي وحدة معتمدة إلى بيكرل (Bq)."""
    if not math.isfinite(value) or value < 0:
        raise ValueError("قيمة النشاط الإشعاعي لا يجوز أن تكون سالبة أو غير منتهية")
    u = unit.strip()
    if u in ("µCi", "uCi"):
        factor = BQ_PER_UCI
    elif u in ACTIVITY_UNITS:
        factor = float(ACTIVITY_UNITS[u]["factor"])
    else:
        raise ValueError(f"وحدة النشاط الإشعاعي غير معروفة: {unit}")
    return float(value * factor)


def convert_activity_from_bq(activity_bq: float, target_unit: str) -> float:
    """تحويل النشاط بالبيكرل إلى وحدة مستهدفة."""
    if not math.isfinite(activity_bq) or activity_bq < 0:
        raise ValueError("النشاط الإشعاعي لا يجوز أن يكون سالباً أو غير منتهٍ")
    u = target_unit.strip()
    if u in ("µCi", "uCi"):
        factor = BQ_PER_UCI
    elif u in ACTIVITY_UNITS:
        factor = float(ACTIVITY_UNITS[u]["factor"])
    else:
        raise ValueError(f"وحدة النشاط الإشعاعي غير معروفة: {target_unit}")
    return float(activity_bq / factor)


def format_activity_auto(activity_bq: float) -> str:
    """تنسيق النشاط الإشعاعي بالوحدة المناسبة تلقائياً (Bq, kBq, MBq, GBq, TBq)."""
    if not math.isfinite(activity_bq) or activity_bq < 0:
        raise ValueError("النشاط الإشعاعي غير صالح للتنسيق")
    if activity_bq >= 1e12:
        return f"{activity_bq / 1e12:.3f} TBq"
    if activity_bq >= 1e9:
        return f"{activity_bq / 1e9:.3f} GBq"
    if activity_bq >= 1e6:
        return f"{activity_bq / 1e6:.3f} MBq"
    if activity_bq >= 1e3:
        return f"{activity_bq / 1e3:.3f} kBq"
    return f"{activity_bq:.1f} Bq"


def half_life_to_days(value: float, unit: str) -> float:
    """تحويل عمر النصف من وحدته الأصلية (s, m, h, d, y) إلى أيام.
    
    يرفض القيم الصفرية والسالبة وغير المنتهية (NaN/inf).
    """
    if not math.isfinite(value) or value <= 0:
        raise ValueError("عمر النصف يجب أن يكون قيمة موجبة تماماً ومحدودة (ليس صفراً ولا سالباً ولا NaN/inf)")
    u = unit.strip().lower()
    if u not in HALF_LIFE_UNITS:
        raise ValueError(f"وحدة عمر النصف '{unit}' غير مدعومة. الوحدات المتاحة: s, m, h, d, y")
    return float(value * HALF_LIFE_UNITS[u]["days_factor"])


def format_half_life(value: float, unit: str) -> str:
    """تنسيق عمر النصف مع وحدته الأصلية باللغة العربية مع صيغة علمية للقيم الكبيرة أو الصغيرة جداً."""
    if not math.isfinite(value) or value <= 0:
        raise ValueError("عمر النصف يجب أن يكون قيمة موجبة ومحدودة")
    u = unit.strip().lower()
    if u not in HALF_LIFE_UNITS:
        raise ValueError(f"وحدة عمر النصف '{unit}' غير مدعومة")
    unit_name = HALF_LIFE_UNITS[u]["name_ar"]
    if value >= 1e4 or value < 1e-2:
        return f"{value:.3e} {unit_name}"
    val_str = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{val_str} {unit_name}"

