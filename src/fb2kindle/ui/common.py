"""Общие детали интерфейса: стиль, вызовы из фоновых потоков, обложки, значки статуса."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from .. import core

ACCENT = "#005FB8"
COVER_COLORS = ["#2D4A6B", "#6B2D3A", "#3D5A3A", "#5A4A2D", "#4A3D6B", "#2D5A5A"]

# статус → (текст, фон, цвет текста)
STATUS_STYLE = {
    "done": ("Готово", "#DFF6DD", "#0E5C0E"),
    "none": ("Не сконвертирована", "#EFEFEF", "#4D4D4D"),
    "changed": ("FB2 изменился", "#FFF4CE", "#7A4D00"),
    "queued": ("В очереди", "#E3EFFB", "#004A8F"),
    "run": ("Конвертируется…", "#E3EFFB", "#004A8F"),
    "error": ("Ошибка", "#FDE7E9", "#A4262C"),
}

QSS = """
QWidget { font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif; font-size: 10pt; color: #1B1B1B; }
QMainWindow, QWidget#page { background: #F3F3F3; }
QDialog { background: #F9F9F9; }
QTabWidget::pane { border: none; background: #F3F3F3; }
QTabBar { background: #FFFFFF; }
QTabBar::tab { background: transparent; padding: 10px 18px; margin: 0 2px; border: none;
               border-bottom: 3px solid transparent; color: #4D4D4D; }
QTabBar::tab:hover { color: #1B1B1B; }
QTabBar::tab:selected { color: #1B1B1B; font-weight: 600; border-bottom: 3px solid #005FB8; }
QFrame#card { background: #FFFFFF; border: 1px solid #E5E5E5; border-radius: 10px; }
QFrame#selbar { background: #EEF5FC; border: 1px solid #C9DFF5; border-radius: 8px; }
QFrame#line { background: #EDEDED; max-height: 1px; min-height: 1px; border: none; }
QPushButton { background: #FFFFFF; border: 1px solid #D1D1D1; border-radius: 6px; padding: 6px 14px; }
QPushButton:hover { background: #F6F6F6; }
QPushButton:pressed { background: #EDEDED; }
QPushButton:disabled { color: #8A8A8A; background: #F7F7F7; border-color: #E0E0E0; }
QToolButton { background: #FFFFFF; border: 1px solid #D1D1D1; border-radius: 6px; padding: 6px 10px; padding-right: 26px; }
QToolButton:hover { background: #F6F6F6; }
QToolButton:disabled { color: #8A8A8A; background: #F7F7F7; border-color: #E0E0E0; }
QToolButton::menu-button { border: none; border-left: 1px solid #D1D1D1; width: 22px; }
QToolButton::menu-arrow { image: url("%ARROW%"); width: 12px; height: 12px; }
QMenu { background: #FFFFFF; border: 1px solid #D1D1D1; padding: 4px; }
QMenu::item { padding: 6px 18px; border-radius: 4px; }
QMenu::item:selected { background: #E3EFFB; color: #1B1B1B; }
QPushButton[primary="true"] { background: #005FB8; border-color: #005FB8; color: #FFFFFF; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #004E99; }
QPushButton[primary="true"]:disabled { background: #9DBFE3; border-color: #9DBFE3; color: #FFFFFF; }
QPushButton[chip="true"] { border-radius: 14px; padding: 4px 12px; }
QPushButton[chip="true"]:checked { background: #1B1B1B; border-color: #1B1B1B; color: #FFFFFF; }
QLineEdit, QComboBox, QSpinBox { border: 1px solid #D1D1D1; border-radius: 6px; padding: 5px 8px; background: #FFFFFF;
                                 selection-background-color: #005FB8; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #005FB8; }
QLineEdit:read-only { background: #FAFAFA; color: #333333; }
QComboBox::drop-down { border: none; width: 24px; }
QComboBox::down-arrow { image: url("%ARROW%"); width: 14px; height: 14px; }
QComboBox QAbstractItemView { border: 1px solid #D1D1D1; background: #FFFFFF; selection-background-color: #E3EFFB;
                              selection-color: #1B1B1B; outline: 0; }
QListWidget { border: none; background: transparent; outline: 0; }
QListWidget::item { border-radius: 6px; margin: 1px 0; }
QListWidget::item:hover { background: #F4F4F4; }
QListWidget::item:selected { background: #E3EFFB; }
QTreeWidget { border: none; background: #FFFFFF; outline: 0; alternate-background-color: #FAFAFA; }
QTreeWidget::item { padding: 4px 2px; }
QTreeWidget::item:selected { background: #E3EFFB; color: #1B1B1B; }
QHeaderView::section { background: #FAFAFA; border: none; border-bottom: 1px solid #E5E5E5; padding: 6px 8px;
                       font-weight: 600; color: #333333; }
QProgressBar { border: none; background: #E5E5E5; border-radius: 3px; max-height: 6px; min-height: 6px; }
QProgressBar::chunk { background: #005FB8; border-radius: 3px; }
QGroupBox { background: #FFFFFF; border: 1px solid #E5E5E5; border-radius: 10px; margin-top: 22px; padding: 12px 12px 10px 12px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 4px; top: 2px; font-weight: 600; color: #1B1B1B; }
QStatusBar { background: #FFFFFF; border-top: 1px solid #E5E5E5; }
QStatusBar QLabel { color: #4D4D4D; }
QLabel[muted="true"] { color: #5F5F5F; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #C8C8C8; border-radius: 3px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #A8A8A8; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #C8C8C8; border-radius: 3px; min-width: 30px; }
QSplitter::handle { background: transparent; }
QToolTip { background: #FFFFFF; color: #1B1B1B; border: 1px solid #D1D1D1; padding: 4px 6px; }
QCheckBox, QRadioButton { spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator, QListView::indicator, QTreeView::indicator {
    width: 16px; height: 16px; background: #FFFFFF; border: 1px solid #7A7A7A; image: none;
}
QCheckBox::indicator, QListView::indicator, QTreeView::indicator { border-radius: 3px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:checked, QListView::indicator:checked, QTreeView::indicator:checked { image: url("%CHECK%"); }
QRadioButton::indicator:checked { image: url("%DOT%"); }
QCheckBox::indicator:indeterminate, QListView::indicator:indeterminate, QTreeView::indicator:indeterminate { image: url("%MIXED%"); }
QCheckBox::indicator:hover, QRadioButton::indicator:hover, QListView::indicator:hover, QTreeView::indicator:hover { border-color: #1B1B1B; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { border-color: #1B1B1B; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled, QListView::indicator:disabled, QTreeView::indicator:disabled { border-color: #C0C0C0; }
QCheckBox:disabled, QRadioButton:disabled { color: #8A8A8A; }
"""


def stylesheet() -> str:
    style = QSS
    for token, filename in (("ARROW", "arrow-down.png"), ("CHECK", "indicator-check.png"),
                            ("DOT", "indicator-dot.png"), ("MIXED", "indicator-mixed.png")):
        style = style.replace(f"%{token}%", (core.RES_DIR / "assets" / filename).as_posix())
    return style


class Bridge(QObject):
    """Передаёт вызовы из фоновых потоков в поток окна."""
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(lambda fn: fn())


_bridge: Bridge | None = None


def ui_call(fn) -> None:
    """Выполнить fn в потоке окна (можно вызывать из любого потока)."""
    global _bridge
    if _bridge is None:
        _bridge = Bridge()
    _bridge.call.emit(fn)


def init_bridge() -> None:
    global _bridge
    if _bridge is None:
        _bridge = Bridge()


def flags(*fs) -> int:
    """Флаги Qt для drawText одним числом (разные типы флагов нельзя складывать напрямую)."""
    v = 0
    for f in fs:
        v |= f.value if hasattr(f, "value") else int(f)
    return v


def button(text: str, primary: bool = False, chip: bool = False, slot=None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setProperty("primary", True)
    if chip:
        b.setProperty("chip", True)
        b.setCheckable(True)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def set_primary(b: QPushButton, primary: bool) -> None:
    if bool(b.property("primary")) != primary:
        b.setProperty("primary", primary)
        b.style().unpolish(b)
        b.style().polish(b)


def muted(text: str = "", size: float | None = None) -> QLabel:
    lab = QLabel(text)
    lab.setProperty("muted", True)
    if size:
        f = lab.font()
        f.setPointSizeF(size)
        lab.setFont(f)
    return lab


def bold(text: str = "", size: float = 10) -> QLabel:
    lab = QLabel(text)
    f = lab.font()
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.DemiBold)
    lab.setFont(f)
    return lab


def style_chip(label: QLabel, status: str, text: str | None = None) -> None:
    t, bg, fg = STATUS_STYLE.get(status, STATUS_STYLE["none"])
    label.setText(text or t)
    label.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:10px; padding:2px 10px; font-size:9pt;")


def cover_placeholder(title: str, author: str, w: int, h: int, seed: int = 0) -> QPixmap:
    """Обложка-заглушка: цветной прямоугольник с автором и названием."""
    pm = QPixmap(w, h)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), max(2, w / 40), max(2, w / 40))
    p.fillPath(path, QColor(COVER_COLORS[seed % len(COVER_COLORS)]))
    pad = max(3, w // 10)
    p.setPen(QColor(255, 255, 255, 220))
    if w >= 80 and author:
        f = QFont(p.font())
        f.setPixelSize(max(8, w // 14))
        p.setFont(f)
        p.drawText(QRectF(pad, pad, w - 2 * pad, h / 3), flags(Qt.TextFlag.TextWordWrap, Qt.AlignmentFlag.AlignTop), author)
    f = QFont(p.font())
    f.setPixelSize(max(6, w // (8 if w >= 80 else 6)))
    f.setWeight(QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(QColor("#FFFFFF"))
    p.drawText(QRectF(pad, h / 3, w - 2 * pad, h * 2 / 3 - pad), flags(Qt.TextFlag.TextWordWrap, Qt.AlignmentFlag.AlignBottom), title)
    p.end()
    return pm


def rounded(pm: QPixmap, w: int, h: int, radius: float = 3) -> QPixmap:
    """Картинка, вписанная в w×h с закруглёнными углами."""
    src = pm.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
    out = QPixmap(w, h)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(int((w - src.width()) / 2), int((h - src.height()) / 2), src)
    p.end()
    return out


def avatar(initials: str, size: int, photo: QPixmap | None = None, bg: str = "#E8E8E8", fg: str = "#4D4D4D") -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(QRectF(0, 0, size, size))
    p.setClipPath(path)
    if photo and not photo.isNull():
        src = photo.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        p.drawPixmap(int((size - src.width()) / 2), 0, src)
    else:
        p.fillPath(path, QColor(bg))
        f = QFont(p.font())
        f.setPixelSize(int(size * 0.36))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(fg))
        p.drawText(QRectF(0, 0, size, size), flags(Qt.AlignmentFlag.AlignCenter), initials)
    p.end()
    return pm


def card() -> QWidget:
    from PySide6.QtWidgets import QFrame
    f = QFrame()
    f.setObjectName("card")
    return f


def hline() -> QWidget:
    from PySide6.QtWidgets import QFrame
    f = QFrame()
    f.setObjectName("line")
    return f


def open_folder(path: Path) -> None:
    if core.IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])


def show_in_explorer(path: Path) -> None:
    """Открыть папку и выделить в ней файл."""
    if core.IS_WINDOWS and path.exists():
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        open_folder(path.parent if path.suffix else path)
