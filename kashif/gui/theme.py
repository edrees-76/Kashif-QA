"""Visual Identity and Theme Engine for KASHIF-QA.

Implements the official design system from docs/visual-identity.md:
- Light Mode (default) & Dark Mode stylesheets (QSS)
- Strict color tokens meeting WCAG AA contrast (>= 4.5:1 for regular text)
- Semantic status badges paired with icons and text
- Dynamic theme switching
"""

import logging
import os
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# Base Color Palette Tokens
COLORS = {
    # Brand Primary
    "navy": "#002460",          # Deep Navy (Titles, primary buttons)
    "blue": "#004884",          # Lab Blue (Secondary surfaces, headers)
    "teal": "#0C909C",          # Analysis Teal (Accents, active borders)
    "teal_dark": "#0B7A85",     # Teal 700 (Readable text on light surface)
    "orange": "#F08418",        # Detector Orange (Highlights, focus)
    "orange_dark": "#B35C00",   # Orange 700 (Warning text on light surface)

    # Light Mode Surfaces & Text
    "light_surface": "#FAF9F7", # Page background (warm cream canvas)
    "light_card": "#FFFFFF",    # Card background
    "light_mist": "#E4F0F0",    # Section fill / alternate rows
    "light_line": "#C9D8DC",    # Input borders (visible, >= 3:1 non-text contrast need)
    "light_hairline": "#EFEFEF", # Card / panel / divider hairline
    "light_ink": "#0E1B2E",     # Primary text (Contrast 16.5:1 on light)
    "light_slate": "#4A5A6E",   # Secondary text (Contrast 5.8:1 on light)

    # Dark Mode Surfaces & Text
    "dark_surface": "#0A1A33",  # Dark page background
    "dark_card": "#11294D",     # Dark card background
    "dark_ink": "#EAF2F4",      # Dark primary text
    "dark_slate": "#A9BCC4",    # Dark secondary text
    "dark_teal": "#3FB8C4",     # Dark primary accent
    "dark_orange": "#F5A04A",   # Dark highlight accent
    "dark_line": "#1E3A66",     # Dark borders

    "dark_calendar_bg": "#0E1B2E",      # Dark calendar days grid background
    "dark_calendar_header": "#070F1E",  # Dark calendar weekday header background (Contrast >= 4.5:1 with text & red)

    # Semantic Status Colors (Always paired with icon & text)
    "success": "#1E7A4C",       # Pass / Conformity (Contrast 5.4:1 on light)
    "warning": "#B35C00",       # Warning / Near limit (Contrast 4.7:1 on light)
    "error": "#B3261E",         # Failure / Out of spec (Contrast 6.6:1 on light)
    "info": "#0B7A85",          # Information (Contrast 5.1:1 on light)
}

FONT_FAMILY = "'Tajawal', 'IBM Plex Sans Arabic', 'Segoe UI', 'Arial', sans-serif"
FONT_FAMILY_TITLE = "'Tajawal', 'Segoe UI', sans-serif"


_current_mode = "light"


def set_mode(mode: str) -> None:
    """Remember the active theme so dialogs opened later follow the main window."""
    global _current_mode
    _current_mode = "dark" if mode == "dark" else "light"


def get_mode() -> str:
    return _current_mode


def logo_file(mode=None) -> str:
    """Logo asset path for the mode (dark variant has lightened navy parts)."""
    mode = mode or _current_mode
    name = "kashif_logo_dark.png" if mode == "dark" else "kashif_logo.png"
    return os.path.join(os.path.dirname(__file__), "assets", name)


def calendar_arrow_files() -> Tuple[str, str]:
    """Return forward-slashed absolute paths for (prev_month_arrow, next_month_arrow).
    
    Static asset files from repository assets folder.
    NEVER writes or modifies files at runtime.
    """
    assets = os.path.join(os.path.dirname(__file__), "assets")
    prev_icon = os.path.join(assets, "cal_arrow_right_white.png").replace("\\", "/")
    next_icon = os.path.join(assets, "cal_arrow_left_white.png").replace("\\", "/")
    return prev_icon, next_icon


def status_color(ok: bool, mode=None) -> str:
    """Readable pass/fail text colour for table cells in the active mode."""
    mode = mode or _current_mode
    if mode == "dark":
        return "#4ADE80" if ok else "#F87171"
    return COLORS["success"] if ok else COLORS["error"]


def get_stylesheet(mode=None) -> str:
    """Generate the QSS stylesheet for 'light'/'dark'; default is the active mode."""
    if mode is None:
        mode = _current_mode
    if mode == "dark":
        return _build_dark_qss()
    return _build_light_qss()


def _build_light_qss() -> str:
    cal_prev, cal_next = calendar_arrow_files()
    return f"""
QWidget {{
    background-color: transparent;
    color: {COLORS["light_ink"]};
    font-family: {FONT_FAMILY};
}}

QMainWindow, QDialog {{
    background-color: {COLORS["light_surface"]};
    color: {COLORS["light_ink"]};
    font-family: {FONT_FAMILY};
}}

QStatusBar {{
    background-color: {COLORS["light_mist"]};
    color: {COLORS["light_slate"]};
    border-top: 1px solid {COLORS["light_hairline"]};
    font-size: 13px;
    padding: 6px;
}}

QStatusBar QLabel {{
    color: {COLORS["light_ink"]};
    padding: 0 10px;
    font-size: 13px;
}}

QMenuBar {{
    background-color: {COLORS["navy"]};
    color: #FFFFFF;
    padding: 4px;
}}

QMenuBar::item:selected {{
    background-color: {COLORS["blue"]};
    border-radius: 4px;
}}

QPushButton {{
    background-color: {COLORS["navy"]};
    color: #FFFFFF;
    border: 1px solid {COLORS["navy"]};
    border-radius: 20px;
    padding: 8px 24px;
    font-size: 13px;
    font-weight: bold;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {COLORS["blue"]};
    border-color: {COLORS["blue"]};
}}

QPushButton:pressed {{
    background-color: #001A44;
}}

QPushButton:disabled {{
    background-color: #A0B2C6;
    border-color: #A0B2C6;
    color: #F0F4F8;
}}

QPushButton.secondary {{
    background-color: {COLORS["light_mist"]};
    color: {COLORS["navy"]};
    border: 1px solid {COLORS["light_hairline"]};
}}

QPushButton.secondary:hover {{
    background-color: #D2E6E8;
}}

QPushButton.danger {{
    background-color: {COLORS["error"]};
    color: #FFFFFF;
    border-color: {COLORS["error"]};
}}

QPushButton.danger:hover {{
    background-color: #8E1C15;
}}

QLineEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
    background-color: #FFFFFF;
    color: {COLORS["light_ink"]};
    border: 1px solid {COLORS["light_line"]};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
}}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border: 2px solid {COLORS["teal"]};
}}

QComboBox {{
    padding-left: 28px;
    padding-right: 12px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    width: 24px;
    border: none;
    background: transparent;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {COLORS["light_slate"]};
    width: 0px;
    height: 0px;
}}


QTableWidget QDateEdit {{
    background-color: #FFFFFF;
    color: {COLORS["light_ink"]};
    border: 1px solid {COLORS["light_line"]};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
}}

QTableWidget QDateEdit:focus {{
    border: 2px solid {COLORS["teal"]};
}}


QTableWidget {{
    background-color: #FFFFFF;
    color: {COLORS["light_ink"]};
    gridline-color: {COLORS["light_line"]};
    border: 1px solid {COLORS["light_line"]};
    border-radius: 16px;
    selection-background-color: {COLORS["light_mist"]};
    selection-color: {COLORS["navy"]};
}}

QHeaderView::section {{
    background-color: {COLORS["navy"]};
    color: #FFFFFF;
    padding: 8px;
    font-weight: bold;
    border: none;
    border-right: 1px solid {COLORS["blue"]};
}}

QFrame.card {{
    background-color: {COLORS["light_card"]};
    border: 1px solid {COLORS["light_hairline"]};
    border-radius: 16px;
    padding: 24px;
}}

QLabel.title {{
    color: {COLORS["navy"]};
    font-size: 20px;
    font-weight: bold;
    font-family: {FONT_FAMILY_TITLE};
}}

QLabel.subtitle {{
    color: {COLORS["light_slate"]};
    font-size: 13px;
}}

QLabel.badge-success {{
    background-color: rgba(30, 122, 76, 0.12);
    color: {COLORS["success"]};
    border: 1px solid {COLORS["success"]};
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-warning {{
    background-color: rgba(179, 92, 0, 0.12);
    color: {COLORS["warning"]};
    border: 1px solid {COLORS["warning"]};
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-error {{
    background-color: rgba(179, 38, 30, 0.12);
    color: {COLORS["error"]};
    border: 1px solid {COLORS["error"]};
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: bold;
    font-size: 12px;
}}
QLabel.h1 {{ color: {COLORS["navy"]}; font-size: 17px; font-weight: bold; }}
QLabel.h2 {{ color: {COLORS["navy"]}; font-size: 15px; font-weight: bold; }}
QLabel.hint {{ color: {COLORS["light_slate"]}; font-size: 12px; }}
QLabel.body {{ color: {COLORS["light_slate"]}; font-size: 13px; }}
QLabel.error-text {{ color: {COLORS["error"]}; font-weight: bold; font-size: 12px; }}
QLabel.warn-title {{ color: {COLORS["warning"]}; font-size: 16px; font-weight: bold; }}
QLabel.status-success {{ color: {COLORS["success"]}; font-size: 13px; font-weight: bold; }}
QLabel.status-info {{ color: {COLORS["teal_dark"]}; font-size: 13px; font-weight: bold; }}
QLabel.status-neutral {{ color: {COLORS["navy"]}; font-size: 13px; font-weight: bold; }}
QLabel.user-badge {{ background-color: {COLORS["light_mist"]}; color: {COLORS["navy"]}; padding: 6px 14px; border-radius: 16px; font-weight: bold; font-size: 13px; }}
QFrame.warn-card {{ background-color: rgba(179, 92, 0, 0.12); border: 1px solid {COLORS["warning"]}; border-radius: 12px; padding: 12px; }}
QPushButton.btn-success {{ background-color: {COLORS["success"]}; color: #FFFFFF; border-color: {COLORS["success"]}; font-size: 14px; }}
QLineEdit.key-field {{ font-family: monospace; color: {COLORS["navy"]}; font-weight: bold; }}


/* Sidebar navigation */
QFrame.sidebar {{
    background-color: #FFFFFF;
    border-left: 1px solid {COLORS["light_hairline"]};
}}

QPushButton.nav-item {{
    background-color: transparent;
    color: {COLORS["light_ink"]};
    border: none;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 14px;
    font-weight: 500;
    text-align: right;
    min-height: 24px;
}}

QPushButton.nav-item:hover {{
    background-color: {COLORS["light_mist"]};
    color: {COLORS["navy"]};
}}

QPushButton.nav-item-active {{
    background-color: {COLORS["light_mist"]};
    color: {COLORS["navy"]};
    border-right: 4px solid {COLORS["teal"]};
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 14px;
    font-weight: bold;
    text-align: right;
    min-height: 24px;
}}

QPushButton.nav-toggle {{
    background-color: transparent;
    color: {COLORS["light_slate"]};
    border: 1px solid {COLORS["light_line"]};
    border-radius: 8px;
    padding: 6px;
    font-size: 13px;
    min-height: 20px;
}}

QPushButton.nav-toggle:hover {{
    background-color: {COLORS["light_mist"]};
    color: {COLORS["navy"]};
}}

/* Summary Cards */
QFrame.summary-card {{
    background-color: {COLORS["light_card"]};
    border: 1px solid {COLORS["light_hairline"]};
    border-radius: 14px;
    padding: 16px;
}}

QFrame.summary-card:hover {{
    border-color: {COLORS["teal"]};
    background-color: #F8FCFC;
}}

QLabel.summary-count {{
    color: {COLORS["navy"]};
    font-size: 26px;
    font-weight: bold;
}}

QLabel.summary-count-warning {{
    color: {COLORS["orange_dark"]};
    font-size: 26px;
    font-weight: bold;
}}

QLabel.summary-title {{
    color: {COLORS["light_slate"]};
    font-size: 13px;
    font-weight: 600;
}}

QLabel.summary-sub {{
    color: {COLORS["teal_dark"]};
    font-size: 12px;
}}

/* Calibration & Activity Badges */
QLabel.badge-expired {{
    background-color: rgba(179, 38, 30, 0.12);
    color: {COLORS["error"]};
    border: 1px solid {COLORS["error"]};
    border-radius: 6px;
    padding: 3px 8px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-valid {{
    background-color: rgba(30, 122, 76, 0.12);
    color: {COLORS["success"]};
    border: 1px solid {COLORS["success"]};
    border-radius: 6px;
    padding: 3px 8px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-neutral {{
    background-color: rgba(12, 144, 156, 0.12);
    color: {COLORS["teal_dark"]};
    border: 1px solid {COLORS["teal"]};
    border-radius: 6px;
    padding: 3px 8px;
    font-weight: bold;
    font-size: 12px;
}}

/* Search input */
QLineEdit.search-box {{
    background-color: #FFFFFF;
    color: {COLORS["light_ink"]};
    border: 1px solid {COLORS["light_line"]};
    border-radius: 18px;
    padding: 6px 14px;
    font-size: 13px;
}}

QLineEdit.search-box:focus {{
    border-color: {COLORS["teal"]};
}}

QTableCornerButton::section {{
    background-color: {COLORS["navy"]};
    border: none;
}}

/* Scrollbars - Light Mode */
QScrollBar:vertical {{
    background: {COLORS["light_surface"]};
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical {{
    background: {COLORS["light_line"]};
    min-height: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical:hover {{
    background: #A9BCC4;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: none;
}}

QScrollBar:horizontal {{
    background: {COLORS["light_surface"]};
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal {{
    background: {COLORS["light_line"]};
    min-width: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal:hover {{
    background: #A9BCC4;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: none;
}}

/* QCalendarWidget - Light Mode */
QCalendarWidget {{
    background-color: #FFFFFF;
    border: 1px solid {COLORS["light_line"]};
    border-radius: 8px;
}}

QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: {COLORS["navy"]};
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 4px;
}}

QCalendarWidget QToolButton#qt_calendar_prevmonth {{
    qproperty-icon: url("{cal_prev}");
    min-width: 24px;
    min-height: 24px;
}}

QCalendarWidget QToolButton#qt_calendar_nextmonth {{
    qproperty-icon: url("{cal_next}");
    min-width: 24px;
    min-height: 24px;
}}

QCalendarWidget QToolButton {{
    background-color: transparent;
    color: #FFFFFF;
    font-weight: bold;
    font-size: 13px;
    border: none;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 22px;
}}

QCalendarWidget QToolButton:hover {{
    background-color: {COLORS["blue"]};
}}

QCalendarWidget QToolButton:pressed {{
    background-color: {COLORS["teal_dark"]};
}}

QCalendarWidget QSpinBox {{
    background-color: {COLORS["blue"]};
    color: #FFFFFF;
    font-weight: bold;
    font-size: 13px;
    border: 1px solid {COLORS["teal"]};
    border-radius: 4px;
    padding: 2px 6px;
}}

QCalendarWidget QWidget#qt_calendar_calendarview {{
    background-color: #FFFFFF;
    alternate-background-color: {COLORS["light_mist"]};
    color: {COLORS["light_ink"]};
    selection-background-color: {COLORS["teal"]};
    selection-color: #FFFFFF;
    gridline-color: {COLORS["light_hairline"]};
}}

QCalendarWidget QAbstractItemView {{
    background-color: #FFFFFF;
    alternate-background-color: {COLORS["light_mist"]};
    color: {COLORS["light_ink"]};
    selection-background-color: {COLORS["teal"]};
    selection-color: #FFFFFF;
    gridline-color: {COLORS["light_hairline"]};
}}

QCalendarWidget QTableView {{
    background-color: #FFFFFF;
    alternate-background-color: {COLORS["light_mist"]};
    color: {COLORS["light_ink"]};
    selection-background-color: {COLORS["teal"]};
    selection-color: #FFFFFF;
    gridline-color: {COLORS["light_hairline"]};
    outline: 0;
    font-size: 13px;
}}

QCalendarWidget QTableView:enabled {{
    color: {COLORS["light_ink"]};
}}

QCalendarWidget QTableView:disabled {{
    color: #8C9BA5;
}}

QCalendarWidget QHeaderView::section {{
    background-color: {COLORS["light_mist"]};
    color: {COLORS["navy"]};
    font-weight: bold;
    font-size: 12px;
    padding: 4px;
    border: none;
}}

QCalendarWidget QMenu {{
    background-color: #FFFFFF;
    color: {COLORS["light_ink"]};
    border: 1px solid {COLORS["light_line"]};
}}
"""


def _build_dark_qss() -> str:
    cal_prev, cal_next = calendar_arrow_files()
    return f"""
QWidget {{
    background-color: transparent;
    color: {COLORS["dark_ink"]};
    font-family: {FONT_FAMILY};
}}

QMainWindow, QDialog {{
    background-color: {COLORS["dark_surface"]};
    color: {COLORS["dark_ink"]};
    font-family: {FONT_FAMILY};
}}

QStatusBar {{
    background-color: #071224;
    color: {COLORS["dark_slate"]};
    border-top: 1px solid {COLORS["dark_line"]};
    font-size: 13px;
    padding: 6px;
}}

QStatusBar QLabel {{
    color: {COLORS["dark_ink"]};
    padding: 0 10px;
    font-size: 13px;
}}

QPushButton {{
    background-color: {COLORS["dark_card"]};
    color: {COLORS["dark_teal"]};
    border: 1px solid {COLORS["dark_teal"]};
    border-radius: 20px;
    padding: 8px 24px;
    font-size: 13px;
    font-weight: bold;
    min-height: 20px;
}}

QPushButton:hover {{
    background-color: {COLORS["dark_teal"]};
    color: {COLORS["dark_surface"]};
}}

QLineEdit, QTextEdit, QComboBox, QDateEdit, QSpinBox, QDoubleSpinBox {{
    background-color: #071224;
    color: {COLORS["dark_ink"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
}}

QLineEdit:focus, QTextEdit:focus, QComboBox:focus {{
    border: 2px solid {COLORS["dark_teal"]};
}}

QComboBox {{
    padding-left: 28px;
    padding-right: 12px;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top left;
    width: 24px;
    border: none;
    background: transparent;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {COLORS["dark_slate"]};
    width: 0px;
    height: 0px;
}}


QFrame.card {{
    background-color: {COLORS["dark_card"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 16px;
    padding: 24px;
}}

QLabel.title {{
    color: {COLORS["dark_teal"]};
    font-size: 20px;
    font-weight: bold;
    font-family: {FONT_FAMILY_TITLE};
}}

QLabel.subtitle {{
    color: {COLORS["dark_slate"]};
    font-size: 13px;
}}

QLabel.badge-success {{
    background-color: rgba(30, 122, 76, 0.25);
    color: #4ADE80;
    border: 1px solid #4ADE80;
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-warning {{
    background-color: rgba(179, 92, 0, 0.25);
    color: {COLORS["dark_orange"]};
    border: 1px solid {COLORS["dark_orange"]};
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-error {{
    background-color: rgba(179, 38, 30, 0.25);
    color: #F87171;
    border: 1px solid #F87171;
    border-radius: 6px;
    padding: 4px 10px;
    font-weight: bold;
    font-size: 12px;
}}
QPushButton:disabled {{
    background-color: #162F55;
    border-color: #162F55;
    color: #6F86A0;
}}

QPushButton.secondary {{
    background-color: transparent;
    color: {COLORS["dark_ink"]};
    border: 1px solid {COLORS["dark_line"]};
}}

QPushButton.danger {{
    background-color: #8E1C15;
    color: #FFFFFF;
    border-color: #B3261E;
}}

QComboBox QAbstractItemView {{
    background-color: #071224;
    color: {COLORS["dark_ink"]};
    selection-background-color: {COLORS["dark_card"]};
    selection-color: {COLORS["dark_teal"]};
}}

QTableWidget QDateEdit {{
    background-color: #071224;
    color: {COLORS["dark_ink"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 13px;
}}

QTableWidget QDateEdit:focus {{
    border: 2px solid {COLORS["dark_teal"]};
}}

QTableWidget {{
    background-color: {COLORS["dark_card"]};
    alternate-background-color: #0F2444;
    color: {COLORS["dark_ink"]};
    gridline-color: {COLORS["dark_line"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 16px;
    selection-background-color: #1B4F6B;
    selection-color: #FFFFFF;
}}

QHeaderView::section {{
    background-color: #071224;
    color: {COLORS["dark_teal"]};
    padding: 8px;
    font-weight: bold;
    border: none;
    border-right: 1px solid {COLORS["dark_line"]};
}}

QCheckBox {{
    color: {COLORS["dark_ink"]};
}}

QLabel.h1 {{ color: {COLORS["dark_teal"]}; font-size: 17px; font-weight: bold; }}
QLabel.h2 {{ color: {COLORS["dark_ink"]}; font-size: 15px; font-weight: bold; }}
QLabel.hint {{ color: {COLORS["dark_slate"]}; font-size: 12px; }}
QLabel.body {{ color: {COLORS["dark_slate"]}; font-size: 13px; }}
QLabel.error-text {{ color: #F87171; font-weight: bold; font-size: 12px; }}
QLabel.warn-title {{ color: {COLORS["dark_orange"]}; font-size: 16px; font-weight: bold; }}
QLabel.status-success {{ color: #4ADE80; font-size: 13px; font-weight: bold; }}
QLabel.status-info {{ color: {COLORS["dark_teal"]}; font-size: 13px; font-weight: bold; }}
QLabel.status-neutral {{ color: {COLORS["dark_ink"]}; font-size: 13px; font-weight: bold; }}
QLabel.user-badge {{ background-color: {COLORS["dark_card"]}; color: {COLORS["dark_teal"]}; padding: 6px 14px; border-radius: 16px; font-weight: bold; font-size: 13px; }}
QFrame.warn-card {{ background-color: rgba(179, 92, 0, 0.25); border: 1px solid {COLORS["dark_orange"]}; border-radius: 12px; padding: 12px; }}
QPushButton.btn-success {{ background-color: {COLORS["success"]}; color: #FFFFFF; border-color: {COLORS["success"]}; font-size: 14px; }}
QLineEdit.key-field {{ font-family: monospace; color: {COLORS["dark_ink"]}; font-weight: bold; }}


/* Sidebar navigation */
QFrame.sidebar {{
    background-color: {COLORS["dark_card"]};
    border-left: 1px solid {COLORS["dark_line"]};
}}

QPushButton.nav-item {{
    background-color: transparent;
    color: {COLORS["dark_ink"]};
    border: none;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 14px;
    font-weight: 500;
    text-align: right;
    min-height: 24px;
}}

QPushButton.nav-item:hover {{
    background-color: #173866;
    color: {COLORS["dark_teal"]};
}}

QPushButton.nav-item-active {{
    background-color: #173866;
    color: {COLORS["dark_teal"]};
    border-right: 4px solid {COLORS["dark_teal"]};
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 14px;
    font-weight: bold;
    text-align: right;
    min-height: 24px;
}}

QPushButton.nav-toggle {{
    background-color: transparent;
    color: {COLORS["dark_slate"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 8px;
    padding: 6px;
    font-size: 13px;
    min-height: 20px;
}}

QPushButton.nav-toggle:hover {{
    background-color: #173866;
    color: {COLORS["dark_teal"]};
}}

/* Summary Cards */
QFrame.summary-card {{
    background-color: {COLORS["dark_card"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 14px;
    padding: 16px;
}}

QFrame.summary-card:hover {{
    border-color: {COLORS["dark_teal"]};
    background-color: #15325E;
}}

QLabel.summary-count {{
    color: {COLORS["dark_teal"]};
    font-size: 26px;
    font-weight: bold;
}}

QLabel.summary-count-warning {{
    color: {COLORS["dark_orange"]};
    font-size: 26px;
    font-weight: bold;
}}

QLabel.summary-title {{
    color: {COLORS["dark_slate"]};
    font-size: 13px;
    font-weight: 600;
}}

QLabel.summary-sub {{
    color: {COLORS["dark_orange"]};
    font-size: 12px;
}}

/* Filter labels for high contrast in dark mode */
QLabel.filter-label {{
    color: #C4D5DF;
    font-weight: 500;
    font-size: 13px;
}}

/* Calibration & Activity Badges */
QLabel.badge-expired {{
    background-color: rgba(179, 38, 30, 0.25);
    color: #F87171;
    border: 1px solid #F87171;
    border-radius: 6px;
    padding: 3px 8px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-valid {{
    background-color: rgba(74, 222, 128, 0.2);
    color: #4ADE80;
    border: 1px solid #4ADE80;
    border-radius: 6px;
    padding: 3px 8px;
    font-weight: bold;
    font-size: 12px;
}}

QLabel.badge-neutral {{
    background-color: rgba(63, 184, 196, 0.2);
    color: {COLORS["dark_teal"]};
    border: 1px solid {COLORS["dark_teal"]};
    border-radius: 6px;
    padding: 3px 8px;
    font-weight: bold;
    font-size: 12px;
}}

/* Search input */
QLineEdit.search-box {{
    background-color: #071224;
    color: {COLORS["dark_ink"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 18px;
    padding: 6px 14px;
    font-size: 13px;
}}

QLineEdit.search-box:focus {{
    border-color: {COLORS["dark_teal"]};
}}

QTableCornerButton::section {{
    background-color: #071224;
    border: none;
}}

/* Scrollbars - Dark Mode */
QScrollBar:vertical {{
    background: {COLORS["dark_surface"]};
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical {{
    background: {COLORS["dark_line"]};
    min-height: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:vertical:hover {{
    background: {COLORS["dark_teal"]};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: none;
}}

QScrollBar:horizontal {{
    background: {COLORS["dark_surface"]};
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal {{
    background: {COLORS["dark_line"]};
    min-width: 24px;
    border-radius: 5px;
}}

QScrollBar::handle:horizontal:hover {{
    background: {COLORS["dark_teal"]};
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: none;
}}

/* QCalendarWidget - Dark Mode (Warm dark background, not absolute black #000000) */
QCalendarWidget {{
    background-color: {COLORS["dark_calendar_bg"]};
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 8px;
}}

QCalendarWidget QWidget#qt_calendar_navigationbar {{
    background-color: {COLORS["dark_card"]};
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 4px;
}}

QCalendarWidget QToolButton#qt_calendar_prevmonth {{
    qproperty-icon: url("{cal_prev}");
    min-width: 24px;
    min-height: 24px;
}}

QCalendarWidget QToolButton#qt_calendar_nextmonth {{
    qproperty-icon: url("{cal_next}");
    min-width: 24px;
    min-height: 24px;
}}

QCalendarWidget QToolButton {{
    background-color: transparent;
    color: {COLORS["dark_ink"]};
    font-weight: bold;
    font-size: 13px;
    border: none;
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 22px;
}}

QCalendarWidget QToolButton:hover {{
    background-color: #173866;
    color: {COLORS["dark_teal"]};
}}

QCalendarWidget QToolButton:pressed {{
    background-color: {COLORS["dark_teal"]};
    color: #FFFFFF;
}}

QCalendarWidget QSpinBox {{
    background-color: #0A1A33;
    color: {COLORS["dark_ink"]};
    font-weight: bold;
    font-size: 13px;
    border: 1px solid {COLORS["dark_line"]};
    border-radius: 4px;
    padding: 2px 6px;
}}

QCalendarWidget QWidget#qt_calendar_calendarview {{
    background-color: {COLORS["dark_calendar_bg"]};
    alternate-background-color: {COLORS["dark_calendar_header"]};
    color: {COLORS["dark_ink"]};
    selection-background-color: {COLORS["dark_teal"]};
    selection-color: #0A1A33;
    gridline-color: {COLORS["dark_line"]};
}}

QCalendarWidget QAbstractItemView {{
    background-color: {COLORS["dark_calendar_bg"]};
    alternate-background-color: {COLORS["dark_calendar_header"]};
    color: {COLORS["dark_ink"]};
    selection-background-color: {COLORS["dark_teal"]};
    selection-color: #0A1A33;
    gridline-color: {COLORS["dark_line"]};
}}

QCalendarWidget QTableView {{
    background-color: {COLORS["dark_calendar_bg"]};
    alternate-background-color: {COLORS["dark_calendar_header"]};
    color: {COLORS["dark_ink"]};
    selection-background-color: {COLORS["dark_teal"]};
    selection-color: #0A1A33;
    gridline-color: {COLORS["dark_line"]};
    outline: 0;
    font-size: 13px;
}}

QCalendarWidget QTableView:enabled {{
    color: {COLORS["dark_ink"]};
}}

QCalendarWidget QTableView:disabled {{
    color: #627282;
}}

QCalendarWidget QHeaderView::section {{
    background-color: {COLORS["dark_calendar_header"]};
    color: {COLORS["dark_teal"]};
    font-weight: bold;
    font-size: 12px;
    padding: 4px;
    border: none;
}}

QCalendarWidget QMenu {{
    background-color: {COLORS["dark_calendar_bg"]};
    color: {COLORS["dark_ink"]};
    border: 1px solid {COLORS["dark_line"]};
}}
"""


# Contrast Calculation Utility for WCAG 2.1 compliance verification
def relative_luminance(hex_color: str) -> float:
    """Calculate the relative luminance of a sRGB hex color."""
    hex_color = hex_color.lstrip("#")
    r, g, b = tuple(int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4))
    
    def adjust(channel):
        return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        
    return 0.2126 * adjust(r) + 0.7152 * adjust(g) + 0.0722 * adjust(b)


def calculate_contrast_ratio(hex1: str, hex2: str) -> float:
    """Calculate WCAG contrast ratio between two hex colors."""
    lum1 = relative_luminance(hex1)
    lum2 = relative_luminance(hex2)
    lighter = max(lum1, lum2)
    darker = min(lum1, lum2)
    return (lighter + 0.05) / (darker + 0.05)


# Backward-compatibility aliases
LAB_LIGHT_QSS = _build_light_qss()
LAB_DARK_QSS = _build_dark_qss()

