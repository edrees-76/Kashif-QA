"""PDF and typography utilities for KASHIF-QA reports.

Provides Arabic text reshaping, BiDi reordering, paragraph line splitting,
and font registration to guarantee correct RTL typography and line order in ReportLab.
Fulfills TASK-1.0d.
"""

import os
from typing import List, Optional

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


_FONTS_REGISTERED = False


def get_fonts_dir() -> str:
    """Return the absolute path to the bundled fonts directory."""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "gui", "assets", "fonts")
    )


def register_arabic_fonts() -> None:
    """Register Amiri, Tajawal, and IBM Plex Sans Arabic TTFonts with ReportLab."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return

    fonts_dir = get_fonts_dir()
    font_files = {
        "Amiri": "Amiri-Regular.ttf",
        "Amiri-Bold": "Amiri-Bold.ttf",
        "Tajawal": "Tajawal-Regular.ttf",
        "Tajawal-Bold": "Tajawal-Bold.ttf",
        "IBMPlexArabic": "IBMPlexSansArabic-Regular.ttf",
        "IBMPlexArabic-Bold": "IBMPlexSansArabic-Bold.ttf",
    }

    for font_name, font_file in font_files.items():
        font_path = os.path.join(fonts_dir, font_file)
        if os.path.exists(font_path):
            pdfmetrics.registerFont(TTFont(font_name, font_path))

    _FONTS_REGISTERED = True


def prepare_arabic_line(line: str) -> str:
    """Reshape Arabic letters and apply BiDi display ordering to a single line.
    
    Processing line-by-line is essential to prevent line-inversion artifacts in ReportLab.
    """
    if not line:
        return ""
    configuration = {
        "delete_harakat": False,
        "support_ligatures": True,
    }
    reshaper = arabic_reshaper.ArabicReshaper(configuration=configuration)
    reshaped = reshaper.reshape(line)
    return get_display(reshaped)


def prepare_arabic_paragraph(text: str, max_chars_per_line: int = 65) -> str:
    """Break paragraphs into lines before applying reshaper and bidi.
    
    This prevents ReportLab from wrapping already-reversed strings, which causes
    lines to appear in reverse vertical order.
    Lines are formatted and rejoined with HTML line breaks (<br/>).
    """
    if not text:
        return ""

    raw_blocks = text.split("\n")
    formatted_lines: List[str] = []

    for block in raw_blocks:
        words = block.strip().split()
        if not words:
            formatted_lines.append("")
            continue

        current_line: List[str] = []
        current_len = 0

        for word in words:
            word_len = len(word)
            if current_line and (current_len + word_len + 1 > max_chars_per_line):
                # Emit line
                line_str = " ".join(current_line)
                formatted_lines.append(prepare_arabic_line(line_str))
                current_line = [word]
                current_len = word_len
            else:
                current_line.append(word)
                current_len += word_len + (1 if current_line else 0)

        if current_line:
            line_str = " ".join(current_line)
            formatted_lines.append(prepare_arabic_line(line_str))

    return "<br/>".join(formatted_lines)
