#!/usr/bin/env python3
# player/main.py

import sys
import os
import urllib.request
import tempfile

# PyInstaller bundle esetén
if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore    import Qt
from PyQt6.QtGui     import QFontDatabase, QFont

def load_fonts():
    """Inter font betöltése – ha nincs lokálisan, rendszer fontot használ."""
    local_paths = [
        os.path.join(os.path.dirname(__file__), "fonts", "Ubuntu-Regular.ttf"),
        os.path.join(os.path.dirname(__file__), "fonts", "Ubuntu-Bold.ttf"),
        os.path.join(os.path.dirname(__file__), "fonts", "Ubuntu-Medium.ttf"),
    ]
    for p in local_paths:
        if os.path.exists(p):
            QFontDatabase.addApplicationFont(p)

    preferred = ["Ubuntu", "Inter", "Segoe UI", "SF Pro Display", "Helvetica Neue",
                 "Roboto", "Arial"]
    available = QFontDatabase.families()
    for f in preferred:
        if f in available:
            return f
    return "Arial"

def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("SchoolLive Player")
    app.setOrganizationName("SchoolLive")

    # Betűtípus
    font_family = load_fonts()
    default_font = QFont(font_family, 13)
    default_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(default_font)

    # Ikon
    try:
        from PyQt6.QtGui import QIcon
        app.setWindowIcon(QIcon("schoollive.ico"))
    except Exception:
        pass

    from ui  import PlayerUI
    from app import SchoolLiveApp

    ui        = PlayerUI()
    _app_ctrl = SchoolLiveApp(ui)

    ui.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()