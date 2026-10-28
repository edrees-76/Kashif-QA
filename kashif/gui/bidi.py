"""Bidirectional (BiDi) isolation utilities for KASHIF-QA.

Prevents line-wrapping and punctuation distortion when Latin scientific terms,
nuclear units (keV, Bq, Ci, etc.), and codes are embedded within Arabic text.
"""

from typing import Any, Dict, Optional
import re

# Unicode Bidirectional Formatting Characters
LRI = "\u2066"  # Left-to-Right Isolate
RLI = "\u2067"  # Right-to-Left Isolate
PDI = "\u2069"  # Pop Directional Isolate
LRM = "\u200E"  # Left-to-Right Mark


def isolate_ltr(text: str) -> str:
    """Wrap Latin text, numbers, or units in Left-to-Right isolate characters."""
    if not text:
        return ""
    return f"{LRI}{text}{PDI}"


def isolate_rtl(text: str) -> str:
    """Wrap Arabic text in Right-to-Left isolate characters."""
    if not text:
        return ""
    return f"{RLI}{text}{PDI}"


# Canonical Arabic translation dictionary for entity lifecycle and inspection statuses
STATUS_LABELS_AR: Dict[str, str] = {
    "ACTIVE": "نشط",
    "RETIRED": "متقاعد",
    "MAINTENANCE": "صيانة",
    "OPERATIONAL": "تشغيلي",
    "IN_SERVICE": "في الخدمة",
    "OUT_OF_SERVICE": "خارج الخدمة",
    "INACTIVE": "غير نشط",
    "SUSPENDED": "موقوف",
    "INTACT": "سليمة",
    "SCRATCHED": "مخدوشة",
    "BROKEN": "مكسورة",
}


def format_status_ar(status: Optional[str]) -> str:
    """Format entity status string in Arabic using the standard status dictionary.
    
    Preserves database representation unchanged while presenting clear Arabic labels in UI.
    """
    if not status:
        return "—"
    s_clean = str(status).strip()
    return STATUS_LABELS_AR.get(s_clean.upper(), s_clean)


def isolate_quantity(value: Any, unit: str = "") -> str:
    """Format and isolate a numerical quantity and its unit together in LTR.
    
    Prevents number and unit inversion, transposition, or scrambling in RTL contexts.
    Examples:
        isolate_quantity("32.432", "kBq") -> "\u206632.432 kBq\u2069"
        isolate_quantity("32.432 kBq") -> "\u206632.432 kBq\u2069"
        isolate_quantity(3000, "V") -> "\u20663000 V\u2069"
        isolate_quantity("37 kBq ± 50%") -> "\u206637 kBq ± 50%\u2069"
    """
    if value is None or value == "" or value == "—":
        return "—"
    val_str = str(value).strip()
    if unit:
        text = f"{val_str} {unit.strip()}"
    else:
        text = val_str
    return isolate_ltr(text)


# Regex matching common scientific terms, models, nuclides, and units
_LATIN_PATTERN = re.compile(
    r"\b([A-Za-z0-9_\-\.\(\)/]+(?:\s*[A-Za-z0-9_\-\.\(\)/]+)*)\b"
)


def format_mixed_text(text: str) -> str:
    """Automatically isolate recognized scientific acronyms and Latin terms in Arabic strings."""
    if not text:
        return ""
    
    # Specific known terms to isolate safely
    terms_to_isolate = [
        "HPGe", "NaI", "NaI(Tl)", "Co-60", "Cs-137", "FWHM", "FWTM", "MCA", "NIM",
        "TRP", "keV", "MeV", "Bq", "kBq", "MBq", "GBq", "TBq", "Ci", "μCi", "mCi",
        "MRNI-507", "MRNI-504", "MRNI-500", "IAEA-TECDOC-1599", "IAEA",
        "KASHIF-QA", "Argon2id", "SHA-256", "SQLite", "WAL", "PyQt6",
        "R-SRC-01", "R-DET-01", "R-INST-01", "R-MCA-01", "mV", "µs", "V/s",
    ]
    
    formatted = text
    for term in sorted(terms_to_isolate, key=len, reverse=True):
        pattern = re.compile(r"(?<!\u2066)\b" + re.escape(term) + r"\b(?!\u2069)")
        formatted = pattern.sub(f"{LRI}{term}{PDI}", formatted)
        
    return formatted


def isolate_unit_bidi(name_ar: str, symbol: str) -> str:
    """Format Arabic unit name with its Latin symbol isolated in LTR parentheses.
    
    Prevents parenthesis inversion and displacement in RTL combo boxes.
    Example: 'بيكرل (Bq)', 'Bq' -> 'بيكرل \u2066(Bq)\u2069'
    """
    base_ar = name_ar.split("(")[0].strip()
    return f"{base_ar} {isolate_ltr(f'({symbol})')}"
