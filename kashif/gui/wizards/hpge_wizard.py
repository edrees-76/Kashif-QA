"""HPGe detector inspection wizard dialog (TASK-2.2).

Implements the multi-step inspection workflow:
- Step 1: Cryostat & Liquid Nitrogen Cooling (FR-2 Stage 1)
- Step 2: NIM Voltages, Preamp & High Voltage Ramping (FR-2 Stage 2)
- Steps 3-5: Future stages placeholders (TASK-2.3)
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QDoubleValidator, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from kashif.core.models import Verdict, VoltagePolarity, WindowStatus
from kashif.core.physics import (
    form_factor,
    fwhm_kev,
    fwtm_kev,
)
from kashif.core.rules import (
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
)
from kashif.core.spectrum import calibrate_co60
from kashif.gui.bidi import (
    format_mixed_text,
    isolate_ltr,
    isolate_quantity,
)
from kashif.gui.dialog_utils import (
    adjust_dialog_size_to_screen,
    enable_dialog_window_controls,
    install_wheel_guard_on_inputs,
)
from kashif.gui.theme import COLORS, get_mode, get_stylesheet
from kashif.services.inspection_service import (
    InspectionService,
    InspectionWorkflowError,
    SecurityViolationError,
    check_early_stage_failure,
)
from kashif.services.session import Session

logger = logging.getLogger(__name__)




def format_bidi_message(text: str) -> str:
    """عزل المقاطع اللاتينية والمعادلات والنسب المئوية داخل الرسائل العربية لمنع تشوش BiDi."""
    if not text:
        return ""
    # عزل نسب السماحية مثل ±5.0% أو ±5%
    t = re.sub(r"[±+-]?\d+(?:\.\d+)?\s*%", lambda m: isolate_ltr(m.group(0)), text)
    # عزل المقاطع بين قوسين التي تحوي مفاتيح ومعادلات لاتينية مثل (nom=+24.0V, diff=25.0%)
    t = re.sub(r"\(([A-Za-z0-9_\s\+\-=\.,%]+)\)", lambda m: f"({isolate_ltr(m.group(1))})", t)
    # عزل المعادلات التقنية المفردة مثل v_pos_24=30.00V
    t = re.sub(r"(?<!⁦)([A-Za-z_][A-Za-z0-9_\.]*\s*=\s*[^\s,()]+)(?!⁩)", lambda m: isolate_ltr(m.group(0)), t)
    return format_mixed_text(t)


class RampConfirmCheckBox(QCheckBox):
    """خانة تأكيد مخصصة لمعدل ومصدر رفع الجهد تضمن مؤشراً 20x20 دقيقاً وخلفية متناسقة مع البطاقة."""

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setObjectName("chk_ramp_confirm")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # نمط يضمن خلو الصف من أي إطارات أو خلفيات متسربة، مع تحديد أبعاد المؤشر
        self.setStyleSheet("""
            QCheckBox#chk_ramp_confirm {
                background: transparent;
                border: none;
                outline: none;
                font-weight: bold;
            }
            QCheckBox#chk_ramp_confirm:focus {
                background: transparent;
                border: none;
                outline: none;
            }
            QCheckBox#chk_ramp_confirm::indicator {
                width: 20px;
                height: 20px;
                border: 2px solid #002460;
                border-radius: 4px;
                background-color: #FFFFFF;
            }
            QCheckBox#chk_ramp_confirm::indicator:checked {
                border: 2px solid #002460;
                background-color: #002460;
            }
        """)

    def get_indicator_rect(self) -> QRect:
        """مستطيل المؤشر 20x20 في موضعه الدقيق وفق اتجاه الواجهة (RTL/LTR)."""
        w = self.width()
        h = self.height()
        size = 20
        y = (h - size) // 2
        margin = 4
        x = (w - size - margin) if self.isRightToLeft() else margin
        return QRect(x, y, size, size)

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        w = 20 + 8 + fm.horizontalAdvance(self.text()) + 16
        h = max(24, fm.height() + 8)
        return QSize(w, h)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def get_card_background(self) -> QColor:
        """استنتاج لون خلفية البطاقة الحاضنة ليتناسق مع الوضع الفاتح أو الداكن."""
        if self.palette().color(self.foregroundRole()).lightness() > 128:
            return QColor(COLORS["dark_card"])
        return QColor(COLORS["light_card"])

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        is_rtl = self.isRightToLeft()

        # 1. تعبئة خلفية الصف بلون البطاقة الحاضنة بدقة
        bg_card = self.get_card_background()
        p.fillRect(0, 0, w, h, bg_card)

        ind_rect = self.get_indicator_rect()
        x = ind_rect.x()
        y = ind_rect.y()
        size = ind_rect.width()
        margin = 4

        navy = QColor(COLORS["navy"])
        orange = QColor(COLORS["orange"])
        white = QColor("#FFFFFF")

        has_focus = self.hasFocus()
        is_checked = self.isChecked()

        # إطار التركيز برتقالي حول المؤشر وحده فقط، وإلا فكحلي
        border_color = orange if has_focus else navy
        bg_color = navy if is_checked else white

        # 2. رسم مربع المؤشر 20x20
        p.setPen(QPen(border_color, 2))
        p.setBrush(QBrush(bg_color))
        p.drawRoundedRect(ind_rect, 4, 4)

        # 3. رسم علامة ✓ البيضاء الواضحة داخل المؤشر عند التأشير
        if is_checked:
            pen = QPen(white, 2.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawLine(x + 5, y + 10, x + 8, y + 14)
            p.drawLine(x + 8, y + 14, x + 15, y + 6)

        # 4. رسم النص بلون نص الثيم النشط تلقائياً من الـ palette
        text_color = self.palette().color(self.foregroundRole())
        p.setPen(text_color)
        font = self.font()
        font.setBold(True)
        p.setFont(font)

        spacing = 8
        if is_rtl:
            text_rect = QRect(margin, 0, w - size - margin * 2 - spacing, h)
            align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        else:
            text_rect = QRect(x + size + spacing, 0, w - size - margin * 2 - spacing, h)
            align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

        p.drawText(text_rect, align, self.text())
        p.end()


class HPGeInspectionWizard(QDialog):
    """حوار معالج الفحص الفني الدوري لكواشف الجرمانيوم عالي النقاوة (HPGe)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        session: Session,
        inspection_id: int,
        service: Optional[InspectionService] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.conn = conn
        self.session = session
        self.inspection_id = inspection_id
        self.service = service or InspectionService(conn)

        # تحميل بيانات الفحص والكاشف والقياسات الحالية
        self.inspection_full = self.service.get_inspection_full(self.inspection_id)
        self.inspection = self.inspection_full.get("inspection") or {}
        self.detector = self.inspection_full.get("detector") or {}
        self.saved_measurements = self.inspection_full.get("hpge_measurements") or {}
        self.saved_peaks: List[Dict[str, Any]] = list(self.inspection_full.get("peaks") or [])

        # التحقق من ملكية الفحص وصلاحية التعديل
        self.is_owner = bool(self.inspection.get("technician_id") == self.session.user_id)
        self.is_editable = bool(self.is_owner and self.inspection.get("status") in ("DRAFT", "REJECTED"))

        # متغيرات حالة مؤقت رفع الجهد الاسترشادي
        self._timer_total_seconds: int = 0
        self._timer_remaining_seconds: int = 0
        self._timer_is_running: bool = False
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(1000)
        self._countdown_timer.timeout.connect(self._on_timer_tick)

        # مؤقت مسح رسائل الحفظ التلقائية (مرتبط بدورة حياة النافذة)
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._clear_feedback)

        self._last_invalid_widget: Optional[QWidget] = None

        # تهيئة الواجهة
        det_code = self.detector.get("code") or f"ID-{self.detector.get('id')}"
        self.setWindowTitle(f"معالج فحص كاشف الجرمانيوم (HPGe) — {det_code} [فحص #{self.inspection_id}]")
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setStyleSheet(get_stylesheet())
        enable_dialog_window_controls(self)

        self._init_ui()
        adjust_dialog_size_to_screen(self, preferred_width=920, preferred_height=760)
        install_wheel_guard_on_inputs(self)

        # في حال فتح الفحص من قبل غير الفني المالك، يتم قفل جميع المدخلات للقراءة فقط
        if not self.is_editable:
            self._apply_read_only_mode()

    def _update_timer_display_style(self) -> None:
        if get_mode() == "dark":
            self.lbl_timer_display.setStyleSheet(
                f"font-size: 24px; font-weight: bold; color: {COLORS['dark_teal']}; "
                f"background-color: {COLORS['dark_card']}; border: 1px solid {COLORS['dark_line']}; padding: 6px 16px; border-radius: 4px;"
            )
        else:
            self.lbl_timer_display.setStyleSheet(
                f"font-size: 24px; font-weight: bold; color: {COLORS['navy']}; "
                f"background-color: {COLORS['light_mist']}; padding: 6px 16px; border-radius: 4px;"
            )

    def _update_ramp_confirm_note_style(self) -> None:
        c = COLORS["dark_orange"] if get_mode() == "dark" else COLORS["warning"]
        self.lbl_ramp_confirm_note.setStyleSheet(f"color: {c}; font-weight: bold; margin-right: 8px;")

    def _update_safety_warning_style(self, lbl: QLabel) -> None:
        if get_mode() == "dark":
            lbl.setStyleSheet(
                "background-color: rgba(179, 38, 30, 0.25); color: #F87171; "
                "border: 1px solid #F87171; border-radius: 4px; padding: 8px; font-weight: bold;"
            )
        else:
            lbl.setStyleSheet(
                f"background-color: #FDE8E8; color: {COLORS['error']}; "
                f"border: 1px solid {COLORS['error']}; border-radius: 4px; padding: 8px; font-weight: bold;"
            )

    def refresh_theme(self) -> None:
        """تحديث أنماط العناصر الداخلية عند تبديل الثيم."""
        if hasattr(self, "lbl_timer_display"):
            self._update_timer_display_style()
        if hasattr(self, "lbl_ramp_confirm_note"):
            self._update_ramp_confirm_note_style()
        if hasattr(self, "step_labels"):
            self._update_navigation_state()
        if hasattr(self, "card_step1_banner"):
            self._update_step_1_live_evaluation()
        if hasattr(self, "card_step2_banner"):
            self._update_step_2_live_evaluation()
        if hasattr(self, "card_step3_banner"):
            self._update_step_3_live_evaluation()
        if hasattr(self, "card_step4_banner"):
            self._update_step_4_live_evaluation()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.refresh_theme()

    def done(self, r: int) -> None:
        """إيقاف كافة المؤقتات عند إغلاق النافذة بأي وسيلة (accept, reject, close)."""
        self._stop_all_timers()
        super().done(r)

    def closeEvent(self, event) -> None:
        """إيقاف كافة المؤقتات عند إغلاق النافذة عبر زر X أو الاختصار."""
        self._stop_all_timers()
        super().closeEvent(event)

    def _stop_all_timers(self) -> None:
        """إيقاف آمن لكافة المؤقتات النشطة لمنع استدعاء أي Slot بعد إغلاق النافذة."""
        if hasattr(self, "_countdown_timer") and self._countdown_timer.isActive():
            self._countdown_timer.stop()
        if hasattr(self, "_feedback_timer") and self._feedback_timer.isActive():
            self._feedback_timer.stop()

    def _on_save_draft_clicked(self) -> None:
        self.save_draft(show_feedback=True)

    def _scroll_to_and_focus_widget(self, widget: Optional[QWidget]) -> None:
        """تمرير منطقة التمرير إلى الحقل الناقص أو الخاطئ ونقل التركيز إليه."""
        if widget is None:
            return
        parent = widget.parentWidget()
        scroll_area: Optional[QScrollArea] = None
        while parent is not None:
            if isinstance(parent, QScrollArea):
                scroll_area = parent
                break
            parent = parent.parentWidget()

        if scroll_area is None:
            step_scrolls = [
                getattr(self, "scroll_step_1", None),
                getattr(self, "scroll_step_2", None),
                getattr(self, "scroll_step_3", None),
                getattr(self, "scroll_step_4", None),
            ]
            idx = self.stack.currentIndex()
            if 0 <= idx < len(step_scrolls):
                scroll_area = step_scrolls[idx]

        if scroll_area is not None:
            scroll_area.ensureWidgetVisible(widget)
        widget.setFocus()

    # -----------------------------------------------------------------------
    # بناء الهيكل العام للواجهة
    # -----------------------------------------------------------------------

    def _init_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 14, 18, 14)
        root_layout.setSpacing(10)

        # 1. شريط العنوان ومعلومات الفحص
        header_card = QFrame()
        header_card.setProperty("class", "card")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(6)

        title_row = QHBoxLayout()
        lbl_title = QLabel("معالج الفحص الفني الدوري لكواشف الجرمانيوم عالي النقاوة (HPGe)")
        lbl_title.setProperty("class", "title")
        title_row.addWidget(lbl_title)
        title_row.addStretch()

        det_code = self.detector.get("code") or "—"
        det_serial = self.detector.get("serial_number") or "—"
        lbl_meta = QLabel(f"الكاشف: <b>{format_mixed_text(det_code)}</b> ({isolate_ltr(det_serial)}) | فحص رقم: <b>{self.inspection_id}</b>")
        lbl_meta.setProperty("class", "hint")
        title_row.addWidget(lbl_meta)
        header_layout.addLayout(title_row)

        # لافتة التنبيه للقراءة فقط إذا لم يكن المستخدم المالك
        if not self.is_editable:
            lbl_ro = QLabel("🔒 وضع العرض فقط: الفني صاحب الفحص وحده يملك صلاحية التعديل وحفظ المسودة والتقديم.")
            lbl_ro.setObjectName("lbl_read_only_notice")
            if get_mode() == "dark":
                lbl_ro.setStyleSheet(
                    f"background-color: {COLORS['dark_card']}; color: {COLORS['dark_teal']}; "
                    f"border: 1px solid {COLORS['dark_line']}; border-radius: 4px; padding: 6px; font-weight: bold;"
                )
            else:
                lbl_ro.setStyleSheet(
                    f"background-color: {COLORS['light_mist']}; color: {COLORS['navy']}; "
                    f"border: 1px solid {COLORS['teal']}; border-radius: 4px; padding: 6px; font-weight: bold;"
                )
            header_layout.addWidget(lbl_ro)

        root_layout.addWidget(header_card)

        # 2. شريط مؤشر الخطوات الخمس (Stepper)
        self.stepper_frame = QFrame()
        self.stepper_frame.setObjectName("stepper_frame")
        self.stepper_layout = QHBoxLayout(self.stepper_frame)
        self.stepper_layout.setContentsMargins(4, 4, 4, 4)
        self.stepper_layout.setSpacing(8)

        self.step_labels = []
        self.step_titles = [
            "1. الحاوية والتبريد",
            "2. المضخم والجهد",
            "3. المضخم الرئيسي",
            "4. MCA والمعايرة",
            "5. المراجعة والاعتماد",
        ]
        for idx, title in enumerate(self.step_titles):
            lbl = QLabel(title)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(self._get_step_style(idx, is_current=(idx == 0)))
            self.step_labels.append(lbl)
            self.stepper_layout.addWidget(lbl, 1)

        root_layout.addWidget(self.stepper_frame)

        # 3. مكدس الصفحات (QStackedWidget) بخمس خطوات
        self.stack = QStackedWidget()
        self.stack.setObjectName("wizard_stack")

        # الصفحة 1: الحاوية والتبريد
        self.page_step_1 = self._build_step_1_page()
        self.stack.addWidget(self.page_step_1)

        # الصفحة 2: المضخم الأولي والجهد العالي
        self.page_step_2 = self._build_step_2_page()
        self.stack.addWidget(self.page_step_2)

        # الصفحة 3: المضخم الرئيسي
        self.page_step_3 = self._build_step_3_page()
        self.stack.addWidget(self.page_step_3)

        # الصفحة 4: MCA ومعايرة الطاقة
        self.page_step_4 = self._build_step_4_page()
        self.stack.addWidget(self.page_step_4)

        # الصفحة 5: المراجعة والاعتماد الفني (قيد التطوير)
        self.page_step_5 = self._build_placeholder_page(5, "المراجعة والاعتماد الفني والتقرير")
        self.stack.addWidget(self.page_step_5)

        root_layout.addWidget(self.stack, 1)

        # 4. شريط الأزرار والتنقل السفلي
        btn_bar = QHBoxLayout()
        btn_bar.setSpacing(10)

        self.btn_prev = QPushButton("السابق ➡️")
        self.btn_prev.setObjectName("btn_prev")
        self.btn_prev.setProperty("class", "secondary")
        self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(self._on_prev_step)
        btn_bar.addWidget(self.btn_prev)

        self.btn_save_draft = QPushButton("💾 حفظ كمسودة")
        self.btn_save_draft.setObjectName("btn_save_draft")
        self.btn_save_draft.setProperty("class", "secondary")
        self.btn_save_draft.setEnabled(self.is_editable)
        self.btn_save_draft.clicked.connect(self._on_save_draft_clicked)
        btn_bar.addWidget(self.btn_save_draft)

        self.lbl_feedback = QLabel("")
        self.lbl_feedback.setObjectName("lbl_feedback")
        self.lbl_feedback.setStyleSheet("font-weight: bold; margin-right: 8px;")
        btn_bar.addWidget(self.lbl_feedback)

        btn_bar.addStretch()

        self.btn_close = QPushButton("إغلاق")
        self.btn_close.setObjectName("btn_close")
        self.btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(self.btn_close)

        self.btn_next = QPushButton("التالي ⬅️")
        self.btn_next.setObjectName("btn_next")
        self.btn_next.clicked.connect(self._on_next_step)
        btn_bar.addWidget(self.btn_next)

        root_layout.addLayout(btn_bar)

        # تحديث أولي لحالة شاشات التقييم المباشر
        self._update_step_1_live_evaluation()
        self._update_step_2_live_evaluation()
        self._update_step_3_live_evaluation()
        self._update_step_4_live_evaluation()

    # -----------------------------------------------------------------------
    # الخطوة 1: الحاوية والتبريد (FR-2 المرحلة 1)
    # -----------------------------------------------------------------------

    def _build_step_1_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_step_1")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 8)
        page_layout.setSpacing(10)

        # بطاقة التوجيه واللافتة التقييمية (ثابتة في الأعلى خارج منطقة التمرير)
        self.card_step1_banner = QFrame()
        self.card_step1_banner.setObjectName("card_step1_banner")
        self.card_step1_banner.setProperty("class", "card")
        banner_l = QVBoxLayout(self.card_step1_banner)
        banner_l.setContentsMargins(14, 10, 14, 10)

        self.lbl_step1_status = QLabel("يرجى فحص الحالة المادية لنافذة البريليوم واستقرار الفراغ وزمن التبريد.")
        self.lbl_step1_status.setObjectName("lbl_step1_status")
        self.lbl_step1_status.setWordWrap(True)
        self.lbl_step1_status.setStyleSheet("border: none; background: transparent; font-weight: bold;")
        banner_l.addWidget(self.lbl_step1_status)

        page_layout.addWidget(self.card_step1_banner)

        # منطقة التمرير للحقول فقط
        self.scroll_step_1 = QScrollArea()
        self.scroll_step_1.setObjectName("scroll_step_1")
        self.scroll_step_1.setWidgetResizable(True)
        self.scroll_step_1.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(12)

        # استمارة المدخلات
        form_card = QFrame()
        form_card.setProperty("class", "card")
        form = QFormLayout(form_card)
        form.setSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 1. حالة نافذة البريليوم (بلا اختيار مسبق)
        self.combo_window_status = QComboBox()
        self.combo_window_status.setObjectName("combo_window_status")
        self.combo_window_status.addItem("— اختر حالة نافذة البريليوم —", None)
        self.combo_window_status.addItem("سليمة تماماً ومحكمة (INTACT)", "INTACT")
        self.combo_window_status.addItem("مخدوشة سطحياً دون تسريب (SCRATCHED)", "SCRATCHED")
        self.combo_window_status.addItem("مكسورة أو مثقوبة (BROKEN) — خطر داهم", "BROKEN")
        form.addRow("حالة نافذة البريليوم *:", self.combo_window_status)

        # 2. زمن التبريد الفعلي بالنيتروجين السائل (بلا قيمة افتراضية)
        cooling_row = QHBoxLayout()
        self.edit_cooling_hours = QLineEdit()
        self.edit_cooling_hours.setObjectName("edit_cooling_hours")
        validator = QDoubleValidator(0.0, 999.0, 2, self)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_cooling_hours.setValidator(validator)
        self.edit_cooling_hours.setPlaceholderText("أدخل ساعات التبريد الفعلية (مثال: 6.5)")
        cooling_row.addWidget(self.edit_cooling_hours)

        raw_cert_cool = self.detector.get("cooling_hours_cert")
        effective_min = max(6.0, float(raw_cert_cool) if raw_cert_cool is not None else 0.0)
        if raw_cert_cool is not None and float(raw_cert_cool) > 6.0:
            lbl_text = f"(الحد الأدنى بالشهادة: {isolate_quantity(float(raw_cert_cool), 'h')})"
        else:
            lbl_text = f"(الحد الأدنى المعتمد: {isolate_quantity(6.0, 'h')})"
        lbl_cert_cooling = QLabel(lbl_text)
        lbl_cert_cooling.setProperty("class", "hint")
        cooling_row.addWidget(lbl_cert_cooling)

        form.addRow("مدة التبريد بالنيتروجين *:", cooling_row)

        # 3. إحكام فراغ الحاوية المفرغة (نعم / لا بلا افتراض)
        self.combo_vacuum_intact = QComboBox()
        self.combo_vacuum_intact.setObjectName("combo_vacuum_intact")
        self.combo_vacuum_intact.addItem("— اختر حالة الفراغ —", None)
        self.combo_vacuum_intact.addItem("نعم — الفراغ مستقر ومحكم (سليم)", 1)
        self.combo_vacuum_intact.addItem("لا — رُصد تسريب أو فقدان للفراغ", 0)
        form.addRow("إحكام فراغ الحاوية (Cryostat Vacuum) *:", self.combo_vacuum_intact)

        # 4. رصد رطوبة أو تجمد على الحاوية (نعم / لا بلا افتراض)
        self.combo_wet_frozen = QComboBox()
        self.combo_wet_frozen.setObjectName("combo_wet_frozen")
        self.combo_wet_frozen.addItem("— اختر حالة التندّي والجليد —", None)
        self.combo_wet_frozen.addItem("لا — الحاوية جافة تماماً (سليم)", 0)
        self.combo_wet_frozen.addItem("نعم — يُلاحظ تندّي رطوبة أو تجمد جليد", 1)
        form.addRow("رصد رطوبة أو تجمد على الحاوية *:", self.combo_wet_frozen)

        layout.addWidget(form_card)
        layout.addStretch()

        # تحميل القيم المحفوظة قبل ربط الإشارات
        saved_ws = self.saved_measurements.get("beryllium_window_status")
        if saved_ws:
            idx = self.combo_window_status.findData(saved_ws)
            if idx >= 0:
                self.combo_window_status.setCurrentIndex(idx)
        else:
            self.combo_window_status.setCurrentIndex(0)

        saved_cool = self.saved_measurements.get("cooling_hours")
        if saved_cool is not None:
            self.edit_cooling_hours.setText(str(saved_cool))

        saved_vac = self.saved_measurements.get("vacuum_intact")
        if saved_vac is not None:
            idx = self.combo_vacuum_intact.findData(int(saved_vac))
            if idx >= 0:
                self.combo_vacuum_intact.setCurrentIndex(idx)
        else:
            self.combo_vacuum_intact.setCurrentIndex(0)

        saved_wet = self.saved_measurements.get("cryostat_wet_or_frozen")
        if saved_wet is not None:
            idx = self.combo_wet_frozen.findData(int(saved_wet))
            if idx >= 0:
                self.combo_wet_frozen.setCurrentIndex(idx)
        else:
            self.combo_wet_frozen.setCurrentIndex(0)

        # ربط إشارات المدخلات بعد اكتمال بناء وتحميل عناصر الصفحة
        self.combo_window_status.currentIndexChanged.connect(self._on_step_1_input_changed)
        self.edit_cooling_hours.textChanged.connect(self._on_step_1_input_changed)
        self.combo_vacuum_intact.currentIndexChanged.connect(self._on_step_1_input_changed)
        self.combo_wet_frozen.currentIndexChanged.connect(self._on_step_1_input_changed)

        self.scroll_step_1.setWidget(container)
        page_layout.addWidget(self.scroll_step_1, 1)
        return page

    def _build_step_2_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_step_2")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 8)
        page_layout.setSpacing(10)

        # بطاقة التوجيه واللافتة التقييمية للمرحلة 2 (ثابتة في الأعلى خارج منطقة التمرير)
        self.card_step2_banner = QFrame()
        self.card_step2_banner.setObjectName("card_step2_banner")
        self.card_step2_banner.setProperty("class", "card")
        b2_layout = QVBoxLayout(self.card_step2_banner)
        b2_layout.setContentsMargins(14, 10, 14, 10)

        self.lbl_step2_status = QLabel("يرجى قياس جهود سلة NIM، وتأكيد قطبية الجهد ومعدل الرفع وإشارة المضخم الأولي.")
        self.lbl_step2_status.setObjectName("lbl_step2_status")
        self.lbl_step2_status.setWordWrap(True)
        self.lbl_step2_status.setStyleSheet("border: none; background: transparent; font-weight: bold;")
        b2_layout.addWidget(self.lbl_step2_status)

        page_layout.addWidget(self.card_step2_banner)

        # منطقة التمرير للحقول فقط
        self.scroll_step_2 = QScrollArea()
        self.scroll_step_2.setObjectName("scroll_step_2")
        self.scroll_step_2.setWidgetResizable(True)
        self.scroll_step_2.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(12)

        # استمارة المدخلات
        form_card = QFrame()
        form_card.setProperty("class", "card")
        form = QFormLayout(form_card)
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # جهود سلة NIM القياسية
        lbl_nim_section = QLabel("<b>جهود سلة NIM القياسية (Standard NIM Voltages):</b>")
        lbl_nim_section.setObjectName("lbl_nim_section")
        lbl_nim_section.setProperty("class", "h2")
        form.addRow(lbl_nim_section)

        val_dbl = QDoubleValidator(-100.0, 100.0, 2, self)
        val_dbl.setNotation(QDoubleValidator.Notation.StandardNotation)

        # صف جهود 24V
        h_24 = QHBoxLayout()
        self.edit_v_pos_24 = QLineEdit()
        self.edit_v_pos_24.setObjectName("edit_v_pos_24")
        self.edit_v_pos_24.setValidator(val_dbl)
        self.edit_v_pos_24.setPlaceholderText("+24.0 V (إلزامي)")
        h_24.addWidget(QLabel("+24 V:"))
        h_24.addWidget(self.edit_v_pos_24)

        self.edit_v_neg_24 = QLineEdit()
        self.edit_v_neg_24.setObjectName("edit_v_neg_24")
        self.edit_v_neg_24.setValidator(val_dbl)
        self.edit_v_neg_24.setPlaceholderText("-24.0 V (إلزامي)")
        h_24.addWidget(QLabel("-24 V:"))
        h_24.addWidget(self.edit_v_neg_24)
        form.addRow("جهد ±24 V *:", h_24)

        # صف جهود 12V
        h_12 = QHBoxLayout()
        self.edit_v_pos_12 = QLineEdit()
        self.edit_v_pos_12.setObjectName("edit_v_pos_12")
        self.edit_v_pos_12.setValidator(val_dbl)
        self.edit_v_pos_12.setPlaceholderText("+12.0 V (إلزامي)")
        h_12.addWidget(QLabel("+12 V:"))
        h_12.addWidget(self.edit_v_pos_12)

        self.edit_v_neg_12 = QLineEdit()
        self.edit_v_neg_12.setObjectName("edit_v_neg_12")
        self.edit_v_neg_12.setValidator(val_dbl)
        self.edit_v_neg_12.setPlaceholderText("-12.0 V (إلزامي)")
        h_12.addWidget(QLabel("-12 V:"))
        h_12.addWidget(self.edit_v_neg_12)
        form.addRow("جهد ±12 V *:", h_12)

        # صف جهود 6V (اختياري)
        h_6 = QHBoxLayout()
        self.edit_v_pos_6 = QLineEdit()
        self.edit_v_pos_6.setObjectName("edit_v_pos_6")
        self.edit_v_pos_6.setValidator(val_dbl)
        self.edit_v_pos_6.setPlaceholderText("+6.0 V (اختياري)")
        h_6.addWidget(QLabel("+6 V:"))
        h_6.addWidget(self.edit_v_pos_6)

        self.edit_v_neg_6 = QLineEdit()
        self.edit_v_neg_6.setObjectName("edit_v_neg_6")
        self.edit_v_neg_6.setValidator(val_dbl)
        self.edit_v_neg_6.setPlaceholderText("-6.0 V (اختياري)")
        h_6.addWidget(QLabel("-6 V:"))
        h_6.addWidget(self.edit_v_neg_6)
        form.addRow("جهد ±6 V (اختياري):", h_6)

        # قطبية الجهد العالي المطبق
        self.combo_applied_polarity = QComboBox()
        self.combo_applied_polarity.setObjectName("combo_applied_polarity")
        self.combo_applied_polarity.addItem("— اختر القطبية المطبقة —", None)
        self.combo_applied_polarity.addItem("موجبة (+ POSITIVE)", "POSITIVE")
        self.combo_applied_polarity.addItem("سالبة (- NEGATIVE)", "NEGATIVE")

        pol_info = []
        nom_pol = self.detector.get("voltage_polarity")
        if nom_pol:
            pol_info.append(f"الاسمية: {nom_pol}")
        cert_pol = self.detector.get("certified_polarity")
        if cert_pol:
            pol_info.append(f"المعتمدة: {cert_pol}")
        else:
            pol_info.append("الشهادة: غير موثقة — توثيق ناقص")

        pol_layout = QHBoxLayout()
        pol_layout.addWidget(self.combo_applied_polarity)
        lbl_pol_guide = QLabel(f"({', '.join(pol_info)})")
        lbl_pol_guide.setProperty("class", "hint")
        pol_layout.addWidget(lbl_pol_guide)
        form.addRow("قطبية الجهد العالي المطبق *:", pol_layout)

        # الجهد العالي المستهدف / المطبق
        volt_layout = QHBoxLayout()
        self.edit_applied_voltage = QLineEdit()
        self.edit_applied_voltage.setObjectName("edit_applied_voltage")
        v_validator = QDoubleValidator(0.0, 10000.0, 1, self)
        v_validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_applied_voltage.setValidator(v_validator)
        self.edit_applied_voltage.setPlaceholderText("أدخل الجهد النهائي بالفولت (مثال: 3000)")
        volt_layout.addWidget(self.edit_applied_voltage)

        nom_v = self.detector.get("nominal_voltage")
        lbl_nom_v = QLabel(f"(الجهد الاسمي للكاشف: {isolate_quantity(nom_v or 0.0, 'V')})")
        lbl_nom_v.setProperty("class", "hint")
        volt_layout.addWidget(lbl_nom_v)
        form.addRow("الجهد العالي المطبق *:", volt_layout)

        # مصدر رفع الجهد (يدوي أو برنامج اقتناء)
        self.combo_ramp_source = QComboBox()
        self.combo_ramp_source.setObjectName("combo_ramp_source")
        self.combo_ramp_source.addItem("— اختر مصدر رفع الجهد —", None)
        self.combo_ramp_source.addItem("يدوي عبر مجهز القدرة (MANUAL)", "MANUAL")
        self.combo_ramp_source.addItem("برنامج اقتناء مثل GENIE-2000 (ACQUISITION_SOFTWARE)", "ACQUISITION_SOFTWARE")
        form.addRow("مصدر رفع الجهد *:", self.combo_ramp_source)

        # معدل رفع الجهد مع إبراز حد الكاشف ومرجعه
        ramp_layout = QHBoxLayout()
        self.edit_ramp_rate = QLineEdit()
        self.edit_ramp_rate.setObjectName("edit_ramp_rate")
        r_validator = QDoubleValidator(0.1, 100.0, 2, self)
        r_validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_ramp_rate.setValidator(r_validator)
        self.edit_ramp_rate.setPlaceholderText("الافتراضي 100V كل 10s = 10 V/s")
        ramp_layout.addWidget(self.edit_ramp_rate)

        max_ramp = self.detector.get("hv_ramp_max_v_per_s") or 10.0
        ramp_ref = self.detector.get("hv_ramp_reference") or "غير محدد في بطاقة الكاشف"
        lbl_ramp_limit = QLabel(f"(الحد الأقصى: {isolate_quantity(max_ramp, 'V/s')} | المرجع: {ramp_ref})")
        lbl_ramp_limit.setProperty("class", "hint")
        ramp_layout.addWidget(lbl_ramp_limit)
        form.addRow("معدل رفع الجهد (V/s) *:", ramp_layout)

        # خانة تأكيد معدل ومصدر الرفع الصريحة مع لافتة القيم السابقة
        chk_layout = QHBoxLayout()
        self.chk_ramp_confirm = RampConfirmCheckBox("أؤكد أن هذا المصدر والمعدل هما ما طُبِّق فعلاً")
        self.chk_ramp_confirm.setObjectName("chk_ramp_confirm")
        self.chk_ramp_confirm.setChecked(False)  # غير مؤكدة دائماً عند الفتح
        chk_layout.addWidget(self.chk_ramp_confirm)

        self.lbl_ramp_confirm_note = QLabel("")
        self.lbl_ramp_confirm_note.setObjectName("lbl_ramp_confirm_note")
        self._update_ramp_confirm_note_style()
        chk_layout.addWidget(self.lbl_ramp_confirm_note)
        chk_layout.addStretch()
        form.addRow("", chk_layout)

        # حالة إشارة المضخم الأولي
        self.combo_preamp_signal = QComboBox()
        self.combo_preamp_signal.setObjectName("combo_preamp_signal")
        self.combo_preamp_signal.addItem("— اختر حالة الإشارة —", None)
        self.combo_preamp_signal.addItem("نعم — الإشارة موجودة وسليمة على راسم الإشارة (سليم)", 1)
        self.combo_preamp_signal.addItem("لا — لا توجد إشارة نبضية من المضخم الأولي (0)", 0)
        form.addRow("إشارة خرج المضخم الأولي *:", self.combo_preamp_signal)

        layout.addWidget(form_card)

        # بطاقة مؤقت رفع الجهد الاسترشادي مع تنبيه الأمان الصارم
        timer_card = QFrame()
        timer_card.setProperty("class", "card")
        timer_layout = QVBoxLayout(timer_card)
        timer_layout.setContentsMargins(14, 12, 14, 12)
        timer_layout.setSpacing(8)

        # تنبيه أمان صارم ظاهر دائماً
        lbl_safety_warning = QLabel("⚠️ تنبيه أمان صارم: ممنوع توصيل أو فصل كابل الجهد العالي أثناء تشغيل مجهز القدرة!")
        lbl_safety_warning.setObjectName("lbl_safety_warning")
        self._update_safety_warning_style(lbl_safety_warning)
        timer_layout.addWidget(lbl_safety_warning)

        timer_header = QLabel("مؤقت رفع الجهد الاسترشادي (حساب تقريبي للزمن اللازم للرفع دون تحكم آلي):")
        timer_header.setObjectName("lbl_timer_header")
        timer_header.setProperty("class", "h2")
        timer_layout.addWidget(timer_header)

        self.lbl_timer_calc = QLabel("الزمن المقترح: أدخل الجهد والمعدل لحساب زمن الرفع")
        self.lbl_timer_calc.setObjectName("lbl_timer_calc")
        self.lbl_timer_calc.setProperty("class", "hint")
        timer_layout.addWidget(self.lbl_timer_calc)

        timer_ctrl_row = QHBoxLayout()
        self.lbl_timer_display = QLabel("00:00")
        self.lbl_timer_display.setObjectName("lbl_timer_display")
        self._update_timer_display_style()
        timer_ctrl_row.addWidget(self.lbl_timer_display)

        self.btn_timer_toggle = QPushButton("▶️ تشغيل المؤقت")
        self.btn_timer_toggle.setObjectName("btn_timer_toggle")
        self.btn_timer_toggle.setProperty("class", "secondary")
        timer_ctrl_row.addWidget(self.btn_timer_toggle)

        self.btn_timer_reset = QPushButton("🔄 إعادة ضبط")
        self.btn_timer_reset.setObjectName("btn_timer_reset")
        self.btn_timer_reset.setProperty("class", "secondary")
        timer_ctrl_row.addWidget(self.btn_timer_reset)

        timer_ctrl_row.addStretch()
        timer_layout.addLayout(timer_ctrl_row)

        layout.addWidget(timer_card)
        layout.addStretch()

        # تحميل القيم المحفوظة قبل ربط الإشارات لمنع رفع أي استثناءات أثناء البناء
        for k, edit in [
            ("v_pos_24", self.edit_v_pos_24),
            ("v_neg_24", self.edit_v_neg_24),
            ("v_pos_12", self.edit_v_pos_12),
            ("v_neg_12", self.edit_v_neg_12),
            ("v_pos_6", self.edit_v_pos_6),
            ("v_neg_6", self.edit_v_neg_6),
        ]:
            if self.saved_measurements.get(k) is not None:
                edit.setText(str(self.saved_measurements[k]))

        saved_pol = self.saved_measurements.get("applied_polarity")
        if saved_pol:
            idx = self.combo_applied_polarity.findData(str(saved_pol).upper())
            if idx >= 0:
                self.combo_applied_polarity.setCurrentIndex(idx)
        else:
            self.combo_applied_polarity.setCurrentIndex(0)

        saved_volt = self.saved_measurements.get("applied_voltage")
        if saved_volt is not None:
            self.edit_applied_voltage.setText(str(saved_volt))

        # عرض مصدر ومعدل الرفع المحفوظين في الحقول فقط إذا كانا غير NULL مع إبقاء خانة التأكيد غير مؤشرة
        saved_src = self.saved_measurements.get("hv_ramp_source")
        if saved_src is not None:
            idx = self.combo_ramp_source.findData(str(saved_src).upper())
            if idx >= 0:
                self.combo_ramp_source.setCurrentIndex(idx)
        else:
            self.combo_ramp_source.setCurrentIndex(0)

        saved_rate = self.saved_measurements.get("hv_ramp_rate_v_per_s")
        if saved_rate is not None:
            self.edit_ramp_rate.setText(str(saved_rate))
        else:
            self.edit_ramp_rate.clear()

        self.chk_ramp_confirm.setChecked(False)
        if saved_src is not None or saved_rate is not None:
            self._update_ramp_confirm_note_style()
            self.lbl_ramp_confirm_note.setText("مؤكَّدة في جلسة سابقة")
            self.lbl_ramp_confirm_note.setVisible(True)
        else:
            self.lbl_ramp_confirm_note.setText("")
            self.lbl_ramp_confirm_note.setVisible(False)

        saved_sig = self.saved_measurements.get("has_preamp_signal")
        if saved_sig is not None:
            idx = self.combo_preamp_signal.findData(int(saved_sig))
            if idx >= 0:
                self.combo_preamp_signal.setCurrentIndex(idx)
        else:
            self.combo_preamp_signal.setCurrentIndex(0)

        # ربط إشارات المدخلات بعد اكتمال بناء وتحميل عناصر الصفحة بالكامل
        self.edit_v_pos_24.textChanged.connect(self._on_step_2_input_changed)
        self.edit_v_neg_24.textChanged.connect(self._on_step_2_input_changed)
        self.edit_v_pos_12.textChanged.connect(self._on_step_2_input_changed)
        self.edit_v_neg_12.textChanged.connect(self._on_step_2_input_changed)
        self.edit_v_pos_6.textChanged.connect(self._on_step_2_input_changed)
        self.edit_v_neg_6.textChanged.connect(self._on_step_2_input_changed)
        self.combo_applied_polarity.currentIndexChanged.connect(self._on_step_2_input_changed)
        self.edit_applied_voltage.textChanged.connect(self._on_step_2_input_changed)
        self.combo_ramp_source.currentIndexChanged.connect(self._on_step_2_input_changed)
        self.edit_ramp_rate.textChanged.connect(self._on_step_2_input_changed)
        self.chk_ramp_confirm.stateChanged.connect(self._on_step_2_input_changed)
        self.combo_preamp_signal.currentIndexChanged.connect(self._on_step_2_input_changed)
        self.btn_timer_toggle.clicked.connect(self._on_toggle_timer)
        self.btn_timer_reset.clicked.connect(self._on_reset_timer)

        self.scroll_step_2.setWidget(container)
        page_layout.addWidget(self.scroll_step_2, 1)
        return page

    def _build_step_3_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_step_3")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 8)
        page_layout.setSpacing(10)

        # بطاقة التوجيه واللافتة التقييمية (ثابتة في الأعلى خارج منطقة التمرير)
        self.card_step3_banner = QFrame()
        self.card_step3_banner.setObjectName("card_step3_banner")
        self.card_step3_banner.setProperty("class", "card")
        banner_l = QVBoxLayout(self.card_step3_banner)
        banner_l.setContentsMargins(14, 10, 14, 10)

        self.lbl_step3_status = QLabel("يرجى ضبط إعدادات المضخم الرئيسي والتحقق من استواء نبضة تعويض القطب والصفر (P/Z).")
        self.lbl_step3_status.setObjectName("lbl_step3_status")
        self.lbl_step3_status.setWordWrap(True)
        self.lbl_step3_status.setStyleSheet("border: none; background: transparent; font-weight: bold;")
        banner_l.addWidget(self.lbl_step3_status)
        page_layout.addWidget(self.card_step3_banner)

        # منطقة التمرير للحقول فقط
        self.scroll_step_3 = QScrollArea()
        self.scroll_step_3.setObjectName("scroll_step_3")
        self.scroll_step_3.setWidgetResizable(True)
        self.scroll_step_3.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(12)

        # بطاقة إعدادات المضخم (اختيارية)
        card_amp = QFrame()
        card_amp.setObjectName("card_step3_amp_settings")
        card_amp.setProperty("class", "card")
        l_amp = QVBoxLayout(card_amp)
        l_amp.setContentsMargins(14, 14, 14, 14)
        l_amp.setSpacing(10)

        lbl_hdr_amp = QLabel("إعدادات المضخم الطيفي الرئيسي (Spectroscopy Amplifier)")
        lbl_hdr_amp.setObjectName("lbl_step3_amp_header")
        lbl_hdr_amp.setProperty("class", "h2")
        l_amp.addWidget(lbl_hdr_amp)

        form_amp = QFormLayout()
        form_amp.setSpacing(12)
        form_amp.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 1. زمن التشكيل shaping_time_us
        self.edit_shaping_time = QLineEdit()
        self.edit_shaping_time.setObjectName("edit_shaping_time")
        val_shaping = QDoubleValidator(0.01, 100.0, 2, self)
        val_shaping.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_shaping_time.setValidator(val_shaping)
        self.edit_shaping_time.setPlaceholderText("زمن التشكيل بالمايكروثانية (مثال: 4.0)")
        form_amp.addRow("زمن التشكيل Shaping Time (µs):", self.edit_shaping_time)

        # 2. الكسب الخشن coarse_gain
        self.edit_coarse_gain = QLineEdit()
        self.edit_coarse_gain.setObjectName("edit_coarse_gain")
        val_cgain = QDoubleValidator(0.01, 10000.0, 2, self)
        val_cgain.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_coarse_gain.setValidator(val_cgain)
        self.edit_coarse_gain.setPlaceholderText("معامل الكسب الخشن (مثال: 50.0)")
        form_amp.addRow("الكسب الخشن Coarse Gain:", self.edit_coarse_gain)

        # 3. الكسب الدقيق fine_gain
        self.edit_fine_gain = QLineEdit()
        self.edit_fine_gain.setObjectName("edit_fine_gain")
        val_fgain = QDoubleValidator(0.01, 1000.0, 3, self)
        val_fgain.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_fine_gain.setValidator(val_fgain)
        self.edit_fine_gain.setPlaceholderText("معامل الكسب الدقيق (مثال: 1.05)")
        form_amp.addRow("الكسب الدقيق Fine Gain:", self.edit_fine_gain)

        # 4. استعادة خط الأساس baseline_restorer
        self.edit_baseline_restorer = QLineEdit()
        self.edit_baseline_restorer.setObjectName("edit_baseline_restorer")
        self.edit_baseline_restorer.setPlaceholderText("وضع استعادة خط الأساس (مثال: AUTO أو HIGH أو OFF)")
        form_amp.addRow("استعادة خط الأساس Baseline Restorer (BLR):", self.edit_baseline_restorer)

        # 5. ضبط P/Z pole_zero_setting
        self.edit_pole_zero_setting = QLineEdit()
        self.edit_pole_zero_setting.setObjectName("edit_pole_zero_setting")
        self.edit_pole_zero_setting.setPlaceholderText("وضع أو قراءة ضبط المقاومة (مثال: مستوية)")
        form_amp.addRow("ضبط مقاومة تعويض القطب والصفر (P/Z):", self.edit_pole_zero_setting)

        # 6. سعة الخرج amp_output_amplitude
        self.edit_amp_output_amplitude = QLineEdit()
        self.edit_amp_output_amplitude.setObjectName("edit_amp_output_amplitude")
        val_amp = QDoubleValidator(0.01, 50.0, 2, self)
        val_amp.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_amp_output_amplitude.setValidator(val_amp)
        self.edit_amp_output_amplitude.setPlaceholderText("سعة نبضة الخرج بالفولت (المثالي ~5.0 V)")
        form_amp.addRow("سعة نبضة الخرج Amplitude (V):", self.edit_amp_output_amplitude)

        l_amp.addLayout(form_amp)
        layout.addWidget(card_amp)

        # بطاقة فحص تشوه نبضة P/Z (إلزامية)
        card_pz = QFrame()
        card_pz.setObjectName("card_step3_pz_check")
        card_pz.setProperty("class", "card")
        l_pz = QVBoxLayout(card_pz)
        l_pz.setContentsMargins(14, 14, 14, 14)
        l_pz.setSpacing(10)

        lbl_hdr_pz = QLabel("فحص تشوه نبضة تعويض القطب والصفر (P/Z Distortion Check)")
        lbl_hdr_pz.setObjectName("lbl_step3_pz_header")
        lbl_hdr_pz.setProperty("class", "h2")
        l_pz.addWidget(lbl_hdr_pz)

        form_pz = QFormLayout()
        form_pz.setSpacing(12)
        form_pz.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.combo_pz_distortion = QComboBox()
        self.combo_pz_distortion.setObjectName("combo_pz_distortion")
        self.combo_pz_distortion.addItem("— اختر حالة تشوه النبضة —", None)
        self.combo_pz_distortion.addItem("لا (النبضة مستوية وسليمة دون ارتداد)", 0)
        self.combo_pz_distortion.addItem("نعم (يوجد تشوه ارتدادي Overshoot أو Undershoot)", 1)
        form_pz.addRow("هل يوجد تشوه في نبضة المضخم (ارتداد علوي أو سفلي)؟ *:", self.combo_pz_distortion)

        l_pz.addLayout(form_pz)
        layout.addWidget(card_pz)

        layout.addStretch()

        # تحميل القيم المخزّنة إن وجدت
        if self.saved_measurements.get("shaping_time_us") is not None:
            self.edit_shaping_time.setText(str(self.saved_measurements["shaping_time_us"]))
        if self.saved_measurements.get("coarse_gain") is not None:
            self.edit_coarse_gain.setText(str(self.saved_measurements["coarse_gain"]))
        if self.saved_measurements.get("fine_gain") is not None:
            self.edit_fine_gain.setText(str(self.saved_measurements["fine_gain"]))
        if self.saved_measurements.get("baseline_restorer"):
            self.edit_baseline_restorer.setText(str(self.saved_measurements["baseline_restorer"]))
        if self.saved_measurements.get("pole_zero_setting"):
            self.edit_pole_zero_setting.setText(str(self.saved_measurements["pole_zero_setting"]))
        if self.saved_measurements.get("amp_output_amplitude") is not None:
            self.edit_amp_output_amplitude.setText(str(self.saved_measurements["amp_output_amplitude"]))

        saved_pz = self.saved_measurements.get("has_pz_distortion")
        if saved_pz is not None:
            idx = self.combo_pz_distortion.findData(int(saved_pz))
            if idx >= 0:
                self.combo_pz_distortion.setCurrentIndex(idx)
        else:
            self.combo_pz_distortion.setCurrentIndex(0)

        # ربط إشارات التحديث الحي
        self.combo_pz_distortion.currentIndexChanged.connect(self._on_step_3_input_changed)

        self.scroll_step_3.setWidget(container)
        page_layout.addWidget(self.scroll_step_3, 1)
        return page

    def _build_step_4_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("page_step_4")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(10, 8, 10, 8)
        page_layout.setSpacing(10)

        # بطاقة التوجيه واللافتة التقييمية المجمعة (ثابتة في الأعلى خارج منطقة التمرير)
        self.card_step4_banner = QFrame()
        self.card_step4_banner.setObjectName("card_step4_banner")
        self.card_step4_banner.setProperty("class", "card")
        banner_l = QVBoxLayout(self.card_step4_banner)
        banner_l.setContentsMargins(14, 10, 14, 10)

        self.lbl_step4_status = QLabel("يرجى إدخال إحصاءات MCA وقمم الكوبالت-60 لحساب المعايرة الطاقية والدقة ومعامل الشكل.")
        self.lbl_step4_status.setObjectName("lbl_step4_status")
        self.lbl_step4_status.setWordWrap(True)
        self.lbl_step4_status.setStyleSheet("border: none; background: transparent; font-weight: bold;")
        banner_l.addWidget(self.lbl_step4_status)
        page_layout.addWidget(self.card_step4_banner)

        # منطقة التمرير للحقول فقط
        self.scroll_step_4 = QScrollArea()
        self.scroll_step_4.setObjectName("scroll_step_4")
        self.scroll_step_4.setWidgetResizable(True)
        self.scroll_step_4.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(12)

        # 1. بطاقة إحصاءات MCA
        card_mca = QFrame()
        card_mca.setObjectName("card_step4_mca")
        card_mca.setProperty("class", "card")
        l_mca = QVBoxLayout(card_mca)
        l_mca.setContentsMargins(14, 14, 14, 14)
        l_mca.setSpacing(10)

        lbl_hdr_mca = QLabel("1. إحصائيات المحلل متعدد القنوات (MCA Statistics)")
        lbl_hdr_mca.setObjectName("lbl_step4_mca_header")
        lbl_hdr_mca.setProperty("class", "h2")
        l_mca.addWidget(lbl_hdr_mca)

        form_mca = QFormLayout()
        form_mca.setSpacing(12)
        form_mca.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.edit_total_count_rate_cps = QLineEdit()
        self.edit_total_count_rate_cps.setObjectName("edit_total_count_rate_cps")
        v_cps = QDoubleValidator(0.0, 1000000.0, 2, self)
        v_cps.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_total_count_rate_cps.setValidator(v_cps)
        self.edit_total_count_rate_cps.setPlaceholderText("معدل العد الإجمالي (cps) (مثال: 1200.0)")
        form_mca.addRow("إجمالي معدل النبضات Total Count Rate (cps) *:", self.edit_total_count_rate_cps)

        self.edit_dead_time_pct = QLineEdit()
        self.edit_dead_time_pct.setObjectName("edit_dead_time_pct")
        v_dt = QDoubleValidator(0.0, 100.0, 2, self)
        v_dt.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_dead_time_pct.setValidator(v_dt)
        self.edit_dead_time_pct.setPlaceholderText("نسبة زمن الموت المئوية (مثال: 2.5)")
        form_mca.addRow("نسبة زمن الموت Dead Time (%) *:", self.edit_dead_time_pct)

        self.edit_live_time_sec = QLineEdit()
        self.edit_live_time_sec.setObjectName("edit_live_time_sec")
        v_lt = QDoubleValidator(0.1, 1000000.0, 2, self)
        v_lt.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_live_time_sec.setValidator(v_lt)
        self.edit_live_time_sec.setPlaceholderText("زمن القياس الحي بالثواني (مثال: 300.0)")
        form_mca.addRow("زمن القياس الحي Live Time (s) *:", self.edit_live_time_sec)

        l_mca.addLayout(form_mca)
        layout.addWidget(card_mca)

        # 2. بطاقة قمة الكوبالت-60 الأولى (1173.2 keV)
        card_p1 = QFrame()
        card_p1.setObjectName("card_step4_peak1")
        card_p1.setProperty("class", "card")
        l_p1 = QVBoxLayout(card_p1)
        l_p1.setContentsMargins(14, 14, 14, 14)
        l_p1.setSpacing(10)

        lbl_hdr_p1 = QLabel("2. قمة الكوبالت-60 الأولى (1173.2 keV)")
        lbl_hdr_p1.setObjectName("lbl_step4_p1_header")
        lbl_hdr_p1.setProperty("class", "h2")
        l_p1.addWidget(lbl_hdr_p1)

        form_p1 = QFormLayout()
        form_p1.setSpacing(12)
        form_p1.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.edit_p1_centroid = QLineEdit()
        self.edit_p1_centroid.setObjectName("edit_p1_centroid")
        v_ch = QDoubleValidator(0.1, 65536.0, 2, self)
        v_ch.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_p1_centroid.setValidator(v_ch)
        self.edit_p1_centroid.setPlaceholderText("موقع القناة المركزية (مثال: 2346.4)")
        form_p1.addRow("موقع القناة المركزية Centroid Channel *:", self.edit_p1_centroid)

        self.edit_p1_fwhm_ch = QLineEdit()
        self.edit_p1_fwhm_ch.setObjectName("edit_p1_fwhm_ch")
        v_w = QDoubleValidator(0.01, 1000.0, 2, self)
        v_w.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_p1_fwhm_ch.setValidator(v_w)
        self.edit_p1_fwhm_ch.setPlaceholderText("عرض نصف الارتفاع بالقنوات (مثال: 3.60)")
        form_p1.addRow("عرض FWHM بالقنوات (Channels) *:", self.edit_p1_fwhm_ch)

        self.edit_p1_fwtm_ch = QLineEdit()
        self.edit_p1_fwtm_ch.setObjectName("edit_p1_fwtm_ch")
        self.edit_p1_fwtm_ch.setValidator(v_w)
        self.edit_p1_fwtm_ch.setPlaceholderText("عرض عُشر الارتفاع بالقنوات (مثال: 6.65)")
        form_p1.addRow("عرض FWTM بالقنوات (Channels) *:", self.edit_p1_fwtm_ch)

        self.edit_p1_net_area = QLineEdit()
        self.edit_p1_net_area.setObjectName("edit_p1_net_area")
        v_area = QDoubleValidator(0.0, 100000000.0, 2, self)
        v_area.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.edit_p1_net_area.setValidator(v_area)
        self.edit_p1_net_area.setPlaceholderText("المساحة الصافية للقمة (مثال: 15000)")
        form_p1.addRow("المساحة الصافية للقمة Net Area Counts *:", self.edit_p1_net_area)

        l_p1.addLayout(form_p1)
        layout.addWidget(card_p1)

        # 3. بطاقة قمة الكوبالت-60 الثانية (1332.5 keV)
        card_p2 = QFrame()
        card_p2.setObjectName("card_step4_peak2")
        card_p2.setProperty("class", "card")
        l_p2 = QVBoxLayout(card_p2)
        l_p2.setContentsMargins(14, 14, 14, 14)
        l_p2.setSpacing(10)

        lbl_hdr_p2 = QLabel("3. قمة الكوبالت-60 الثانية (1332.5 keV)")
        lbl_hdr_p2.setObjectName("lbl_step4_p2_header")
        lbl_hdr_p2.setProperty("class", "h2")
        l_p2.addWidget(lbl_hdr_p2)

        form_p2 = QFormLayout()
        form_p2.setSpacing(12)
        form_p2.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.edit_p2_centroid = QLineEdit()
        self.edit_p2_centroid.setObjectName("edit_p2_centroid")
        self.edit_p2_centroid.setValidator(v_ch)
        self.edit_p2_centroid.setPlaceholderText("موقع القناة المركزية (مثال: 2665.0)")
        form_p2.addRow("موقع القناة المركزية Centroid Channel *:", self.edit_p2_centroid)

        self.edit_p2_fwhm_ch = QLineEdit()
        self.edit_p2_fwhm_ch.setObjectName("edit_p2_fwhm_ch")
        self.edit_p2_fwhm_ch.setValidator(v_w)
        self.edit_p2_fwhm_ch.setPlaceholderText("عرض نصف الارتفاع بالقنوات (مثال: 3.70)")
        form_p2.addRow("عرض FWHM بالقنوات (Channels) *:", self.edit_p2_fwhm_ch)

        self.edit_p2_fwtm_ch = QLineEdit()
        self.edit_p2_fwtm_ch.setObjectName("edit_p2_fwtm_ch")
        self.edit_p2_fwtm_ch.setValidator(v_w)
        self.edit_p2_fwtm_ch.setPlaceholderText("عرض عُشر الارتفاع بالقنوات (مثال: 6.85)")
        form_p2.addRow("عرض FWTM بالقنوات (Channels) *:", self.edit_p2_fwtm_ch)

        self.edit_p2_net_area = QLineEdit()
        self.edit_p2_net_area.setObjectName("edit_p2_net_area")
        self.edit_p2_net_area.setValidator(v_area)
        self.edit_p2_net_area.setPlaceholderText("المساحة الصافية للقمة (مثال: 12500)")
        form_p2.addRow("المساحة الصافية للقمة Net Area Counts *:", self.edit_p2_net_area)

        l_p2.addLayout(form_p2)
        layout.addWidget(card_p2)

        # 4. بطاقة النتائج المحسوبة تلقائياً (للعرض فقط)
        card_calc = QFrame()
        card_calc.setObjectName("card_step4_calc_results")
        card_calc.setProperty("class", "card")
        l_calc = QVBoxLayout(card_calc)
        l_calc.setContentsMargins(14, 14, 14, 14)
        l_calc.setSpacing(10)

        lbl_hdr_calc = QLabel("4. نتائج المعايرة الخطية والدقة الطاقية (محسوبة تلقائياً)")
        lbl_hdr_calc.setObjectName("lbl_step4_calc_header")
        lbl_hdr_calc.setProperty("class", "h2")
        l_calc.addWidget(lbl_hdr_calc)

        form_calc = QFormLayout()
        form_calc.setSpacing(12)
        form_calc.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.edit_slope_m = QLineEdit()
        self.edit_slope_m.setObjectName("edit_calibration_slope_m")
        self.edit_slope_m.setReadOnly(True)
        form_calc.addRow("معامل المعايرة الطاقية Slope m (keV/channel):", self.edit_slope_m)

        self.edit_intercept_b = QLineEdit()
        self.edit_intercept_b.setObjectName("edit_calibration_intercept_b")
        self.edit_intercept_b.setReadOnly(True)
        form_calc.addRow("نقطة التقاطع Intercept b (keV):", self.edit_intercept_b)

        self.edit_measured_fwhm_kev = QLineEdit()
        self.edit_measured_fwhm_kev.setObjectName("edit_measured_fwhm_kev")
        self.edit_measured_fwhm_kev.setReadOnly(True)
        form_calc.addRow("دقة التحليل FWHM (keV) عند 1332.5:", self.edit_measured_fwhm_kev)

        self.edit_measured_fwtm_kev = QLineEdit()
        self.edit_measured_fwtm_kev.setObjectName("edit_measured_fwtm_kev")
        self.edit_measured_fwtm_kev.setReadOnly(True)
        form_calc.addRow("دقة التحليل FWTM (keV) عند 1332.5:", self.edit_measured_fwtm_kev)

        self.edit_form_factor = QLineEdit()
        self.edit_form_factor.setObjectName("edit_form_factor")
        self.edit_form_factor.setReadOnly(True)
        form_calc.addRow("معامل شكل النبضة Form Factor (FWTM/FWHM):", self.edit_form_factor)

        self.edit_net_counts_1332 = QLineEdit()
        self.edit_net_counts_1332.setObjectName("edit_net_counts_1332")
        self.edit_net_counts_1332.setReadOnly(True)
        form_calc.addRow("المساحة الصافية لقمة 1332.5 keV (نبضات):", self.edit_net_counts_1332)

        l_calc.addLayout(form_calc)
        layout.addWidget(card_calc)

        layout.addStretch()

        # تحميل القيم من القياسات المحفوظة
        if self.saved_measurements.get("total_count_rate_cps") is not None:
            self.edit_total_count_rate_cps.setText(str(self.saved_measurements["total_count_rate_cps"]))
        if self.saved_measurements.get("dead_time_pct") is not None:
            self.edit_dead_time_pct.setText(str(self.saved_measurements["dead_time_pct"]))
        if self.saved_measurements.get("live_time_sec") is not None:
            self.edit_live_time_sec.setText(str(self.saved_measurements["live_time_sec"]))

        # تحميل القمم من saved_peaks
        p1 = next((p for p in self.saved_peaks if abs(float(p.get("energy_reference_kev", 0)) - 1173.2) < 1.0 or "1173" in str(p.get("peak_name", ""))), None)
        if p1:
            if p1.get("centroid_channel") is not None:
                self.edit_p1_centroid.setText(str(p1["centroid_channel"]))
            if p1.get("fwhm_channels") is not None:
                self.edit_p1_fwhm_ch.setText(str(p1["fwhm_channels"]))
            if p1.get("fwtm_channels") is not None:
                self.edit_p1_fwtm_ch.setText(str(p1["fwtm_channels"]))
            if p1.get("net_area_counts") is not None:
                self.edit_p1_net_area.setText(str(p1["net_area_counts"]))
            if not self.edit_live_time_sec.text() and p1.get("live_time_sec") is not None:
                self.edit_live_time_sec.setText(str(p1["live_time_sec"]))

        p2 = next((p for p in self.saved_peaks if abs(float(p.get("energy_reference_kev", 0)) - 1332.5) < 1.0 or "1332" in str(p.get("peak_name", ""))), None)
        if p2:
            if p2.get("centroid_channel") is not None:
                self.edit_p2_centroid.setText(str(p2["centroid_channel"]))
            if p2.get("fwhm_channels") is not None:
                self.edit_p2_fwhm_ch.setText(str(p2["fwhm_channels"]))
            if p2.get("fwtm_channels") is not None:
                self.edit_p2_fwtm_ch.setText(str(p2["fwtm_channels"]))
            if p2.get("net_area_counts") is not None:
                self.edit_p2_net_area.setText(str(p2["net_area_counts"]))
            if not self.edit_live_time_sec.text() and p2.get("live_time_sec") is not None:
                self.edit_live_time_sec.setText(str(p2["live_time_sec"]))

        # ربط إشارات التحديث الحي
        for w in (
            self.edit_total_count_rate_cps,
            self.edit_dead_time_pct,
            self.edit_live_time_sec,
            self.edit_p1_centroid,
            self.edit_p1_fwhm_ch,
            self.edit_p1_fwtm_ch,
            self.edit_p1_net_area,
            self.edit_p2_centroid,
            self.edit_p2_fwhm_ch,
            self.edit_p2_fwtm_ch,
            self.edit_p2_net_area,
        ):
            w.textChanged.connect(self._on_step_4_input_changed)

        self.scroll_step_4.setWidget(container)
        page_layout.addWidget(self.scroll_step_4, 1)
        return page

    def _build_placeholder_page(self, step_number: int, step_title: str) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 30, 20, 20)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        card = QFrame()
        card.setProperty("class", "card")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(24, 24, 24, 24)
        card_l.setSpacing(12)
        card_l.setAlignment(Qt.AlignmentFlag.AlignCenter)

        lbl_step = QLabel(f"المرحلة {step_number}: {step_title}")
        lbl_step.setProperty("class", "title")
        lbl_step.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_l.addWidget(lbl_step)

        lbl_notice = QLabel("🚧 هذه الصفحة قيد التطوير (TASK-2.3)")
        lbl_notice.setObjectName(f"lbl_placeholder_step_{step_number}")
        lbl_notice.setStyleSheet(
            f"background-color: {COLORS['light_mist']}; color: {COLORS['navy']}; "
            f"border: 1px dashed {COLORS['teal']}; border-radius: 6px; padding: 16px; "
            f"font-size: 15px; font-weight: bold;"
        )
        lbl_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_l.addWidget(lbl_notice)

        lbl_desc = QLabel("لا يمكن إنهاء الفحص أو تقديمه من هذه الخطوة في هذه المهمة الحالية.")
        lbl_desc.setProperty("class", "hint")
        lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_l.addWidget(lbl_desc)

        layout.addWidget(card)
        layout.addStretch()
        return container

    # -----------------------------------------------------------------------
    # مؤقت رفع الجهد الاسترشادي
    # -----------------------------------------------------------------------

    def _on_toggle_timer(self) -> None:
        if self._timer_is_running:
            self._countdown_timer.stop()
            self._timer_is_running = False
            self.btn_timer_toggle.setText("▶️ استئناف")
        else:
            if self._timer_remaining_seconds <= 0:
                self._update_timer_target_seconds()
            if self._timer_remaining_seconds > 0:
                self._countdown_timer.start()
                self._timer_is_running = True
                self.btn_timer_toggle.setText("⏸️ إيقاف مؤقت")

    def _on_reset_timer(self) -> None:
        self._countdown_timer.stop()
        self._timer_is_running = False
        self.btn_timer_toggle.setText("▶️ تشغيل المؤقت")
        self._update_timer_target_seconds()

    def _update_timer_target_seconds(self) -> None:
        if not hasattr(self, "edit_applied_voltage") or not hasattr(self, "edit_ramp_rate"):
            return
        try:
            v = float(self.edit_applied_voltage.text().strip())
            r = float(self.edit_ramp_rate.text().strip())
            if r > 0 and v > 0:
                total_s = int(round(v / r))
                self._timer_total_seconds = total_s
                self._timer_remaining_seconds = total_s
                mins, secs = divmod(total_s, 60)
                self.lbl_timer_calc.setText(f"الزمن المقترح للرفع: {total_s} ثانية ({mins:02d}:{secs:02d} دقيقة)")
                self.lbl_timer_display.setText(f"{mins:02d}:{secs:02d}")
                return
        except ValueError:
            pass
        self._timer_total_seconds = 0
        self._timer_remaining_seconds = 0
        self.lbl_timer_calc.setText("الزمن المقترح: أدخل الجهد والمعدل لحساب زمن الرفع")
        self.lbl_timer_display.setText("00:00")

    def _on_timer_tick(self) -> None:
        if self._timer_remaining_seconds > 0:
            self._timer_remaining_seconds -= 1
            mins, secs = divmod(self._timer_remaining_seconds, 60)
            self.lbl_timer_display.setText(f"{mins:02d}:{secs:02d}")
        else:
            self._countdown_timer.stop()
            self._timer_is_running = False
            self.btn_timer_toggle.setText("▶️ تشغيل المؤقت")
            self.lbl_timer_calc.setText("اكتمل زمن الرفع المقترح! يرجى التأكد من استقرار الجهد.")

    # -----------------------------------------------------------------------
    # التقييم المباشر لقواعد المرحلتين 1 و 2
    # -----------------------------------------------------------------------

    def _get_current_inspection_snapshot(self) -> Dict[str, Any]:
        """تجميع لقطة القياسات الحالية لفحص حالات الإخفاق المبكر عبر check_early_stage_failure."""
        meas: Dict[str, Any] = {}
        if hasattr(self, "combo_window_status"):
            ws = self.combo_window_status.currentData()
            if ws is not None:
                meas["beryllium_window_status"] = ws
        if hasattr(self, "combo_vacuum_intact"):
            vac = self.combo_vacuum_intact.currentData()
            if vac is not None:
                meas["vacuum_intact"] = vac
        if hasattr(self, "combo_wet_frozen"):
            wet = self.combo_wet_frozen.currentData()
            if wet is not None:
                meas["cryostat_wet_or_frozen"] = wet
        if hasattr(self, "combo_applied_polarity"):
            pol = self.combo_applied_polarity.currentData()
            if pol is not None:
                meas["applied_polarity"] = pol
        if hasattr(self, "combo_preamp_signal"):
            sig = self.combo_preamp_signal.currentData()
            if sig is not None:
                meas["has_preamp_signal"] = sig
        return {
            "detector": self.detector,
            "hpge_measurements": meas,
        }

    def _on_step_1_input_changed(self) -> None:
        self._update_step_1_live_evaluation()

    def _on_step_2_input_changed(self) -> None:
        self._update_timer_target_seconds()
        self._update_step_2_live_evaluation()

    def _on_step_3_input_changed(self) -> None:
        self._update_step_3_live_evaluation()

    def _on_step_4_input_changed(self) -> None:
        self._update_step_4_live_evaluation()

    def _update_step_3_live_evaluation(self) -> None:
        if not hasattr(self, "card_step3_banner") or not hasattr(self, "combo_pz_distortion"):
            return

        pz = self.combo_pz_distortion.currentData()
        if pz is None:
            self.lbl_step3_status.setText("يرجى الإجابة عن فحص تشوه نبضة Pole-Zero (إلزامي).")
            self._style_banner(self.card_step3_banner, "normal")
            return

        res_09 = eval_r_hpge_09_pole_zero(has_pz_distortion=bool(pz == 1))
        if res_09.verdict == Verdict.OUT_OF_SPEC:
            self.lbl_step3_status.setText(
                f"⚠️ تحذير (R-HPGE-09): {res_09.message_ar}. (يُسمح بالعبور مع إلزامية الضبط أو إرفاق التبرير)."
            )
            self._style_banner(self.card_step3_banner, "warning")
        else:
            self.lbl_step3_status.setText(f"✓ {res_09.message_ar}.")
            self._style_banner(self.card_step3_banner, "success")

    def _update_step_4_live_evaluation(self) -> None:
        if not hasattr(self, "card_step4_banner"):
            return

        cps_txt = self.edit_total_count_rate_cps.text().strip()
        dt_txt = self.edit_dead_time_pct.text().strip()
        live_txt = self.edit_live_time_sec.text().strip()
        p1_c_txt = self.edit_p1_centroid.text().strip()
        p1_w_txt = self.edit_p1_fwhm_ch.text().strip()
        p1_t_txt = self.edit_p1_fwtm_ch.text().strip()
        p1_a_txt = self.edit_p1_net_area.text().strip()
        p2_c_txt = self.edit_p2_centroid.text().strip()
        p2_w_txt = self.edit_p2_fwhm_ch.text().strip()
        p2_t_txt = self.edit_p2_fwtm_ch.text().strip()
        p2_a_txt = self.edit_p2_net_area.text().strip()

        # إذا كانت القمم غير مكتملة، نفرغ الحقول المحسوبة
        if not (p1_c_txt and p2_c_txt and p1_w_txt and p1_t_txt and p2_w_txt and p2_t_txt and p2_a_txt):
            self.edit_slope_m.clear()
            self.edit_intercept_b.clear()
            self.edit_measured_fwhm_kev.clear()
            self.edit_measured_fwtm_kev.clear()
            self.edit_form_factor.clear()
            self.edit_net_counts_1332.clear()

            # تقييم معدل العد إن توفر
            if cps_txt and dt_txt:
                try:
                    res_10 = eval_r_hpge_10_mca_rates(float(cps_txt), float(dt_txt))
                    if res_10.verdict == Verdict.OUT_OF_SPEC:
                        self.lbl_step4_status.setText(f"⚠️ {res_10.message_ar}")
                        self._style_banner(self.card_step4_banner, "warning")
                        return
                except ValueError:
                    pass

            self.lbl_step4_status.setText("يرجى إدخال إحصاءات MCA وقمم الكوبالت-60 لحساب المعايرة وتقييم القواعد.")
            self._style_banner(self.card_step4_banner, "normal")
            return

        # محاولة التحويل العددي والفحص الفيزيائي
        try:
            p1_c = float(p1_c_txt)
            p2_c = float(p2_c_txt)
            p1_w = float(p1_w_txt)
            p1_t = float(p1_t_txt)
            p2_w = float(p2_w_txt)
            p2_t = float(p2_t_txt)
            p2_a = float(p2_a_txt)
        except ValueError:
            self.lbl_step4_status.setText("يرجى إدخال أرقام صالحة لجميع حقول القمم.")
            self._style_banner(self.card_step4_banner, "error")
            return

        if p1_c <= 0 or p2_c <= 0 or p1_w <= 0 or p1_t <= 0 or p2_w <= 0 or p2_t <= 0 or p2_a < 0:
            self.lbl_step4_status.setText("قيم القنوات والعرض يجب أن تكون موجبة تماماً.")
            self._style_banner(self.card_step4_banner, "error")
            return

        if p2_c <= p1_c:
            self.lbl_step4_status.setText("خطأ منطقي: قناة قمة 1332.5 keV يجب أن تكون أعلى من قناة قمة 1173.2 keV.")
            self._style_banner(self.card_step4_banner, "error")
            return

        if p1_t <= p1_w:
            self.lbl_step4_status.setText("خطأ فيزيائي: عرض FWTM لقمة 1173.2 keV يجب أن يكون أكبر من FWHM.")
            self._style_banner(self.card_step4_banner, "error")
            return

        if p2_t <= p2_w:
            self.lbl_step4_status.setText("خطأ فيزيائي: عرض FWTM لقمة 1332.5 keV يجب أن يكون أكبر من FWHM.")
            self._style_banner(self.card_step4_banner, "error")
            return

        # الحساب عبر دوال core
        try:
            slope_m, intercept_b = calibrate_co60(p1_c, p2_c)
            fwhm_k = fwhm_kev(p2_w, slope_m)
            fwtm_k = fwtm_kev(p2_t, slope_m)
            ff = form_factor(p2_t, p2_w)
        except Exception as e:
            self.lbl_step4_status.setText(f"خطأ في الحسابات الطيفية: {str(e)}")
            self._style_banner(self.card_step4_banner, "error")
            return

        # تحديث الحقول المحسوبة للعرض
        self.edit_slope_m.setText(f"{slope_m:.6f}")
        self.edit_intercept_b.setText(f"{intercept_b:.4f}")
        self.edit_measured_fwhm_kev.setText(f"{fwhm_k:.3f}")
        self.edit_measured_fwtm_kev.setText(f"{fwtm_k:.3f}")
        self.edit_form_factor.setText(f"{ff:.3f}")
        self.edit_net_counts_1332.setText(f"{p2_a:.0f}")

        # تقييم القواعد الأربع R-HPGE-10, 11, 12, 13/14
        rule_messages = []
        is_wait = False
        is_failure = False
        is_oos = False

        # R-HPGE-10: معدل العد وزمن الموت
        if cps_txt and dt_txt:
            try:
                res_10 = eval_r_hpge_10_mca_rates(float(cps_txt), float(dt_txt))
                if res_10.verdict == Verdict.OUT_OF_SPEC:
                    is_oos = True
                    rule_messages.append(f"• {res_10.rule_id}: {res_10.message_ar}")
            except ValueError:
                pass

        # R-HPGE-11: مساحة قمة 1332.5 keV
        res_11 = eval_r_hpge_11_peak_counts(p2_a)
        if res_11.verdict == Verdict.WAIT:
            is_wait = True
            rule_messages.append(f"• {res_11.rule_id}: {res_11.message_ar}")

        # R-HPGE-12: دقة التحليل FWHM
        nom_fwhm = self.detector.get("nominal_fwhm_kev")
        res_12 = eval_r_hpge_12_fwhm(fwhm_k, float(nom_fwhm) if nom_fwhm is not None else None)
        if res_12.verdict == Verdict.FAILURE:
            is_failure = True
            rule_messages.append(f"• {res_12.rule_id}: {res_12.message_ar}")
        elif res_12.verdict == Verdict.OUT_OF_SPEC:
            is_oos = True
            rule_messages.append(f"• {res_12.rule_id}: {res_12.message_ar}")

        # R-HPGE-13/14: معامل الشكل Form Factor
        res_ff = eval_r_hpge_13_14_form_factor(ff)
        if res_ff.verdict == Verdict.OUT_OF_SPEC:
            is_oos = True
            rule_messages.append(f"• {res_ff.rule_id}: {res_ff.message_ar}")

        # تنسيق اللافتة
        if is_wait:
            msg = f"⚠️ حالة انتظار (R-HPGE-11): يلزم تمديد زمن القياس الحي قبل المتابعة للخطوة 5.\n" + "\n".join(rule_messages)
            self.lbl_step4_status.setText(msg)
            self._style_banner(self.card_step4_banner, "warning")
        elif is_failure:
            msg = f"❌ رصد عطل فني في الدقة الطاقية (FAILURE — يُسمح بالعبور لإعادة القياس لاحقاً):\n" + "\n".join(rule_messages)
            self.lbl_step4_status.setText(msg)
            self._style_banner(self.card_step4_banner, "error")
        elif is_oos:
            msg = f"⚠️ خروج عن المواصفات (OUT_OF_SPEC — يُسمح بالعبور مع التوثيق):\n" + "\n".join(rule_messages)
            self.lbl_step4_status.setText(msg)
            self._style_banner(self.card_step4_banner, "warning")
        else:
            self.lbl_step4_status.setText(
                f"✓ جميع مؤشرات MCA والمعايرة والدقة الطاقية ومعامل الشكل مطابقة للمواصفات "
                f"(m = {slope_m:.6f} keV/ch، FWHM = {fwhm_k:.3f} keV، Form Factor = {ff:.3f})."
            )
            self._style_banner(self.card_step4_banner, "success")


    def _update_step_1_live_evaluation(self) -> None:
        if not hasattr(self, "combo_window_status") or not hasattr(self, "card_step1_banner"):
            return

        ws = self.combo_window_status.currentData()
        vac = self.combo_vacuum_intact.currentData()
        wet = self.combo_wet_frozen.currentData()
        c_text = self.edit_cooling_hours.text().strip()

        # 1. فحص الإخفاق الحاسم المبكر عبر check_early_stage_failure و R-HPGE-01 و R-HPGE-04
        early_fail = check_early_stage_failure(self._get_current_inspection_snapshot())
        if early_fail and ("R-HPGE-01" in early_fail or "R-HPGE-04" in early_fail):
            self.lbl_step1_status.setText(
                f"❌ إخفاق حاسم فوري ({format_bidi_message(early_fail)})! "
                "الفحص في حالة رسوب قطعي (FAILURE). يُقفل الانتقال للخطوة التالية نهائياً."
            )
            self._style_banner(self.card_step1_banner, "error")
            return

        if ws is not None:
            res_01 = eval_r_hpge_01_window_integrity(WindowStatus(ws))
            if res_01.verdict == Verdict.FAILURE:
                self.lbl_step1_status.setText(
                    f"❌ إخفاق حاسم فوري (R-HPGE-01): {format_bidi_message(res_01.message_ar)}. "
                    "الفحص في حالة رسوب قطعي (FAILURE). يُقفل الانتقال للخطوة التالية نهائياً."
                )
                self._style_banner(self.card_step1_banner, "error")
                return

        if vac is not None or wet is not None:
            res_04 = eval_r_hpge_04_cryostat_vacuum(
                cryostat_wet_or_frozen=bool(wet == 1) if wet is not None else False,
                vacuum_leak_detected=bool(vac == 0) if vac is not None else False,
            )
            if res_04.verdict == Verdict.FAILURE:
                self.lbl_step1_status.setText(
                    f"❌ إخفاق حاسم فوري (R-HPGE-04): {format_bidi_message(res_04.message_ar)}. "
                    "الفحص في حالة رسوب قطعي (FAILURE). يُمنع رفع الجهد والانتقال للمرحلة التالية."
                )
                self._style_banner(self.card_step1_banner, "error")
                return

        # 2. فحص زمن التبريد عبر R-HPGE-03
        if c_text:
            try:
                cooling_h = float(c_text)
                raw_cert = self.detector.get("cooling_hours_cert")
                cert_h = float(raw_cert) if raw_cert is not None else None
                res_c = eval_r_hpge_03_cooling_time(cooling_h, cert_h)
                if res_c.verdict in (Verdict.BLOCK, Verdict.WAIT):
                    effective_min = max(6.0, cert_h if cert_h is not None else 0.0)
                    rem = max(0.0, effective_min - cooling_h)
                    source_desc = f" حسب شهادة الكاشف {cert_h:.1f} h" if (cert_h is not None and cert_h > 6.0) else ""
                    self.lbl_step1_status.setText(
                        f"⏳ انتظار تبريد وليس عطلاً (R-HPGE-03): الساعات المتبقية {rem:.2f} ساعة "
                        f"(المقاس: {cooling_h:.2f} h، الحد الأدنى المعتمد: {effective_min:.1f} h{source_desc}). يُمنع الانتقال للخطوة 2 حتى اكتمال التبريد."
                    )
                    self._style_banner(self.card_step1_banner, "warning")
                    return
            except ValueError:
                pass

        # 3. فحص الخدش السطحي عبر R-HPGE-02 (تحذير OUT_OF_SPEC)
        if ws == "SCRATCHED":
            res_02 = eval_r_hpge_02_window_scratch(WindowStatus.SCRATCHED)
            self.lbl_step1_status.setText(
                f"⚠️ تحذير خروج عن المواصفات OUT_OF_SPEC (R-HPGE-02): {format_bidi_message(res_02.message_ar)}. "
                "يُسمح بالمتابعة مع توثيق الملاحظة الفنية في التقرير."
            )
            self._style_banner(self.card_step1_banner, "warning")
            return

        if ws == "INTACT" and vac == 1 and wet == 0 and c_text:
            self.lbl_step1_status.setText("✅ المرحلة الأولى مستوفية لكافة الشروط الفنية — يمكنك الانتقال للخطوة التالية.")
            self._style_banner(self.card_step1_banner, "success")
            return

        self.lbl_step1_status.setText("يرجى فحص الحالة المادية لنافذة البريليوم واستقرار الفراغ وزمن التبريد.")
        self._style_banner(self.card_step1_banner, "default")

    def _update_step_2_live_evaluation(self) -> None:
        if not hasattr(self, "card_step2_banner") or not hasattr(self, "combo_applied_polarity"):
            return

        pol = self.combo_applied_polarity.currentData()
        sig = self.combo_preamp_signal.currentData()
        ramp_confirmed = self.chk_ramp_confirm.isChecked()

        # 1. فحص إشارة المضخم الأولي عبر check_early_stage_failure و R-HPGE-08
        early_fail = check_early_stage_failure(self._get_current_inspection_snapshot())
        if early_fail and ("R-HPGE-08" in early_fail or "R-HPGE-06" in early_fail):
            if sig == 0:
                self.lbl_step2_status.setText(
                    f"❌ إخفاق حاسم فوري ({format_bidi_message(early_fail)})! "
                    "الفحص في حالة رسوب قطعي (FAILURE). يُقفل الانتقال للخطوة التالية."
                )
                self._style_banner(self.card_step2_banner, "error")
                return

        if sig is not None:
            res_08 = eval_r_hpge_08_preamp_signal(has_preamp_signal=bool(sig))
            if res_08.verdict == Verdict.FAILURE:
                self.lbl_step2_status.setText(
                    f"❌ إخفاق حاسم فوري (R-HPGE-08): {format_bidi_message(res_08.message_ar)}. "
                    "الفحص في حالة رسوب قطعي (FAILURE). يُقفل الانتقال للخطوة التالية."
                )
                self._style_banner(self.card_step2_banner, "error")
                return

        # 2. فحص قطبية الجهد العالي عبر R-HPGE-06
        if pol is not None:
            nom_pol = self.detector.get("voltage_polarity")
            cert_pol = self.detector.get("certified_polarity")
            try:
                p_enum = VoltagePolarity(pol)
                n_enum = VoltagePolarity(nom_pol) if nom_pol else None
                c_enum = VoltagePolarity(cert_pol) if cert_pol else None
                res_pol = eval_r_hpge_06_hv_polarity(p_enum, n_enum, c_enum)
                if res_pol.verdict == Verdict.BLOCK:
                    self.lbl_step2_status.setText(
                        f"🛑 إخفاق أمان مانع (R-HPGE-06): {res_pol.message_ar}"
                    )
                    self._style_banner(self.card_step2_banner, "error")
                    return
            except Exception:
                pass

        # 3. فحص معدل رفع الجهد عبر R-HPGE-07
        r_text = self.edit_ramp_rate.text().strip()
        if r_text:
            try:
                rate_val = float(r_text)
                max_rate = self.detector.get("hv_ramp_max_v_per_s") or 10.0
                ref_txt = self.detector.get("hv_ramp_reference")
                res_ramp = eval_r_hpge_07_hv_ramp_rate(rate_val, max_rate, ref_txt)
                if res_ramp.verdict == Verdict.BLOCK:
                    self.lbl_step2_status.setText(
                        f"🛑 إخفاق أمان مانع (R-HPGE-07): {res_ramp.message_ar}"
                    )
                    self._style_banner(self.card_step2_banner, "error")
                    return
            except ValueError:
                pass

        # 4. فحص جهود سلة NIM القياسية عبر R-HPGE-05 (OUT_OF_SPEC تحذير يسمح بالمتابعة ولا يحظر)
        nim_texts = [
            self.edit_v_pos_24.text().strip(),
            self.edit_v_neg_24.text().strip(),
            self.edit_v_pos_12.text().strip(),
            self.edit_v_neg_12.text().strip(),
        ]
        res_05: Optional[RuleResult] = None
        if all(nim_texts):
            try:
                res_05 = eval_r_hpge_05_nim_voltages(
                    float(nim_texts[0]),
                    float(nim_texts[1]),
                    float(nim_texts[2]),
                    float(nim_texts[3]),
                )
            except ValueError:
                pass

        if res_05 and res_05.verdict == Verdict.OUT_OF_SPEC:
            extra = ""
            if not ramp_confirmed and (self.combo_ramp_source.currentData() or r_text):
                extra = " — يرجى أيضاً تأكيد خانة الإقرار لمصدر ومعدل رفع الجهد."
            self.lbl_step2_status.setText(
                f"⚠️ تحذير خروج عن المواصفات OUT_OF_SPEC (R-HPGE-05): {format_bidi_message(res_05.message_ar)}{extra}"
            )
            self._style_banner(self.card_step2_banner, "warning")
            return

        # 5. تنبيه عدم تأكيد الإقرار
        if not ramp_confirmed and (self.combo_ramp_source.currentData() or r_text):
            self.lbl_step2_status.setText("⚠️ يرجى تأكيد خانة الإقرار الصريحة لمصدر ومعدل رفع الجهد قبل المتابعة.")
            self._style_banner(self.card_step2_banner, "warning")
            return

        # 6. إذا اكتملت كافة حقول الخطوة 2 وكانت مطابقة
        v_text = self.edit_applied_voltage.text().strip()
        if (
            all(nim_texts)
            and pol is not None
            and v_text
            and self.combo_ramp_source.currentData()
            and r_text
            and ramp_confirmed
            and sig == 1
        ):
            self.lbl_step2_status.setText("✅ المرحلة الثانية مستوفية لكافة الشروط الفنية — يمكنك الانتقال للخطوة التالية.")
            self._style_banner(self.card_step2_banner, "success")
            return

        self.lbl_step2_status.setText("يرجى قياس جهود سلة NIM، وتأكيد قطبية الجهد ومعدل الرفع وإشارة المضخم الأولي.")
        self._style_banner(self.card_step2_banner, "default")

    def _style_banner(self, card: QFrame, level: str) -> None:
        card_id = card.objectName() or "card_banner"
        mode = get_mode()
        if level == "error":
            if mode == "dark":
                card.setStyleSheet(f"#{card_id} {{ background-color: rgba(179, 38, 30, 0.25); border: 1px solid #F87171; border-radius: 6px; }}")
            else:
                card.setStyleSheet(f"#{card_id} {{ background-color: #FDE8E8; border: 1px solid {COLORS['error']}; border-radius: 6px; }}")
        elif level == "warning":
            if mode == "dark":
                card.setStyleSheet(f"#{card_id} {{ background-color: rgba(179, 92, 0, 0.25); border: 1px solid {COLORS['dark_orange']}; border-radius: 6px; }}")
            else:
                card.setStyleSheet(f"#{card_id} {{ background-color: #FFF4E5; border: 1px solid {COLORS['warning']}; border-radius: 6px; }}")
        elif level == "success":
            if mode == "dark":
                card.setStyleSheet(f"#{card_id} {{ background-color: rgba(30, 122, 76, 0.25); border: 1px solid #4ADE80; border-radius: 6px; }}")
            else:
                card.setStyleSheet(f"#{card_id} {{ background-color: #E6F4EA; border: 1px solid {COLORS['success']}; border-radius: 6px; }}")
        else:
            card.setStyleSheet(f"#{card_id} {{ background-color: transparent; border: none; }}")

    # -----------------------------------------------------------------------
    # التحقق من صحة الخطوات قبل الانتقال
    # -----------------------------------------------------------------------

    def _validate_current_step(self) -> Tuple[bool, Optional[str]]:
        self._last_invalid_widget = None
        idx = self.stack.currentIndex()

        if idx == 0:
            # الخطوة 1
            ws = self.combo_window_status.currentData()
            c_text = self.edit_cooling_hours.text().strip()
            vac = self.combo_vacuum_intact.currentData()
            wet = self.combo_wet_frozen.currentData()

            # التحقق من تعبئة الحقول الإلزامية بلا افتراضات
            if ws is None:
                self._last_invalid_widget = self.combo_window_status
                return False, "حقل (حالة نافذة البريليوم) إلزامي بلا افتراض مسبق."
            if not c_text:
                self._last_invalid_widget = self.edit_cooling_hours
                return False, "حقل (مدة التبريد بالنيتروجين السائل) إلزامي بلا افتراض مسبق."
            try:
                cooling_h = float(c_text)
            except ValueError:
                self._last_invalid_widget = self.edit_cooling_hours
                return False, "قيمة مدة التبريد يجب أن تكون رقماً صالحاً."
            if vac is None:
                self._last_invalid_widget = self.combo_vacuum_intact
                return False, "حقل (إحكام فراغ الحاوية) إلزامي (نعم / لا)."
            if wet is None:
                self._last_invalid_widget = self.combo_wet_frozen
                return False, "حقل (رصد رطوبة أو تجمد) إلزامي (نعم / لا)."

            # فحص حالات الإخفاق الحاسم عبر check_early_stage_failure و R-HPGE-01 و R-HPGE-04
            early_fail = check_early_stage_failure(self._get_current_inspection_snapshot())
            if early_fail and ("R-HPGE-01" in early_fail or "R-HPGE-04" in early_fail):
                self.save_draft(show_feedback=False)
                self._last_invalid_widget = self.combo_window_status if "R-HPGE-01" in early_fail else (
                    self.combo_vacuum_intact if vac == 0 else self.combo_wet_frozen
                )
                return False, f"إخفاق فوري ({format_bidi_message(early_fail)})! يُمنع الانتقال للخطوة 2."

            res_01 = eval_r_hpge_01_window_integrity(WindowStatus(ws))
            if res_01.verdict == Verdict.FAILURE:
                self.save_draft(show_feedback=False)
                self._last_invalid_widget = self.combo_window_status
                return False, f"إخفاق فوري (R-HPGE-01): {format_bidi_message(res_01.message_ar)} يُمنع الانتقال للخطوة 2."

            res_04 = eval_r_hpge_04_cryostat_vacuum(
                cryostat_wet_or_frozen=bool(wet == 1),
                vacuum_leak_detected=bool(vac == 0),
            )
            if res_04.verdict == Verdict.FAILURE:
                self.save_draft(show_feedback=False)
                self._last_invalid_widget = self.combo_vacuum_intact if vac == 0 else self.combo_wet_frozen
                return False, f"إخفاق فوري (R-HPGE-04): {format_bidi_message(res_04.message_ar)} يُمنع الانتقال للخطوة 2."

            # فحص زمن التبريد عبر R-HPGE-03
            raw_cert = self.detector.get("cooling_hours_cert")
            cert_h = float(raw_cert) if raw_cert is not None else None
            res_c = eval_r_hpge_03_cooling_time(cooling_h, cert_h)
            if res_c.verdict in (Verdict.BLOCK, Verdict.WAIT):
                effective_min = max(6.0, cert_h if cert_h is not None else 0.0)
                rem = max(0.0, effective_min - cooling_h)
                source_desc = f" حسب شهادة الكاشف {cert_h:.1f} h" if (cert_h is not None and cert_h > 6.0) else ""
                self._last_invalid_widget = self.edit_cooling_hours
                return False, f"انتظار تبريد وليس عطلاً (R-HPGE-03): يلزم الانتظار {rem:.2f} ساعة إضافية (المقاس: {cooling_h:.2f} h، الحد الأدنى المعتمد: {effective_min:.1f} h{source_desc})."

            return True, None

        elif idx == 1:
            # الخطوة 2
            # 1. جهود NIM الإلزامية
            nim_vals = []
            for name, edit, label in [
                ("v_pos_24", self.edit_v_pos_24, "+24 V"),
                ("v_neg_24", self.edit_v_neg_24, "-24 V"),
                ("v_pos_12", self.edit_v_pos_12, "+12 V"),
                ("v_neg_12", self.edit_v_neg_12, "-12 V"),
            ]:
                txt = edit.text().strip()
                if not txt:
                    self._last_invalid_widget = edit
                    return False, f"جهد سلة NIM ({label}) إلزامي."
                try:
                    nim_vals.append(float(txt))
                except ValueError:
                    self._last_invalid_widget = edit
                    return False, f"قيمة جهد ({label}) غير صالحة."

            # تقييم R-HPGE-05 (OUT_OF_SPEC يسمح بالمتابعة مع تحذير، ولا يحظر الانتقال)
            res_05 = eval_r_hpge_05_nim_voltages(nim_vals[0], nim_vals[1], nim_vals[2], nim_vals[3])
            if res_05.verdict == Verdict.BLOCK:
                self._last_invalid_widget = self.edit_v_pos_24
                return False, f"إخفاق مانع (R-HPGE-05): {format_bidi_message(res_05.message_ar)}"

            # 2. القطبية المطبقة
            pol = self.combo_applied_polarity.currentData()
            if pol is None:
                self._last_invalid_widget = self.combo_applied_polarity
                return False, "قطبية الجهد العالي المطبق إلزامية بلا اختيار مسبق."

            nom_pol = self.detector.get("voltage_polarity")
            cert_pol = self.detector.get("certified_polarity")
            try:
                res_pol = eval_r_hpge_06_hv_polarity(
                    VoltagePolarity(pol),
                    VoltagePolarity(nom_pol) if nom_pol else None,
                    VoltagePolarity(cert_pol) if cert_pol else None,
                )
                if res_pol.verdict == Verdict.BLOCK:
                    self.save_draft(show_feedback=False)
                    self._last_invalid_widget = self.combo_applied_polarity
                    return False, f"إخفاق مانع (R-HPGE-06): {res_pol.message_ar}"
            except Exception:
                pass

            # 3. الجهد العالي المطبق
            v_text = self.edit_applied_voltage.text().strip()
            if not v_text:
                self._last_invalid_widget = self.edit_applied_voltage
                return False, "حقل الجهد العالي المطبق إلزامي."
            try:
                float(v_text)
            except ValueError:
                self._last_invalid_widget = self.edit_applied_voltage
                return False, "قيمة الجهد العالي المطبق غير صالحة."

            # 4. مصدر ومعدل رفع الجهد والتأكيد الصريح في الجلسة الحالية
            ramp_src = self.combo_ramp_source.currentData()
            if ramp_src is None:
                self._last_invalid_widget = self.combo_ramp_source
                return False, "مصدر رفع الجهد إلزامي بلا اختيار مسبق."

            r_text = self.edit_ramp_rate.text().strip()
            if not r_text:
                self._last_invalid_widget = self.edit_ramp_rate
                return False, "معدل رفع الجهد (V/s) إلزامي بلا قيمة افتراضية."
            try:
                rate_val = float(r_text)
            except ValueError:
                self._last_invalid_widget = self.edit_ramp_rate
                return False, "قيمة معدل رفع الجهد غير صالحة."

            if not self.chk_ramp_confirm.isChecked():
                self._last_invalid_widget = self.chk_ramp_confirm
                return False, "يجب تحديد خانة الإقرار: أؤكد أن هذا المصدر والمعدل هما ما طُبِّق فعلاً."

            max_rate = self.detector.get("hv_ramp_max_v_per_s") or 10.0
            ref_txt = self.detector.get("hv_ramp_reference")
            res_ramp = eval_r_hpge_07_hv_ramp_rate(rate_val, max_rate, ref_txt)
            if res_ramp.verdict == Verdict.BLOCK:
                self._last_invalid_widget = self.edit_ramp_rate
                return False, f"إخفاق مانع (R-HPGE-07): {res_ramp.message_ar}"

            # 5. إشارة المضخم الأولي عبر R-HPGE-08
            sig = self.combo_preamp_signal.currentData()
            if sig is None:
                self._last_invalid_widget = self.combo_preamp_signal
                return False, "حالة إشارة خرج المضخم الأولي إلزامية (نعم / لا)."
            res_sig = eval_r_hpge_08_preamp_signal(has_preamp_signal=bool(sig))
            if res_sig.verdict == Verdict.FAILURE:
                self.save_draft(show_feedback=False)
                self._last_invalid_widget = self.combo_preamp_signal
                return False, f"إخفاق فوري (R-HPGE-08): {res_sig.message_ar} يُمنع الانتقال للخطوة التالية."

            return True, None

        elif idx == 2:
            # الخطوة 3: المضخم الرئيسي
            pz = self.combo_pz_distortion.currentData()
            if pz is None:
                self._last_invalid_widget = self.combo_pz_distortion
                return False, "حقل (تشوه نبضة تعويض القطب والصفر P/Z) إلزامي بلا اختيار مسبق."

            # التحقق من صحة الحقول الرقمية الاختيارية في حال تعبئتها
            for edit, name in [
                (self.edit_shaping_time, "زمن التشكيل Shaping Time"),
                (self.edit_coarse_gain, "الكسب الخشن Coarse Gain"),
                (self.edit_fine_gain, "الكسب الدقيق Fine Gain"),
                (self.edit_amp_output_amplitude, "سعة نبضة الخرج Amplitude"),
            ]:
                t = edit.text().strip()
                if t:
                    try:
                        v = float(t)
                        if v <= 0:
                            self._last_invalid_widget = edit
                            return False, f"قيمة حقل ({name}) يجب أن تكون قيمة موجبة تماماً."
                    except ValueError:
                        self._last_invalid_widget = edit
                        return False, f"قيمة حقل ({name}) غير صالحة."

            return True, None

        elif idx == 3:
            # الخطوة 4: MCA ومعايرة الطاقة
            cps_txt = self.edit_total_count_rate_cps.text().strip()
            if not cps_txt:
                self._last_invalid_widget = self.edit_total_count_rate_cps
                return False, "حقل (إجمالي معدل النبضات MCA) إلزامي."
            try:
                cps = float(cps_txt)
                if cps < 0:
                    self._last_invalid_widget = self.edit_total_count_rate_cps
                    return False, "قيمة (إجمالي معدل النبضات MCA) لا يجوز أن تكون سالبة."
            except ValueError:
                self._last_invalid_widget = self.edit_total_count_rate_cps
                return False, "قيمة (إجمالي معدل النبضات MCA) غير صالحة."

            dt_txt = self.edit_dead_time_pct.text().strip()
            if not dt_txt:
                self._last_invalid_widget = self.edit_dead_time_pct
                return False, "حقل (نسبة زمن الموت MCA) إلزامي."
            try:
                dt = float(dt_txt)
                if dt < 0:
                    self._last_invalid_widget = self.edit_dead_time_pct
                    return False, "قيمة (نسبة زمن الموت MCA) لا يجوز أن تكون سالبة."
            except ValueError:
                self._last_invalid_widget = self.edit_dead_time_pct
                return False, "قيمة (نسبة زمن الموت MCA) غير صالحة."

            live_txt = self.edit_live_time_sec.text().strip()
            if not live_txt:
                self._last_invalid_widget = self.edit_live_time_sec
                return False, "حقل (زمن القياس الحي Live Time) إلزامي."
            try:
                lt = float(live_txt)
                if lt <= 0:
                    self._last_invalid_widget = self.edit_live_time_sec
                    return False, "قيمة (زمن القياس الحي Live Time) يجب أن تكون موجبة تماماً."
            except ValueError:
                self._last_invalid_widget = self.edit_live_time_sec
                return False, "قيمة (زمن القياس الحي Live Time) غير صالحة."

            # مدخلات القمة الأولى (1173.2 keV)
            p1_c_txt = self.edit_p1_centroid.text().strip()
            if not p1_c_txt:
                self._last_invalid_widget = self.edit_p1_centroid
                return False, "حقل (موقع القناة المركزية لقمة 1173.2 keV) إلزامي."
            try:
                p1_c = float(p1_c_txt)
                if p1_c <= 0:
                    self._last_invalid_widget = self.edit_p1_centroid
                    return False, "قناة قمة 1173.2 keV يجب أن تكون موجبة تماماً."
            except ValueError:
                self._last_invalid_widget = self.edit_p1_centroid
                return False, "قيمة قناة قمة 1173.2 keV غير صالحة."

            p1_w_txt = self.edit_p1_fwhm_ch.text().strip()
            if not p1_w_txt:
                self._last_invalid_widget = self.edit_p1_fwhm_ch
                return False, "حقل (عرض FWHM لقمة 1173.2 keV) إلزامي."
            try:
                p1_w = float(p1_w_txt)
                if p1_w <= 0:
                    self._last_invalid_widget = self.edit_p1_fwhm_ch
                    return False, "عرض FWHM لقمة 1173.2 keV يجب أن يكون موجباً تماماً."
            except ValueError:
                self._last_invalid_widget = self.edit_p1_fwhm_ch
                return False, "قيمة عرض FWHM لقمة 1173.2 keV غير صالحة."

            p1_t_txt = self.edit_p1_fwtm_ch.text().strip()
            if not p1_t_txt:
                self._last_invalid_widget = self.edit_p1_fwtm_ch
                return False, "حقل (عرض FWTM لقمة 1173.2 keV) إلزامي."
            try:
                p1_t = float(p1_t_txt)
                if p1_t <= 0:
                    self._last_invalid_widget = self.edit_p1_fwtm_ch
                    return False, "عرض FWTM لقمة 1173.2 keV يجب أن يكون موجباً تماماً."
            except ValueError:
                self._last_invalid_widget = self.edit_p1_fwtm_ch
                return False, "قيمة عرض FWTM لقمة 1173.2 keV غير صالحة."

            p1_a_txt = self.edit_p1_net_area.text().strip()
            if not p1_a_txt:
                self._last_invalid_widget = self.edit_p1_net_area
                return False, "حقل (المساحة الصافية لقمة 1173.2 keV) إلزامي."
            try:
                p1_a = float(p1_a_txt)
                if p1_a < 0:
                    self._last_invalid_widget = self.edit_p1_net_area
                    return False, "المساحة الصافية لقمة 1173.2 keV لا يجوز أن تكون سالبة."
            except ValueError:
                self._last_invalid_widget = self.edit_p1_net_area
                return False, "قيمة المساحة الصافية لقمة 1173.2 keV غير صالحة."

            if p1_t <= p1_w:
                self._last_invalid_widget = self.edit_p1_fwtm_ch
                return False, "خطأ فيزيائي: في قمة 1173.2 keV يجب أن يكون عرض عُشر الارتفاع (FWTM) أكبر من عرض نصف الارتفاع (FWHM)."

            # مدخلات القمة الثانية (1332.5 keV)
            p2_c_txt = self.edit_p2_centroid.text().strip()
            if not p2_c_txt:
                self._last_invalid_widget = self.edit_p2_centroid
                return False, "حقل (موقع القناة المركزية لقمة 1332.5 keV) إلزامي."
            try:
                p2_c = float(p2_c_txt)
                if p2_c <= 0:
                    self._last_invalid_widget = self.edit_p2_centroid
                    return False, "قناة قمة 1332.5 keV يجب أن تكون موجبة تماماً."
            except ValueError:
                self._last_invalid_widget = self.edit_p2_centroid
                return False, "قيمة قناة قمة 1332.5 keV غير صالحة."

            p2_w_txt = self.edit_p2_fwhm_ch.text().strip()
            if not p2_w_txt:
                self._last_invalid_widget = self.edit_p2_fwhm_ch
                return False, "حقل (عرض FWHM لقمة 1332.5 keV) إلزامي."
            try:
                p2_w = float(p2_w_txt)
                if p2_w <= 0:
                    self._last_invalid_widget = self.edit_p2_fwhm_ch
                    return False, "عرض FWHM لقمة 1332.5 keV يجب أن يكون موجباً تماماً."
            except ValueError:
                self._last_invalid_widget = self.edit_p2_fwhm_ch
                return False, "قيمة عرض FWHM لقمة 1332.5 keV غير صالحة."

            p2_t_txt = self.edit_p2_fwtm_ch.text().strip()
            if not p2_t_txt:
                self._last_invalid_widget = self.edit_p2_fwtm_ch
                return False, "حقل (عرض FWTM لقمة 1332.5 keV) إلزامي."
            try:
                p2_t = float(p2_t_txt)
                if p2_t <= 0:
                    self._last_invalid_widget = self.edit_p2_fwtm_ch
                    return False, "عرض FWTM لقمة 1332.5 keV يجب أن يكون موجباً تماماً."
            except ValueError:
                self._last_invalid_widget = self.edit_p2_fwtm_ch
                return False, "قيمة عرض FWTM لقمة 1332.5 keV غير صالحة."

            p2_a_txt = self.edit_p2_net_area.text().strip()
            if not p2_a_txt:
                self._last_invalid_widget = self.edit_p2_net_area
                return False, "حقل (المساحة الصافية لقمة 1332.5 keV) إلزامي."
            try:
                p2_a = float(p2_a_txt)
                if p2_a < 0:
                    self._last_invalid_widget = self.edit_p2_net_area
                    return False, "المساحة الصافية لقمة 1332.5 keV لا يجوز أن تكون سالبة."
            except ValueError:
                self._last_invalid_widget = self.edit_p2_net_area
                return False, "قيمة المساحة الصافية لقمة 1332.5 keV غير صالحة."

            if p2_t <= p2_w:
                self._last_invalid_widget = self.edit_p2_fwtm_ch
                return False, "خطأ فيزيائي: في قمة 1332.5 keV يجب أن يكون عرض عُشر الارتفاع (FWTM) أكبر من عرض نصف الارتفاع (FWHM)."

            if p2_c <= p1_c:
                self._last_invalid_widget = self.edit_p2_centroid
                return False, "خطأ منطقي: قناة قمة 1332.5 keV يجب أن تكون أكبر من قناة قمة 1173.2 keV."

            # الحساب عبر calibrate_co60
            try:
                slope_m, intercept_b = calibrate_co60(p1_c, p2_c)
            except ValueError as e:
                self._last_invalid_widget = self.edit_p2_centroid
                return False, str(e)

            # فحص R-HPGE-11: المساحة الصافية < 10000 تمنع الانتقال للخطوة 5
            res_11 = eval_r_hpge_11_peak_counts(p2_a)
            if res_11.verdict == Verdict.WAIT:
                self._last_invalid_widget = self.edit_p2_net_area
                return False, f"حالة انتظار (R-HPGE-11): المساحة الصافية لقمة 1332.5 keV ({p2_a:.0f} نبضة) أقل من 10,000 نبضة؛ يلزم تمديد زمن القياس الحي لتقليل الشك الإحصائي (< 1%) قبل المتابعة."

            # قواعد OUT_OF_SPEC و FAILURE لا تمنع العبور
            return True, None

        return True, None

    # -----------------------------------------------------------------------
    # حفظ المسودة الجزئي واستدعاء خدمة الفحص
    # -----------------------------------------------------------------------

    def save_draft(self, show_feedback: bool = True) -> bool:
        """حفظ مدخلات الفحص كمسودة جزئية دون فرض حقول المراحل اللاحقة."""
        if not self.is_editable:
            return False

        payload: Dict[str, Any] = {}

        # 1. مدخلات المرحلة الأولى
        ws = self.combo_window_status.currentData()
        if ws is not None:
            payload["beryllium_window_status"] = ws

        c_text = self.edit_cooling_hours.text().strip()
        if c_text:
            try:
                payload["cooling_hours"] = float(c_text)
            except ValueError:
                if show_feedback:
                    QMessageBox.warning(self, "تنبيه قبل المتابعة", "قيمة حقل (مدة التبريد بالنيتروجين السائل) غير صالحة.")
                    self._scroll_to_and_focus_widget(self.edit_cooling_hours)
                return False

        vac = self.combo_vacuum_intact.currentData()
        if vac is not None:
            payload["vacuum_intact"] = vac

        wet = self.combo_wet_frozen.currentData()
        if wet is not None:
            payload["cryostat_wet_or_frozen"] = wet

        # اشتقاق cryostat_intact صراحة
        if vac is not None and wet is not None:
            payload["cryostat_intact"] = 1 if (vac == 1 and wet == 0 and ws != "BROKEN") else 0

        # 2. مدخلات المرحلة الثانية
        nim_fields = [
            ("v_pos_24", self.edit_v_pos_24, "جهد سلة NIM (+24 V)"),
            ("v_neg_24", self.edit_v_neg_24, "جهد سلة NIM (-24 V)"),
            ("v_pos_12", self.edit_v_pos_12, "جهد سلة NIM (+12 V)"),
            ("v_neg_12", self.edit_v_neg_12, "جهد سلة NIM (-12 V)"),
            ("v_pos_6", self.edit_v_pos_6, "جهد سلة NIM (+6 V)"),
            ("v_neg_6", self.edit_v_neg_6, "جهد سلة NIM (-6 V)"),
        ]
        for k, edit, label in nim_fields:
            val_txt = edit.text().strip()
            if val_txt:
                try:
                    payload[k] = float(val_txt)
                except ValueError:
                    if show_feedback:
                        QMessageBox.warning(self, "تنبيه قبل المتابعة", f"القيمة المدخلة في حقل '{label}' غير صالحة.")
                        self._scroll_to_and_focus_widget(edit)
                    return False

        pol = self.combo_applied_polarity.currentData()
        if pol is not None:
            payload["applied_polarity"] = pol

        v_text = self.edit_applied_voltage.text().strip()
        if v_text:
            try:
                payload["applied_voltage"] = float(v_text)
            except ValueError:
                if show_feedback:
                    QMessageBox.warning(self, "تنبيه قبل المتابعة", "قيمة حقل (الجهد العالي المطبق) غير صالحة.")
                    self._scroll_to_and_focus_widget(self.edit_applied_voltage)
                return False

        sig = self.combo_preamp_signal.currentData()
        if sig is not None:
            payload["has_preamp_signal"] = sig

        # شرط الأمان الحرج لمعطيات رفع الجهد:
        if self.chk_ramp_confirm.isChecked():
            r_src = self.combo_ramp_source.currentData()
            if r_src is not None:
                payload["hv_ramp_source"] = r_src

            r_rate_txt = self.edit_ramp_rate.text().strip()
            if r_rate_txt:
                try:
                    payload["hv_ramp_rate_v_per_s"] = float(r_rate_txt)
                except ValueError:
                    if show_feedback:
                        QMessageBox.warning(self, "تنبيه قبل المتابعة", "قيمة حقل (معدل رفع الجهد) غير صالحة.")
                        self._scroll_to_and_focus_widget(self.edit_ramp_rate)
                    return False
        else:
            # لم يؤشر على خانة التأكيد في الجلسة الحالية:
            # إذا كان في القاعدة صف مخزّن سابقاً، نرسل القيمتين المخزّنتين كما هما للحفاظ عليهما
            if self.saved_measurements.get("hv_ramp_source") is not None:
                payload["hv_ramp_source"] = self.saved_measurements["hv_ramp_source"]
            if self.saved_measurements.get("hv_ramp_rate_v_per_s") is not None:
                payload["hv_ramp_rate_v_per_s"] = self.saved_measurements["hv_ramp_rate_v_per_s"]

        # 3. مدخلات المرحلة الثالثة (المضخم الرئيسي)
        for k, edit, label in [
            ("shaping_time_us", self.edit_shaping_time, "زمن التشكيل"),
            ("coarse_gain", self.edit_coarse_gain, "الكسب الخشن"),
            ("fine_gain", self.edit_fine_gain, "الكسب الدقيق"),
            ("amp_output_amplitude", self.edit_amp_output_amplitude, "سعة نبضة الخرج"),
        ]:
            val_txt = edit.text().strip()
            if val_txt:
                try:
                    num_val = float(val_txt)
                    if num_val <= 0:
                        if show_feedback:
                            QMessageBox.warning(self, "تنبيه قبل المتابعة", f"قيمة حقل '{label}' يجب أن تكون موجبة تماماً.")
                            self._scroll_to_and_focus_widget(edit)
                        return False
                    payload[k] = num_val
                except ValueError:
                    if show_feedback:
                        QMessageBox.warning(self, "تنبيه قبل المتابعة", f"القيمة المدخلة في حقل '{label}' غير صالحة.")
                        self._scroll_to_and_focus_widget(edit)
                    return False

        if self.edit_baseline_restorer.text().strip():
            payload["baseline_restorer"] = self.edit_baseline_restorer.text().strip()
        if self.edit_pole_zero_setting.text().strip():
            payload["pole_zero_setting"] = self.edit_pole_zero_setting.text().strip()

        pz = self.combo_pz_distortion.currentData()
        if pz is not None:
            payload["has_pz_distortion"] = int(pz)

        # 4. مدخلات المرحلة الرابعة (MCA ومعايرة الطاقة)
        for k, edit, label in [
            ("total_count_rate_cps", self.edit_total_count_rate_cps, "إجمالي معدل النبضات MCA"),
            ("dead_time_pct", self.edit_dead_time_pct, "نسبة زمن الموت MCA"),
        ]:
            val_txt = edit.text().strip()
            if val_txt:
                try:
                    num_val = float(val_txt)
                    if num_val < 0:
                        if show_feedback:
                            QMessageBox.warning(self, "تنبيه قبل المتابعة", f"قيمة حقل '{label}' لا يجوز أن تكون سالبة.")
                            self._scroll_to_and_focus_widget(edit)
                        return False
                    payload[k] = num_val
                except ValueError:
                    if show_feedback:
                        QMessageBox.warning(self, "تنبيه قبل المتابعة", f"القيمة المدخلة في حقل '{label}' غير صالحة.")
                        self._scroll_to_and_focus_widget(edit)
                    return False

        live_txt = self.edit_live_time_sec.text().strip()
        live_val = None
        if live_txt:
            try:
                live_val = float(live_txt)
                if live_val <= 0:
                    if show_feedback:
                        QMessageBox.warning(self, "تنبيه قبل المتابعة", "قيمة حقل (زمن القياس الحي) يجب أن تكون موجبة تماماً.")
                        self._scroll_to_and_focus_widget(self.edit_live_time_sec)
                    return False
                payload["live_time_sec"] = live_val
            except ValueError:
                if show_feedback:
                    QMessageBox.warning(self, "تنبيه قبل المتابعة", "قيمة حقل (زمن القياس الحي) غير صالحة.")
                    self._scroll_to_and_focus_widget(self.edit_live_time_sec)
                return False

        # قراءة قيم القمتين
        p1_c_txt = self.edit_p1_centroid.text().strip()
        p1_w_txt = self.edit_p1_fwhm_ch.text().strip()
        p1_t_txt = self.edit_p1_fwtm_ch.text().strip()
        p1_a_txt = self.edit_p1_net_area.text().strip()

        p2_c_txt = self.edit_p2_centroid.text().strip()
        p2_w_txt = self.edit_p2_fwhm_ch.text().strip()
        p2_t_txt = self.edit_p2_fwtm_ch.text().strip()
        p2_a_txt = self.edit_p2_net_area.text().strip()

        p1_c = p1_w = p1_t = p1_a = None
        p2_c = p2_w = p2_t = p2_a = None

        if p1_c_txt or p1_w_txt or p1_t_txt or p1_a_txt:
            try:
                if p1_c_txt:
                    p1_c = float(p1_c_txt)
                    if p1_c <= 0:
                        raise ValueError("قناة قمة 1173.2 keV يجب أن تكون موجبة تماماً.")
                if p1_w_txt:
                    p1_w = float(p1_w_txt)
                    if p1_w <= 0:
                        raise ValueError("عرض FWHM لقمة 1173.2 keV يجب أن يكون موجباً تماماً.")
                if p1_t_txt:
                    p1_t = float(p1_t_txt)
                    if p1_t <= 0:
                        raise ValueError("عرض FWTM لقمة 1173.2 keV يجب أن يكون موجباً تماماً.")
                if p1_a_txt:
                    p1_a = float(p1_a_txt)
                    if p1_a < 0:
                        raise ValueError("المساحة الصافية لقمة 1173.2 keV لا يجوز أن تكون سالبة.")
                if p1_w is not None and p1_t is not None and p1_t <= p1_w:
                    raise ValueError("خطأ في قمة 1173.2 keV: عرض عُشر الارتفاع (FWTM) يجب أن يكون أكبر من عرض نصف الارتفاع (FWHM).")
            except ValueError as ve:
                if show_feedback:
                    w_focus = self.edit_p1_fwtm_ch if "FWTM" in str(ve) else (
                        self.edit_p1_centroid if "قناة" in str(ve) else (
                            self.edit_p1_fwhm_ch if "FWHM" in str(ve) else self.edit_p1_net_area
                        )
                    )
                    QMessageBox.warning(self, "تنبيه قبل المتابعة", str(ve))
                    self._scroll_to_and_focus_widget(w_focus)
                return False

        if p2_c_txt or p2_w_txt or p2_t_txt or p2_a_txt:
            try:
                if p2_c_txt:
                    p2_c = float(p2_c_txt)
                    if p2_c <= 0:
                        raise ValueError("قناة قمة 1332.5 keV يجب أن تكون موجبة تماماً.")
                if p2_w_txt:
                    p2_w = float(p2_w_txt)
                    if p2_w <= 0:
                        raise ValueError("عرض FWHM لقمة 1332.5 keV يجب أن يكون موجباً تماماً.")
                if p2_t_txt:
                    p2_t = float(p2_t_txt)
                    if p2_t <= 0:
                        raise ValueError("عرض FWTM لقمة 1332.5 keV يجب أن يكون موجباً تماماً.")
                if p2_a_txt:
                    p2_a = float(p2_a_txt)
                    if p2_a < 0:
                        raise ValueError("المساحة الصافية لقمة 1332.5 keV لا يجوز أن تكون سالبة.")
                if p2_w is not None and p2_t is not None and p2_t <= p2_w:
                    raise ValueError("خطأ في قمة 1332.5 keV: عرض عُشر الارتفاع (FWTM) يجب أن يكون أكبر من عرض نصف الارتفاع (FWHM).")
            except ValueError as ve:
                if show_feedback:
                    w_focus = self.edit_p2_fwtm_ch if "FWTM" in str(ve) else (
                        self.edit_p2_centroid if "قناة" in str(ve) else (
                            self.edit_p2_fwhm_ch if "FWHM" in str(ve) else self.edit_p2_net_area
                        )
                    )
                    QMessageBox.warning(self, "تنبيه قبل المتابعة", str(ve))
                    self._scroll_to_and_focus_widget(w_focus)
                return False

        if p1_c is not None and p2_c is not None and p2_c <= p1_c:
            err = "خطأ منطقي: قناة قمة 1332.5 keV يجب أن تكون أكبر من قناة قمة 1173.2 keV."
            if show_feedback:
                QMessageBox.warning(self, "تنبيه قبل المتابعة", err)
                self._scroll_to_and_focus_widget(self.edit_p2_centroid)
            return False

        # الحسابات الطيفية لحفظها في hpge_measurements
        slope_m = None
        if p1_c is not None and p2_c is not None:
            try:
                slope_m, intercept_b = calibrate_co60(p1_c, p2_c)
                payload["calibration_slope_m"] = slope_m
                payload["calibration_intercept_b"] = intercept_b
                if p2_w is not None:
                    payload["measured_fwhm_kev"] = fwhm_kev(p2_w, slope_m)
                if p2_t is not None:
                    payload["measured_fwtm_kev"] = fwtm_kev(p2_t, slope_m)
                if p2_w is not None and p2_t is not None:
                    payload["form_factor"] = form_factor(p2_t, p2_w)
            except Exception:
                pass

        if p2_a is not None:
            payload["net_counts_1332"] = p2_a

        # بناء قائمة القمم وحفظها عبر service.save_peaks
        peaks_to_save: List[Dict[str, Any]] = []

        if any(x is not None for x in (p1_c, p1_w, p1_t, p1_a)):
            p1_dict = {
                "detector_type": "HPGE",
                "peak_name": "Co-60 (1173.2 keV)",
                "energy_reference_kev": 1173.2,
                "centroid_channel": p1_c,
                "fwhm_channels": p1_w,
                "fwhm_kev": fwhm_kev(p1_w, slope_m) if (slope_m and p1_w) else None,
                "fwtm_channels": p1_t,
                "fwtm_kev": fwtm_kev(p1_t, slope_m) if (slope_m and p1_t) else None,
                "net_area_counts": p1_a,
                "live_time_sec": live_val,
            }
            peaks_to_save.append(p1_dict)

        if any(x is not None for x in (p2_c, p2_w, p2_t, p2_a)):
            p2_dict = {
                "detector_type": "HPGE",
                "peak_name": "Co-60 (1332.5 keV)",
                "energy_reference_kev": 1332.5,
                "centroid_channel": p2_c,
                "fwhm_channels": p2_w,
                "fwhm_kev": fwhm_kev(p2_w, slope_m) if (slope_m and p2_w) else None,
                "fwtm_channels": p2_t,
                "fwtm_kev": fwtm_kev(p2_t, slope_m) if (slope_m and p2_t) else None,
                "net_area_counts": p2_a,
                "live_time_sec": live_val,
            }
            peaks_to_save.append(p2_dict)

        if not payload and not peaks_to_save:
            if show_feedback:
                self._show_feedback("لا توجد بيانات جديدة لحفظها كمسودة.")
            return True

        try:
            if payload:
                self.service.save_hpge_measurements(self.inspection_id, payload, self.session)
                self.saved_measurements.update(payload)
            if peaks_to_save:
                self.service.save_peaks(self.inspection_id, peaks_to_save, self.session)
                self.saved_peaks = list(peaks_to_save)
            if show_feedback:
                self._show_feedback("تم حفظ المسودة بنجاح ✓")
            return True
        except (PermissionError, SecurityViolationError, InspectionWorkflowError) as e:
            logger.error("Service error saving HPGe draft for inspection %s: %s", self.inspection_id, e)
            if show_feedback:
                QMessageBox.critical(self, "خطأ في حفظ المسودة", f"تعذر حفظ مسودة الفحص:\n{str(e)}")
            return False
        except Exception as e:
            logger.exception("Unexpected error saving HPGe draft for inspection %s", self.inspection_id)
            if show_feedback:
                QMessageBox.critical(self, "خطأ غير متوقع", f"حدث خطأ أثناء حفظ المسودة:\n{str(e)}")
            return False

    def _show_feedback(self, msg: str, is_error: bool = False) -> None:
        if is_error:
            return  # lbl_feedback يبقى لرسالة نجاح الحفظ فقط؛ لا تمرر عبره أخطاء
        if hasattr(self, "lbl_feedback"):
            self.lbl_feedback.setText(msg)
            self.lbl_feedback.setStyleSheet(f"color: {COLORS['success']}; font-weight: bold;")
            if hasattr(self, "_feedback_timer"):
                self._feedback_timer.stop()
                self._feedback_timer.start(5000)

    def _clear_feedback(self) -> None:
        if hasattr(self, "lbl_feedback"):
            try:
                self.lbl_feedback.setText("")
            except RuntimeError:
                pass

    def _on_next_step(self) -> None:
        idx = self.stack.currentIndex()
        if idx >= 4:
            return  # الخطوة 5 لا تسمح بالإنهاء في هذه المهمة

        # التحقق من صحة مدخلات الخطوة الحالية
        ok, err_msg = self._validate_current_step()
        if not ok:
            QMessageBox.warning(self, "تنبيه قبل المتابعة", err_msg or "يرجى تصحيح الأخطاء أولاً.")
            if self._last_invalid_widget is not None:
                self._scroll_to_and_focus_widget(self._last_invalid_widget)
            return

        # حفظ التقدم كمسودة عند الانتقال
        if self.is_editable:
            self.save_draft(show_feedback=False)

        next_idx = idx + 1
        self.stack.setCurrentIndex(next_idx)
        self._update_navigation_state()

    def _on_prev_step(self) -> None:
        idx = self.stack.currentIndex()
        if idx <= 0:
            return
        next_idx = idx - 1
        self.stack.setCurrentIndex(next_idx)
        self._update_navigation_state()

    def _update_navigation_state(self) -> None:
        idx = self.stack.currentIndex()
        self.btn_prev.setEnabled(idx > 0)

        # تحديث شريط الخطوات (الستيبّر)
        for i, lbl in enumerate(self.step_labels):
            lbl.setStyleSheet(self._get_step_style(i, is_current=(i == idx)))

        # الخطوة 5 (المراجعة والاعتماد) لا تسمح بالتقديم في هذه المهمة
        if idx >= 4:
            self.btn_next.setEnabled(False)
            self.btn_next.setText("قيد التطوير (TASK-2.3)")
        else:
            self.btn_next.setEnabled(True)
            self.btn_next.setText("التالي ⬅️")

    def _get_step_style(self, step_idx: int, is_current: bool) -> str:
        mode = get_mode()
        if is_current:
            if mode == "dark":
                return (
                    f"background-color: {COLORS['dark_teal']}; color: #0A1A33; "
                    f"font-weight: bold; border-radius: 4px; padding: 6px 10px; font-size: 13px;"
                )
            return (
                f"background-color: {COLORS['navy']}; color: {COLORS['light_card']}; "
                f"font-weight: bold; border-radius: 4px; padding: 6px 10px; font-size: 13px;"
            )
        if mode == "dark":
            return (
                f"background-color: {COLORS['dark_card']}; color: {COLORS['dark_slate']}; "
                f"border: 1px solid {COLORS['dark_line']}; border-radius: 4px; padding: 6px 10px; font-size: 13px;"
            )
        return (
            f"background-color: {COLORS['light_mist']}; color: {COLORS['light_slate']}; "
            f"border: 1px solid {COLORS['light_line']}; border-radius: 4px; padding: 6px 10px; font-size: 13px;"
        )

    def _apply_read_only_mode(self) -> None:
        """قفل جميع أدوات الإدخال عند فتح الفحص للقراءة فقط."""
        # الخطوة 1
        self.combo_window_status.setEnabled(False)
        self.edit_cooling_hours.setReadOnly(True)
        self.combo_vacuum_intact.setEnabled(False)
        self.combo_wet_frozen.setEnabled(False)

        # الخطوة 2
        for edit in (
            self.edit_v_pos_24, self.edit_v_neg_24,
            self.edit_v_pos_12, self.edit_v_neg_12,
            self.edit_v_pos_6, self.edit_v_neg_6,
            self.edit_applied_voltage, self.edit_ramp_rate,
        ):
            edit.setReadOnly(True)
        self.combo_applied_polarity.setEnabled(False)
        self.combo_ramp_source.setEnabled(False)
        self.chk_ramp_confirm.setEnabled(False)
        self.combo_preamp_signal.setEnabled(False)

        # الخطوة 3
        for edit in (
            self.edit_shaping_time,
            self.edit_coarse_gain,
            self.edit_fine_gain,
            self.edit_baseline_restorer,
            self.edit_pole_zero_setting,
            self.edit_amp_output_amplitude,
        ):
            edit.setReadOnly(True)
        self.combo_pz_distortion.setEnabled(False)

        # الخطوة 4
        for edit in (
            self.edit_total_count_rate_cps,
            self.edit_dead_time_pct,
            self.edit_live_time_sec,
            self.edit_p1_centroid,
            self.edit_p1_fwhm_ch,
            self.edit_p1_fwtm_ch,
            self.edit_p1_net_area,
            self.edit_p2_centroid,
            self.edit_p2_fwhm_ch,
            self.edit_p2_fwtm_ch,
            self.edit_p2_net_area,
        ):
            edit.setReadOnly(True)

        # زر حفظ المسودة
        self.btn_save_draft.setEnabled(False)

