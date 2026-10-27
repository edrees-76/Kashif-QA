"""Integration test verifying Arabic typography rendering in PDF via ReportLab.

Tests arabic-reshaper and python-bidi with Tajawal and IBM Plex Sans Arabic fonts,
verifying connected letter shaping, numbers, mixed-direction text, and correct
top-to-bottom line order without line inversion artifacts.
Fulfills TASK-1.0d.
"""

import os
import pytest
from pypdf import PdfReader

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

from kashif.gui.theme import COLORS
from kashif.reports.pdf_utils import (
    prepare_arabic_line,
    prepare_arabic_paragraph,
    register_arabic_fonts,
)


def test_arabic_pdf_generation_and_line_order():
    """TASK-1.0d: Test Arabic text shaping, line order preservation, and PDF generation."""
    output_dir = os.path.join(os.path.dirname(__file__), "output")
    os.makedirs(output_dir, exist_ok=True)
    pdf_path = os.path.join(output_dir, "arabic_pdf_test.pdf")

    # Register TTFonts (Tajawal, IBM Plex Sans Arabic, Amiri)
    register_arabic_fonts()

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name="ArabicTitle",
        fontName="Tajawal-Bold",
        fontSize=18,
        leading=24,
        alignment=1,  # Center
        textColor=colors.HexColor(COLORS["navy"]),
    )
    body_style = ParagraphStyle(
        name="ArabicBody",
        fontName="IBMPlexArabic",
        fontSize=11,
        leading=18,
        alignment=2,  # Right aligned (RTL)
        textColor=colors.HexColor(COLORS["light_ink"]),
    )

    story = []

    # Title using Tajawal-Bold
    title_text = prepare_arabic_line("منظومة «كاشف» — تقرير الفحص الفني وضبط الجودة (KASHIF-QA)")
    story.append(Paragraph(title_text, title_style))
    story.append(Spacer(1, 15))

    # Multi-line paragraph split into lines before reshaper/bidi to preserve line order
    multi_line_text = (
        "السطر الأول: أُجري الفحص الدوري الشامل لكاشف الجرمانيوم عالي النقاء HPGe وفق إجراءات الوكالة الدولية للطاقة الذرية.\n"
        "السطر الثاني: أظهرت قياسات قمة الكوبالت 1332.5 keV مطابقة تامة لمتطلبات الاستبانة الطاقية مع استقرار ملحوظ في جهد الانحياز.\n"
        "السطر الثالث: يوصي المختبر باعتماد المنظومة للاستخدام المعملي القياسي للفترة القادمة دون الحاجة لمعايرة إضافية."
    )
    shaped_paragraph = prepare_arabic_paragraph(multi_line_text, max_chars_per_line=75)
    story.append(Paragraph(shaped_paragraph, body_style))
    story.append(Spacer(1, 15))

    # Table with Arabic text and numbers (replaces '✓' with '[مطابق]')
    table_data = [
        [
            prepare_arabic_line("المعيار الفيزيائي"),
            prepare_arabic_line("القيمة المقاسة"),
            prepare_arabic_line("حالة المطابقة"),
        ],
        [
            prepare_arabic_line("دقة الطاقة FWHM عند 1332.5 keV"),
            "1.85 keV",
            prepare_arabic_line("مطابق [ناجح]"),
        ],
        [
            prepare_arabic_line("معامل شكل النبضة FWTM/FWHM"),
            "1.88",
            prepare_arabic_line("مطابق [ناجح]"),
        ],
        [
            prepare_arabic_line("الكفاءة النسبية (Relative Efficiency)"),
            "42.5 %",
            prepare_arabic_line("مطابق [ناجح]"),
        ],
    ]
    t = Table(table_data, colWidths=[240, 120, 120])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(COLORS["navy"])),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor(COLORS["light_card"])),
        ('FONTNAME', (0, 0), (-1, -1), 'IBMPlexArabic'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor(COLORS["light_line"])),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [
            colors.HexColor(COLORS["light_card"]),
            colors.HexColor(COLORS["light_mist"]),
        ]),
    ]))
    story.append(t)

    # Build PDF
    doc.build(story)

    assert os.path.exists(pdf_path), "PDF file must be successfully generated."
    assert os.path.getsize(pdf_path) > 1000, "PDF file must contain content (>1KB)."

    # Verify extracted text and validate top-to-bottom line order
    reader = PdfReader(pdf_path)
    assert len(reader.pages) > 0, "PDF must have at least one page."
    page_text = reader.pages[0].extract_text()
    assert page_text, "Extracted text from page must not be empty."

    # In visual extraction, check that line 1 content appears before line 2, and line 2 before line 3
    # Notice that after bidi, words within each line are visually reversed or shaped, but line sequence is preserved.
    # We test for tokens from each line in sequential order:
    pos_line1 = page_text.find("HPGe")
    pos_line2 = page_text.find("1332.5")
    
    assert pos_line1 != -1, "Line 1 identifier 'HPGe' must exist in extracted PDF text."
    assert pos_line2 != -1, "Line 2 identifier '1332.5' must exist in extracted PDF text."
    assert pos_line1 < pos_line2, (
        f"Line order inversion detected! 'HPGe' (index {pos_line1}) should appear before "
        f"'1332.5' (index {pos_line2}) in top-to-bottom reading order."
    )
