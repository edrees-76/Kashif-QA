"""Laboratory Dark Theme for KASHIF-QA.

Color palette inspired by the institutional seal logo:
- Background: #0B132B (Deep Navy)
- Surface: #1C2541 (Slate Blue Card)
- Primary Accent: #00F2FE (Cyan Radiation Glow)
- Secondary Accent: #4FACFE (Precision Blue)
- Text Primary: #F8FAFC (White Smoke)
- Text Secondary: #94A3B8 (Cool Slate)
- Border: #334155 (Subtle Navy Border)
- Success: #10B981 (Emerald)
- Warning: #F59E0B (Amber)
- Danger: #EF4444 (Crimson)
"""

LAB_DARK_QSS = """
QMainWindow, QDialog {
    background-color: #0B132B;
    color: #F8FAFC;
    font-family: 'Segoe UI', 'Amiri', 'Arial', sans-serif;
}

QWidget {
    background-color: transparent;
    color: #F8FAFC;
}

QStatusBar {
    background-color: #070D1F;
    color: #94A3B8;
    border-top: 1px solid #1E293B;
    font-size: 13px;
    padding: 6px;
}

QStatusBar QLabel {
    color: #CBD5E1;
    padding: 0 10px;
}

QMenuBar {
    background-color: #070D1F;
    color: #E2E8F0;
    border-bottom: 1px solid #1E293B;
    padding: 4px;
}

QMenuBar::item:selected {
    background-color: #1C2541;
    border-radius: 4px;
    color: #00F2FE;
}

QMenu {
    background-color: #0B132B;
    color: #F8FAFC;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 4px;
}

QMenu::item:selected {
    background-color: #1C2541;
    color: #00F2FE;
    border-radius: 4px;
}

QPushButton {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1C2541, stop:1 #27355B);
    color: #F8FAFC;
    border: 1px solid #3B82F6;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: bold;
    font-size: 13px;
}

QPushButton:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563EB, stop:1 #00F2FE);
    color: #0B132B;
    border: 1px solid #00F2FE;
}

QPushButton:pressed {
    background-color: #1D4ED8;
    color: #FFFFFF;
}

QFrame.card {
    background-color: #131E3A;
    border: 1px solid #1E293B;
    border-radius: 10px;
    padding: 16px;
}

QFrame.card:hover {
    border: 1px solid #00F2FE;
}

QLabel.title {
    color: #00F2FE;
    font-size: 22px;
    font-weight: bold;
}

QLabel.subtitle {
    color: #94A3B8;
    font-size: 14px;
}

QLabel.badge-green {
    background-color: rgba(16, 185, 129, 0.15);
    color: #10B981;
    border: 1px solid #10B981;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: bold;
}

QLabel.badge-cyan {
    background-color: rgba(0, 242, 254, 0.15);
    color: #00F2FE;
    border: 1px solid #00F2FE;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: bold;
}
"""
