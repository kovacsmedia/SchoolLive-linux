# player/ui.py
# SchoolLive Player – PyQt6 modern material dark UI

import time
import math
import datetime
from typing import Optional, Callable

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QFrame, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QGraphicsOpacityEffect, QSizePolicy,
)
from PyQt6.QtCore import (
    Qt, QTimer, QThread, pyqtSignal, QPropertyAnimation,
    QEasingCurve, QRect, pyqtProperty, QObject, QPoint,
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QBrush, QLinearGradient,
    QPen, QFontDatabase, QPalette, QScreen,
)

# ── Paletta ────────────────────────────────────────────────────────────────────
BG          = "#07101f"
BG2         = "#0d1b2e"
BG3         = "#0a1628"
BORDER      = "#1a2d47"
TEXT        = "#f0f6ff"
TEXT_MUTED  = "#8da4c0"
TEXT_DIM    = "#4a6280"
BLUE        = "#3b82f6"
BLUE_DARK   = "#1d4ed8"
BLUE_GLOW   = "rgba(59,130,246,0.15)"
GREEN       = "#22c55e"
RED         = "#ef4444"
AMBER       = "#f59e0b"
ORANGE      = "#f97316"
PURPLE      = "#6366f1"

# ── Global stylesheet ──────────────────────────────────────────────────────────
GLOBAL_QSS = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: 'Segoe UI', 'Inter', 'SF Pro Display', Arial, sans-serif;
}}
QLabel {{
    background: transparent;
    color: {TEXT};
}}
QLineEdit {{
    background: {BG2};
    color: {TEXT};
    border: 1.5px solid {BORDER};
    border-radius: 10px;
    padding: 10px 16px;
    font-size: 14px;
    selection-background-color: {BLUE};
}}
QLineEdit:focus {{
    border: 1.5px solid {BLUE};
}}
QPushButton {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {BLUE}, stop:1 {PURPLE});
    color: white;
    border: none;
    border-radius: 12px;
    padding: 12px 32px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #2563eb, stop:1 #4f46e5);
}}
QPushButton:pressed {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #1d4ed8, stop:1 #3730a3);
}}
QPushButton:disabled {{
    background: {BORDER};
    color: {TEXT_DIM};
}}
QFrame#card {{
    background: {BG2};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QFrame#banner {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {AMBER}, stop:1 #f97316);
    border-radius: 0px;
}}
QFrame#updateBanner {{
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 {BLUE}, stop:1 {PURPLE});
    border-radius: 0px;
}}
"""

# ── Segéd: thread-safe UI híd ─────────────────────────────────────────────────
class UIBridge(QObject):
    set_online_signal          = pyqtSignal(bool)
    set_snap_signal            = pyqtSignal(str)
    set_institution_signal     = pyqtSignal(str)
    set_bells_signal           = pyqtSignal(list)
    set_cache_signal           = pyqtSignal(str)
    show_bell_banner_signal    = pyqtSignal(bool)
    show_msg_overlay_signal    = pyqtSignal(str, int)
    show_radio_overlay_signal  = pyqtSignal(str)
    hide_overlay_signal        = pyqtSignal()
    show_update_banner_signal  = pyqtSignal(str)
    show_login_signal          = pyqtSignal()
    hide_login_signal          = pyqtSignal()
    show_pending_signal        = pyqtSignal()
    hide_pending_signal        = pyqtSignal()
    set_login_error_signal     = pyqtSignal(str)
    set_volume_display_signal  = pyqtSignal(int)


# ── Egyedi widgetek ────────────────────────────────────────────────────────────
class ProgressBar(QWidget):
    """Gradient progress sáv – üzenet olvasási időhöz."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pct = 0.0
        self.setFixedHeight(6)
        self._color1 = QColor(BLUE)
        self._color2 = QColor(PURPLE)

    def set_pct(self, pct: float):
        self._pct = max(0.0, min(1.0, pct))
        self.update()

    def set_colors(self, c1: str, c2: str):
        self._color1 = QColor(c1)
        self._color2 = QColor(c2)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.setBrush(QBrush(QColor(BORDER)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, w, h, h//2, h//2)
        if self._pct > 0:
            grad = QLinearGradient(0, 0, w, 0)
            grad.setColorAt(0, self._color1)
            grad.setColorAt(1, self._color2)
            p.setBrush(QBrush(grad))
            p.drawRoundedRect(0, 0, int(w * self._pct), h, h//2, h//2)


class PulsingLine(QWidget):
    """Rádió lejátszás animáció – teljes szélességű narancs vonal."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity  = 1.0
        self._color    = QColor(AMBER)
        self._color2   = QColor(ORANGE)
        self.setFixedHeight(6)

    def set_opacity(self, opacity: float):
        self._opacity = max(0.0, min(1.0, opacity))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        bg = QColor(BORDER)
        p.setBrush(QBrush(bg))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, w, h, h//2, h//2)
        grad = QLinearGradient(0, 0, w, 0)
        c1 = QColor(self._color);  c1.setAlphaF(self._opacity)
        c2 = QColor(self._color2); c2.setAlphaF(self._opacity)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.setBrush(QBrush(grad))
        p.drawRoundedRect(0, 0, w, h, h//2, h//2)


class StatusDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._online = False
        self.setFixedSize(12, 12)

    def set_online(self, online: bool):
        self._online = online
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(GREEN if self._online else RED)
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, 12, 12)
        if self._online:
            glow = QColor(GREEN); glow.setAlpha(60)
            p.setBrush(QBrush(glow))
            p.drawEllipse(-3, -3, 18, 18)


# ══════════════════════════════════════════════════════════════════════════════
class PlayerUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self._bridge = UIBridge()
        self._connect_bridge()
        self._volume = 7
        self._bells: list = []
        self._overlay_visible = False
        self._banner_visible  = False
        self._update_click_cb: Optional[Callable] = None
        self.on_volume_change: Optional[Callable] = None
        self._dismiss_step    = 0
        self._dismiss_timer:  Optional[QTimer] = None
        self._radio_pulse_timer: Optional[QTimer] = None
        self._radio_pulse_step  = 0

        self._setup_window()
        self._build_ui()
        self._start_clock()

    # ── Bridge ─────────────────────────────────────────────────────────────────
    def _connect_bridge(self):
        b = self._bridge
        b.set_online_signal.connect(self._do_set_online)
        b.set_snap_signal.connect(self._do_set_snap)
        b.set_institution_signal.connect(self._do_set_institution)
        b.set_bells_signal.connect(self._do_set_bells)
        b.set_cache_signal.connect(self._do_set_cache)
        b.show_bell_banner_signal.connect(self._do_show_bell_banner)
        b.show_msg_overlay_signal.connect(self._do_show_msg_overlay)
        b.show_radio_overlay_signal.connect(self._do_show_radio_overlay)
        b.hide_overlay_signal.connect(self._do_hide_overlay)
        b.show_update_banner_signal.connect(self._do_show_update_banner)
        b.show_login_signal.connect(self._do_show_login)
        b.hide_login_signal.connect(self._do_hide_login)
        b.show_pending_signal.connect(self._do_show_pending)
        b.hide_pending_signal.connect(self._do_hide_pending)
        b.set_login_error_signal.connect(self._do_set_login_error)
        b.set_volume_display_signal.connect(self._do_set_volume)

    # ── Ablak ──────────────────────────────────────────────────────────────────
    def _setup_window(self):
        self.setWindowTitle("SchoolLive Player")
        self.setStyleSheet(GLOBAL_QSS)
        self.showFullScreen()

    # ── UI építés ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        self._root_layout = QVBoxLayout(root)
        self._root_layout.setContentsMargins(0, 0, 0, 0)
        self._root_layout.setSpacing(0)

        # Update banner
        self._update_banner = QFrame()
        self._update_banner.setObjectName("updateBanner")
        self._update_banner.setFixedHeight(40)
        _ub_layout = QHBoxLayout(self._update_banner)
        _ub_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_update = QLabel("")
        self._lbl_update.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_update.setStyleSheet(
            "color: white; font-size: 13px; font-weight: 600; background: transparent;"
        )
        self._lbl_update.mousePressEvent = self._on_update_click
        _ub_layout.addWidget(self._lbl_update)
        self._update_banner.hide()
        self._root_layout.addWidget(self._update_banner)

        # Bell banner
        self._bell_banner = QFrame()
        self._bell_banner.setObjectName("banner")
        self._bell_banner.setFixedHeight(40)
        _bb_layout = QHBoxLayout(self._bell_banner)
        _bb_layout.setContentsMargins(0, 0, 0, 0)
        _bb_lbl = QLabel("🔔  Csengetés folyamatban")
        _bb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _bb_lbl.setStyleSheet(
            "color: white; font-size: 13px; font-weight: 700; background: transparent;"
        )
        _bb_layout.addWidget(_bb_lbl)
        self._bell_banner.hide()
        self._root_layout.addWidget(self._bell_banner)

        self._root_layout.addWidget(self._build_header())

        self._stack = QStackedWidget()
        self._root_layout.addWidget(self._stack)

        self._main_widget    = self._build_main()
        self._pending_widget = self._build_pending()
        self._stack.addWidget(self._main_widget)    # 0
        self._stack.addWidget(self._pending_widget) # 1
        self._stack.setCurrentIndex(1)

        self._overlay = self._build_overlay()
        self._overlay.hide()

    # ── Header ─────────────────────────────────────────────────────────────────
    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(f"background: {BG2}; border-bottom: 1px solid {BORDER};")
        frame.setFixedHeight(64)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(28, 0, 28, 0)

        left = QVBoxLayout()
        left.setSpacing(2)
        brand = QLabel("SchoolLive")
        brand.setStyleSheet(f"color: {BLUE}; font-size: 15px; font-weight: 800;")
        self._lbl_inst = QLabel("")
        self._lbl_inst.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 12px;")
        left.addWidget(brand)
        left.addWidget(self._lbl_inst)
        layout.addLayout(left)
        layout.addStretch()

        self._lbl_snap = QLabel("")
        self._lbl_snap.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        layout.addWidget(self._lbl_snap)
        layout.addSpacing(16)

        self._dot = StatusDot()
        self._lbl_status = QLabel("Offline")
        self._lbl_status.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 12px; font-weight: 700;"
        )
        layout.addWidget(self._dot)
        layout.addSpacing(6)
        layout.addWidget(self._lbl_status)

        return frame

    # ── Main képernyő ──────────────────────────────────────────────────────────
    def _build_main(self) -> QWidget:
        outer = QWidget()
        outer.setStyleSheet(f"background: {BG};")
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        content = QWidget()
        content.setStyleSheet(f"background: {BG};")
        outer_layout.addWidget(content, stretch=1)
        outer_layout.addWidget(self._build_footer())

        self._logo_label = QLabel(content)
        self._logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._logo_label.setStyleSheet("background: transparent;")
        try:
            from PyQt6.QtGui import QPixmap
            pix = QPixmap("player/schoollive-logo.png")
            self._logo_pixmap = pix if not pix.isNull() else None
        except Exception:
            self._logo_pixmap = None

        logo_opacity = QGraphicsOpacityEffect()
        logo_opacity.setOpacity(0.10)
        self._logo_label.setGraphicsEffect(logo_opacity)

        if self._logo_pixmap is None:
            self._logo_label.setText("SchoolLive")
            self._logo_label.setStyleSheet(
                f"background: transparent; color: {TEXT};"
                f"font-size: 96px; font-weight: 900; letter-spacing: -2px;"
            )

        self._lbl_clock = QLabel("00:00:00", content)
        self._lbl_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_clock.setStyleSheet(
            f"color: {TEXT}; font-size: 180px; font-weight: 1200;"
            f"letter-spacing: -3px; background: transparent;"
            f"font-family: 'Ubuntu', 'Segoe UI', Arial, sans-serif;"
            f"font-variant-numeric: tabular-nums;"
        )

        self._lbl_date = QLabel("", content)
        self._lbl_date.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_date.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 22px; font-weight: 600; background: transparent;"
        )

        self._bell_card = QFrame(content)
        self._bell_card.setObjectName("card")
        self._bell_card.setFixedSize(380, 64)
        bell_layout = QHBoxLayout(self._bell_card)
        bell_layout.setContentsMargins(20, 0, 20, 0)
        bell_icon = QLabel("🔔")
        bell_icon.setStyleSheet("font-size: 22px; background: transparent;")
        bell_label = QLabel("Következő csengetés:")
        bell_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px; font-weight: 600;")
        self._lbl_next_bell = QLabel("--:--")
        self._lbl_next_bell.setStyleSheet(
            f"color: {TEXT}; font-size: 22px; font-weight: 800;"
        )
        bell_layout.addWidget(bell_icon)
        bell_layout.addSpacing(8)
        bell_layout.addWidget(bell_label)
        bell_layout.addStretch()
        bell_layout.addWidget(self._lbl_next_bell)
        self._bell_card.hide()

        self._main_content = content
        self._layout_main_content()

        return outer

    def _layout_main_content(self):
        if not hasattr(self, "_main_content"):
            return
        w = self._main_content.width()
        h = self._main_content.height()
        if w == 0 or h == 0:
            return

        logo_size = min(w * 1.4, h * 1.2)
        lw = int(logo_size)
        lh = int(logo_size * 0.35)
        if self._logo_pixmap:
            from PyQt6.QtCore import Qt as _Qt
            scaled = self._logo_pixmap.scaled(
                lw, lh,
                _Qt.AspectRatioMode.KeepAspectRatio,
                _Qt.TransformationMode.SmoothTransformation,
            )
            self._logo_label.setPixmap(scaled)
            self._logo_label.setFixedSize(scaled.width(), scaled.height())
        else:
            self._logo_label.setFixedSize(lw, lh)
        self._logo_label.move(
            (w - self._logo_label.width()) // 2,
            (h - self._logo_label.height()) // 2,
        )

        fm = self._lbl_clock.fontMetrics()
        clock_w = fm.horizontalAdvance("00:00:00") + 20
        clock_h = fm.height() + 20
        self._lbl_clock.setFixedSize(clock_w, clock_h)
        clock_y = int(h * 0.30)
        self._lbl_clock.move((w - clock_w) // 2, clock_y)

        self._lbl_date.adjustSize()
        dw = max(self._lbl_date.sizeHint().width() + 20, clock_w)
        dh = self._lbl_date.sizeHint().height()
        self._lbl_date.setFixedSize(dw, dh)
        date_y = clock_y + clock_h + 6
        self._lbl_date.move((w - dw) // 2, date_y)

        bell_y = date_y + dh + 20
        self._bell_card.move((w - self._bell_card.width()) // 2, bell_y)

    # ── Footer ─────────────────────────────────────────────────────────────────
    def _build_footer(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(f"background: {BG2}; border-top: 1px solid {BORDER};")
        frame.setFixedHeight(48)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(20, 0, 20, 0)

        from api_client import SHORT_ID
        lbl_id = QLabel(f"📱  {SHORT_ID}")
        lbl_id.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        layout.addWidget(lbl_id)

        self._lbl_cache = QLabel("")
        self._lbl_cache.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px;")
        layout.addWidget(self._lbl_cache)
        layout.addStretch()

        vol_frame = QHBoxLayout()
        vol_frame.setSpacing(4)
        lbl_speaker = QLabel("🔈")
        lbl_speaker.setStyleSheet("font-size: 14px;")
        self._btn_vol_down = QPushButton("−")
        self._btn_vol_down.setFixedSize(30, 30)
        self._btn_vol_down.setStyleSheet(
            f"background: {BG}; color: {TEXT_MUTED}; border: 1px solid {BORDER};"
            f"border-radius: 8px; font-size: 16px; padding: 0;"
        )
        self._btn_vol_down.clicked.connect(self._vol_down)

        self._lbl_vol = QLabel("7")
        self._lbl_vol.setFixedWidth(24)
        self._lbl_vol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_vol.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 13px;")

        self._btn_vol_up = QPushButton("+")
        self._btn_vol_up.setFixedSize(30, 30)
        self._btn_vol_up.setStyleSheet(self._btn_vol_down.styleSheet())
        self._btn_vol_up.clicked.connect(self._vol_up)

        lbl_speaker2 = QLabel("🔊")
        lbl_speaker2.setStyleSheet("font-size: 14px;")

        for ww in [lbl_speaker, self._btn_vol_down, self._lbl_vol,
                   self._btn_vol_up, lbl_speaker2]:
            vol_frame.addWidget(ww)
        layout.addLayout(vol_frame)

        return frame

    # ── Pending ────────────────────────────────────────────────────────────────
    def _build_pending(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: {BG};")
        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(12)

        icon = QLabel("📱")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 64px;")
        layout.addWidget(icon)

        title = QLabel("Virtuális lejátszó")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {TEXT}; font-size: 26px; font-weight: 800;")
        layout.addWidget(title)

        sub = QLabel(
            "Ez az eszköz még nincs aktiválva.\n"
            "Kérj meg egy rendszergazdát, hogy aktiválja az Eszközök menüben."
        )
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 14px; line-height: 1.6;")
        layout.addWidget(sub)

        card = QFrame()
        card.setObjectName("card")
        card.setFixedWidth(340)
        c_layout = QVBoxLayout(card)
        c_layout.setContentsMargins(20, 16, 20, 16)
        c_layout.setSpacing(4)
        lbl_id_title = QLabel("ESZKÖZ AZONOSÍTÓ")
        lbl_id_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_id_title.setStyleSheet(
            f"color: {TEXT_DIM}; font-size: 10px; letter-spacing: 1px;"
        )
        from api_client import SHORT_ID
        lbl_id = QLabel(SHORT_ID)
        lbl_id.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_id.setStyleSheet(f"color: {BLUE}; font-size: 18px; font-weight: 700;")
        c_layout.addWidget(lbl_id_title)
        c_layout.addWidget(lbl_id)
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)

        wait = QLabel("⏳  Várakozás aktiválásra…")
        wait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        wait.setStyleSheet(f"color: {TEXT_DIM}; font-size: 13px;")
        layout.addWidget(wait)

        return w

    # ── Overlay ────────────────────────────────────────────────────────────────
    def _build_overlay(self) -> QWidget:
        w = QWidget(self.centralWidget())
        w.setStyleSheet(f"background: rgba(7,16,31,0.96);")

        layout = QVBoxLayout(w)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(16)

        self._lbl_msg_text = QLabel("")
        self._lbl_msg_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_msg_text.setWordWrap(True)
        self._lbl_msg_text.setStyleSheet(
            f"color: {TEXT}; font-size: 42px; font-weight: 800;"
        )
        layout.addWidget(self._lbl_msg_text)

        self._prog_bar = ProgressBar()
        self._prog_bar.setFixedWidth(500)
        layout.addWidget(self._prog_bar, 0, Qt.AlignmentFlag.AlignCenter)

        self._lbl_radio_icon = QLabel("📻")
        self._lbl_radio_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_radio_icon.setStyleSheet("font-size: 64px;")
        layout.addWidget(self._lbl_radio_icon)

        self._lbl_radio_title = QLabel("Iskolarádió")
        self._lbl_radio_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_radio_title.setStyleSheet(
            f"color: {AMBER}; font-size: 48px; font-weight: 900;"
        )
        layout.addWidget(self._lbl_radio_title)

        self._pulsing_line = PulsingLine()
        self._pulsing_line.setFixedWidth(500)
        layout.addWidget(self._pulsing_line, 0, Qt.AlignmentFlag.AlignCenter)

        self._btn_dismiss = QPushButton("×  Bezárás")
        self._btn_dismiss.setFixedSize(160, 40)
        self._btn_dismiss.setStyleSheet(
            f"background: transparent; color: {TEXT_DIM}; border: 1px solid {BORDER};"
            f"border-radius: 10px; font-size: 14px; padding: 0;"
        )
        self._btn_dismiss.clicked.connect(self._do_hide_overlay)
        layout.addWidget(self._btn_dismiss, 0, Qt.AlignmentFlag.AlignCenter)

        self._prog_timer = QTimer()
        self._prog_timer.setInterval(100)
        self._prog_timer.timeout.connect(self._update_progress)
        self._prog_start = 0.0
        self._prog_total = 0.0

        return w

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_overlay"):
            cw = self.centralWidget()
            if cw:
                self._overlay.setGeometry(0, 0, cw.width(), cw.height())
        if hasattr(self, "_main_content"):
            self._main_content.update()
            QTimer.singleShot(0, self._layout_main_content)

    # ── Óra ────────────────────────────────────────────────────────────────────
    def _start_clock(self):
        self._clock_timer = QTimer()
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._tick_clock)
        self._clock_timer.start()
        self._tick_clock()

    # Magyar hónapok és napok – Windows kompatibilis, locale-független
    _HU_MONTHS = [
        "", "január", "február", "március", "április", "május", "június",
        "július", "augusztus", "szeptember", "október", "november", "december"
    ]
    _HU_DAYS = [
        "hétfő", "kedd", "szerda", "csütörtök", "péntek", "szombat", "vasárnap"
    ]

    def _tick_clock(self):
        now = datetime.datetime.now()
        if hasattr(self, "_lbl_clock"):
            self._lbl_clock.setText(now.strftime("%H:%M:%S"))
        if hasattr(self, "_lbl_date"):
            month    = self._HU_MONTHS[now.month]
            day      = self._HU_DAYS[now.weekday()]
            self._lbl_date.setText(f"{now.year}. {month} {now.day}., {day}")
        if hasattr(self, "_main_content") and self._main_content.width() > 0:
            self._layout_main_content()
        self._refresh_next_bell()

    def _refresh_next_bell(self):
        if not self._bells:
            return
        now = datetime.datetime.now()
        now_min = now.hour * 60 + now.minute
        future = [b for b in self._bells if b["hour"] * 60 + b["minute"] > now_min]
        if future:
            nb = min(future, key=lambda b: b["hour"] * 60 + b["minute"])
            self._lbl_next_bell.setText(f"{nb['hour']:02d}:{nb['minute']:02d}")
            self._bell_card.show()
        else:
            self._bell_card.hide()

    # ── Hangerő ────────────────────────────────────────────────────────────────
    def _vol_up(self):
        self._volume = min(10, self._volume + 1)
        self._lbl_vol.setText(str(self._volume))
        if self.on_volume_change:
            self.on_volume_change(self._volume)

    def _vol_down(self):
        self._volume = max(0, self._volume - 1)
        self._lbl_vol.setText(str(self._volume))
        if self.on_volume_change:
            self.on_volume_change(self._volume)

    # ── Üzenet progress animáció ───────────────────────────────────────────────
    def _start_progress(self, total_ms: int):
        self._prog_start = time.monotonic()
        self._prog_total = total_ms / 1000
        self._prog_bar.set_pct(0)
        self._prog_bar.set_colors(GREEN, BLUE)
        self._prog_bar.show()
        self._prog_timer.start()
        if total_ms > 0:
            QTimer.singleShot(total_ms, self._start_dismiss_animation)

    def _start_dismiss_animation(self):
        if not self._overlay_visible:
            return
        self._prog_timer.stop()
        self._prog_bar.set_colors(AMBER, RED)
        self._prog_bar.set_pct(1.0)
        self._dismiss_step = 0
        if self._dismiss_timer is None:
            self._dismiss_timer = QTimer(self)
            self._dismiss_timer.setInterval(16)
            self._dismiss_timer.timeout.connect(self._dismiss_tick)
        self._dismiss_timer.start()

    def _dismiss_tick(self):
        self._dismiss_step += 1
        total_steps = 20
        pct = max(0.0, 1.0 - self._dismiss_step / total_steps)
        self._prog_bar.set_pct(pct)
        if pct <= 0:
            self._dismiss_timer.stop()
            self._do_hide_overlay()

    def _update_progress(self):
        if self._prog_total <= 0:
            return
        elapsed = time.monotonic() - self._prog_start
        pct = min(1.0, elapsed / self._prog_total)
        self._prog_bar.set_pct(pct)
        if pct >= 1.0:
            self._prog_timer.stop()

    # ── Rádió pulzáló animáció ────────────────────────────────────────────────
    def _start_radio_pulse(self):
        self._radio_pulse_step = 0
        if self._radio_pulse_timer is None:
            self._radio_pulse_timer = QTimer(self)
            self._radio_pulse_timer.setInterval(40)
            self._radio_pulse_timer.timeout.connect(self._radio_pulse_tick)
        self._radio_pulse_timer.start()

    def _radio_pulse_tick(self):
        self._radio_pulse_step += 1
        opacity = 0.25 + 0.75 * (math.sin(self._radio_pulse_step * 0.065) + 1) / 2
        self._pulsing_line.set_opacity(opacity)

    # ── Update banner kattintás ────────────────────────────────────────────────
    def _on_update_click(self, event):
        if self._update_click_cb:
            self._update_click_cb()

    # ── Keyboard ───────────────────────────────────────────────────────────────
    def keyPressEvent(self, event):
        from PyQt6.QtCore import Qt as QtCore
        key = event.key()
        if key in (QtCore.Key.Key_Escape, QtCore.Key.Key_F11):
            if self.isFullScreen():
                self.showNormal()
            else:
                self.showFullScreen()
        elif key == QtCore.Key.Key_F4:
            self.close()
        elif event.text() in ("+", "="):
            self._vol_up()
        elif event.text() == "-":
            self._vol_down()

    # ══════════════════════════════════════════════════════════════════════════
    # Thread-safe publikus API
    # ══════════════════════════════════════════════════════════════════════════

    def set_online(self, online: bool):
        self._bridge.set_online_signal.emit(online)

    def set_snap_status(self, text: str):
        self._bridge.set_snap_signal.emit(text)

    def set_institution(self, name: str):
        self._bridge.set_institution_signal.emit(name)

    def set_bells(self, bells: list):
        self._bridge.set_bells_signal.emit(bells)

    def set_cache_status(self, text: str):
        self._bridge.set_cache_signal.emit(text)

    def show_bell_banner(self, show: bool):
        self._bridge.show_bell_banner_signal.emit(show)

    def show_message_overlay(self, text: str, reading_ms: int = 0):
        self._bridge.show_msg_overlay_signal.emit(text, reading_ms)

    def show_radio_overlay(self, title: str = "Iskolarádió"):
        self._bridge.show_radio_overlay_signal.emit(title)

    def hide_overlay(self):
        self._bridge.hide_overlay_signal.emit()

    def show_update_banner(self, text: str, on_click: Optional[Callable] = None):
        self._update_click_cb = on_click
        self._bridge.show_update_banner_signal.emit(text)

    def show_login(self):
        self._bridge.show_login_signal.emit()

    def hide_login(self):
        self._bridge.hide_login_signal.emit()

    def show_pending(self):
        self._bridge.show_pending_signal.emit()

    def hide_pending(self):
        self._bridge.hide_pending_signal.emit()

    def set_login_error(self, msg: str):
        self._bridge.set_login_error_signal.emit(msg)

    def set_volume_display(self, vol: int):
        self._bridge.set_volume_display_signal.emit(vol)

    def update_radio_time(self, seconds_left: int):
        m, s = divmod(max(0, seconds_left), 60)

    # ══════════════════════════════════════════════════════════════════════════
    # Slot implementációk (Qt main thread)
    # ══════════════════════════════════════════════════════════════════════════

    def _do_set_online(self, online: bool):
        self._dot.set_online(online)
        self._lbl_status.setText("Online" if online else "Offline")
        self._lbl_status.setStyleSheet(
            f"color: {'#22c55e' if online else TEXT_DIM}; font-size: 12px; font-weight: 700;"
        )

    def _do_set_snap(self, text: str):
        self._lbl_snap.setText(text)

    def _do_set_institution(self, name: str):
        self._lbl_inst.setText(name)

    def _do_set_bells(self, bells: list):
        self._bells = bells
        self._refresh_next_bell()

    def _do_set_cache(self, text: str):
        self._lbl_cache.setText(text)

    def _do_show_bell_banner(self, show: bool):
        if show:
            self._bell_banner.show()
        else:
            self._bell_banner.hide()

    def _do_show_msg_overlay(self, text: str, reading_ms: int):
        self._lbl_radio_icon.hide()
        self._lbl_radio_title.hide()
        self._pulsing_line.hide()
        self._btn_dismiss.hide()
        if self._radio_pulse_timer:
            self._radio_pulse_timer.stop()

        l = len(text.strip())
        if   l <= 40:  size = 52
        elif l <= 80:  size = 38
        elif l <= 160: size = 28
        else:          size = 21

        self._lbl_msg_text.setStyleSheet(
            f"color: {TEXT}; font-size: {size}px; font-weight: 800;"
        )
        self._lbl_msg_text.setText(text)
        self._lbl_msg_text.show()
        self._prog_bar.show()
        self._overlay_visible = True

        if reading_ms > 0:
            self._start_progress(reading_ms)

        self._overlay.show()
        self._overlay.raise_()
        cw = self.centralWidget()
        if cw:
            self._overlay.setGeometry(0, 0, cw.width(), cw.height())

    def _do_show_radio_overlay(self, title: str):
        self._lbl_msg_text.hide()
        self._prog_bar.hide()
        self._prog_timer.stop()
        if self._dismiss_timer:
            self._dismiss_timer.stop()

        self._lbl_radio_icon.show()
        self._lbl_radio_title.setText(title)
        self._lbl_radio_title.setStyleSheet(
            f"color: {AMBER}; font-size: 48px; font-weight: 900;"
        )
        self._lbl_radio_title.show()
        self._pulsing_line.show()
        self._btn_dismiss.show()
        self._overlay_visible = True

        self._start_radio_pulse()

        self._overlay.show()
        self._overlay.raise_()
        cw = self.centralWidget()
        if cw:
            self._overlay.setGeometry(0, 0, cw.width(), cw.height())

    def _do_hide_overlay(self):
        self._prog_timer.stop()
        if self._dismiss_timer:
            self._dismiss_timer.stop()
        if self._radio_pulse_timer:
            self._radio_pulse_timer.stop()
        self._overlay_visible = False
        self._overlay.hide()

    def _do_show_update_banner(self, text: str):
        self._lbl_update.setText(text)
        self._update_banner.show()

    def _do_show_login(self):
        pass

    def _do_hide_login(self):
        pass

    def _do_show_pending(self):
        self._stack.setCurrentIndex(1)

    def _do_hide_pending(self):
        self._stack.setCurrentIndex(0)

    def _do_set_login_error(self, msg: str):
        pass

    def _do_set_volume(self, vol: int):
        self._volume = vol
        self._lbl_vol.setText(str(vol))