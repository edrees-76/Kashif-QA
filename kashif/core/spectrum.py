"""معايرة الطاقة الخطية وتحليل القمم الطيفية لمنظومة كاشف (Spectrum Analysis).

Energy calibration and channel-energy conversion according to IAEA MRNI-507 / MRNI-504.
Strict consistency validation (positive slope, distinct peaks, valid channels).
Pure functions without GUI or external dependencies.
"""

from __future__ import annotations

from typing import Tuple

# الثوابت الطاقية المعتمدة لنظائر المعايرة (keV)
CO60_ENERGY_1_KEV: float = 1173.2
"""طاقة القمة الأولى للكوبالت-60 بوحدة keV (IAEA MRNI-507 ص 14)."""

CO60_ENERGY_2_KEV: float = 1332.5
"""طاقة القمة الثانية للكوبالت-60 بوحدة keV (IAEA MRNI-507 ص 14)."""

CS137_ENERGY_KEV: float = 661.5
"""طاقة قمة السيزيوم-137 بوحدة keV (IAEA MRNI-504 ص 7)."""


def linear_energy_calibration(
    ch1: float,
    energy1: float,
    ch2: float,
    energy2: float,
) -> Tuple[float, float]:
    """حساب معاملي معادلة المعايرة الخطية (y = mx + b) من قمتين طيفيتين.
    
    المعادلة:
        y = m * x + b
        حيث:
        y = الطاقة بوحدة keV
        x = رقم القناة
        m = الطاقة لكل قناة (keV/channel)
        b = الطاقة عند القناة صفر (Intercept)
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.7.4, Page 15:
        "obtain the Energy calibration equation for the Spectrometry System:
         y = mx + b
         where: y = energy value, m = energy per channel, x = channel value, b = energy at channel 0"
    
    المعاملات:
        ch1: موقع القمة الأولى بالقنوات (>= 0).
        energy1: الطاقة النظرية للقمة الأولى بوحدة keV (> 0).
        ch2: موقع القمة الثانية بالقنوات (>= 0).
        energy2: الطاقة النظرية للقمة الثانية بوحدة keV (> 0).
    
    العائد:
        زوج مرتب (m, b) يمثل (الميل، نقطة التقاطع).
    
    استثناءات:
        ValueError: في حال تكرار القنوات أو الطاقات، أو كان الميل سالباً، أو كانت القيم غير مقبولة فيزيائياً.
    """
    if ch1 < 0 or ch2 < 0:
        raise ValueError("أرقام القنوات لا يجوز أن تكون سالبة")
    if energy1 <= 0 or energy2 <= 0:
        raise ValueError("طاقات القمم يجب أن تكون موجبة تماماً")
    if ch1 == ch2:
        raise ValueError("يجب أن تكون القناتان مختلفتين لحساب المعايرة الخطية (تجنب القسمة على صفر)")
    if energy1 == energy2:
        raise ValueError("يجب أن تكون طاقتا القمتين مختلفتين لحساب المعايرة")

    slope_m = (energy2 - energy1) / (ch2 - ch1)
    if slope_m <= 0:
        raise ValueError(
            f"ميل المعايرة الطاقية ({slope_m:.6f}) يجب أن يكون موجباً تماماً "
            "(ازدياد رقم القناة يقتضي حتماً ازدياد الطاقة الإشعاعية المقابلة)"
        )

    intercept_b = energy1 - (slope_m * ch1)
    return float(slope_m), float(intercept_b)


def calibrate_co60(ch_1173: float, ch_1332: float) -> Tuple[float, float]:
    """حساب المعايرة الخطية لكاشف الجرمانيوم باستخدام قمتي الكوبالت-60 (1173.2 و 1332.5 keV).
    
    المرجع:
        IAEA Procedure Nº: MRNI-507 (Rev. D0), Section 3.7.2 - 3.7.4, Page 14-15.
    
    المعاملات:
        ch_1173: موقع قمة 1173.2 keV بالقنوات.
        ch_1332: موقع قمة 1332.5 keV بالقنوات (يجب أن يكون > ch_1173).
    
    العائد:
        (m, b) معاملي معادلة المعايرة.
    """
    if ch_1332 <= ch_1173:
        raise ValueError("قناة القمة الثانية (1332.5 keV) يجب أن تكون أعلى من قناة القمة الأولى (1173.2 keV)")
    
    return linear_energy_calibration(
        ch1=ch_1173,
        energy1=CO60_ENERGY_1_KEV,
        ch2=ch_1332,
        energy2=CO60_ENERGY_2_KEV,
    )


def calibrate_nai_single_peak(
    ch_661: float,
    intercept_b: float = 0.0,
) -> Tuple[float, float]:
    """حساب المعايرة الخطية لكاشف يوديد الصوديوم NaI(Tl) من قمة السيزيوم-137 (661.5 keV).
    
    المرجع:
        IAEA Procedure Nº: MRNI-504 (Rev. D0), Section 3.5.3, Page 7-8.
    
    المعاملات:
        ch_661: موقع قمة السيزيوم 661.5 keV بالقنوات (> 0).
        intercept_b: نقطة التقاطع عند القناة صفر (الافتراضي 0.0 keV).
    
    العائد:
        (m, b) حيث m = (661.5 - b) / ch_661.
    """
    if ch_661 <= 0:
        raise ValueError("رقم قناة قمة السيزيوم يجب أن يكون أكبر من الصفر")
    if intercept_b >= CS137_ENERGY_KEV:
        raise ValueError("نقطة التقاطع b يجب أن تكون أقل من طاقة قمة السيزيوم (661.5 keV)")

    slope_m = (CS137_ENERGY_KEV - intercept_b) / ch_661
    return float(slope_m), float(intercept_b)


def channel_to_energy(channel: float, slope_m: float, intercept_b: float) -> float:
    """تحويل رقم القناة إلى الطاقة المكافئة بوحدة keV.
    
    المعادلة:
        E = m * channel + b
    
    المعاملات:
        channel: رقم القناة (>= 0).
        slope_m: ميل المعايرة الطاقية (> 0).
        intercept_b: نقطة التقاطع عند القناة صفر.
    """
    if channel < 0:
        raise ValueError("رقم القناة لا يجوز أن يكون سالباً")
    if slope_m <= 0:
        raise ValueError("ميل المعايرة يجب أن يكون موجباً تماماً")
    
    return float((slope_m * channel) + intercept_b)


def energy_to_channel(energy: float, slope_m: float, intercept_b: float) -> float:
    """تحويل الطاقة بوحدة keV إلى رقم القناة المكافئة.
    
    المعادلة:
        channel = (E - b) / m
    
    المعاملات:
        energy: الطاقة بوحدة keV (> 0).
        slope_m: ميل المعايرة الطاقية (> 0).
        intercept_b: نقطة التقاطع عند القناة صفر.
    """
    if energy <= 0:
        raise ValueError("الطاقة يجب أن تكون قيمة موجبة تماماً")
    if slope_m <= 0:
        raise ValueError("ميل المعايرة يجب أن يكون موجباً تماماً")
    
    ch = (energy - intercept_b) / slope_m
    if ch < 0:
        raise ValueError(
            f"الطاقة المحددة ({energy:.2f} keV) تقع دون عتبة القناة صفر "
            f"مع المعاملات الحالية (القناة الناتجة {ch:.2f} < 0)"
        )
    return float(ch)
