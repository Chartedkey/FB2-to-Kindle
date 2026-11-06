"""Небольшие диалоги трекера; цвета заданы независимо от системной тёмной темы."""
from __future__ import annotations

from copy import deepcopy
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QSpinBox,
    QVBoxLayout, QWidget, QScrollArea)

from ..reading import Entry, MONTHS, PRECISIONS, STATUSES, validate
from ..recommendations import GENRE_TERMS
from .common import bold, muted


def setup_dialog(dialog, title):
    dialog.setObjectName("readingDialog")
    dialog.setWindowTitle(title)
    dialog.setStyleSheet("QDialog#readingDialog { background:#F9F9F9; } "
        "QPushButton { background:#FFFFFF; color:#1B1B1B; border:1px solid #D1D1D1; border-radius:6px; padding:6px 14px; } "
        "QPushButton[primary=\"true\"] { background:#005FB8; color:#FFFFFF; border-color:#005FB8; } "
        "QPlainTextEdit { background:white; color:#1B1B1B; border:1px solid #D1D1D1; border-radius:6px; }")
    dialog.setMinimumWidth(470)


def plain(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class EntryDialog(QDialog):
    def __init__(self, entry: Entry | None = None, parent=None, read=False):
        super().__init__(parent)
        setup_dialog(self, "Моё чтение")
        self.entry = deepcopy(entry) if entry else None
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(bold("Отметка о книге" if entry else "Добавить книгу", 14))
        form = QFormLayout()
        self.title = QLineEdit(entry.title if entry else "")
        self.author = QLineEdit(entry.author if entry else "")
        self.title.setReadOnly(entry is not None)
        self.author.setReadOnly(entry is not None)
        form.addRow("Название", self.title)
        form.addRow("Автор", self.author)
        self.status = QComboBox()
        for k, label in STATUSES.items():
            self.status.addItem(label, k)
        self.status.setCurrentIndex(self.status.findData("read" if read or entry is None else entry.status))
        form.addRow("Статус", self.status)
        self.rating = QComboBox()
        self.rating.addItem("Без оценки", 0)
        for n in range(1, 6):
            self.rating.addItem("★" * n + f"   {n} / 5", n)
        self.rating.setCurrentIndex(entry.rating if entry else 0)
        form.addRow("Моя оценка", self.rating)
        root.addLayout(form)
        self.date_box = QWidget()
        dates = QFormLayout(self.date_box)
        dates.setContentsMargins(0, 0, 0, 0)
        self.precision = QComboBox()
        for k, label in PRECISIONS.items():
            self.precision.addItem(label, k)
        self.precision.setCurrentIndex(self.precision.findData(entry.precision if entry else "unknown"))
        dates.addRow("Когда прочитано", self.precision)
        self.day = QDateEdit(QDate.currentDate())
        self.day.setCalendarPopup(True)
        self.day.setMaximumDate(QDate.currentDate())
        self.day.setDisplayFormat("dd.MM.yyyy")
        self.year = QSpinBox()
        self.year.setRange(1, date.today().year)
        self.year.setValue(date.today().year)
        self.month = QComboBox()
        self.month.addItems(MONTHS)
        self.month.setCurrentIndex(date.today().month - 1)
        self.month_box = QWidget()
        ml = QHBoxLayout(self.month_box)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.addWidget(self.month)
        self.month_year = QSpinBox()
        self.month_year.setRange(1, date.today().year)
        self.month_year.setValue(date.today().year)
        ml.addWidget(self.month_year)
        if entry and entry.finished:
            parts = list(map(int, entry.finished.split("-")))
            self.year.setValue(parts[0])
            self.month_year.setValue(parts[0])
            if len(parts) > 1:
                self.month.setCurrentIndex(parts[1] - 1)
            if len(parts) == 3:
                self.day.setDate(QDate(*parts))
        dates.addRow(self.day)
        dates.addRow(self.month_box)
        dates.addRow(self.year)
        self.date_hint = plain("")
        dates.addRow(self.date_hint)
        root.addWidget(self.date_box)
        root.addWidget(muted("Заметка · необязательно"))
        self.note = QPlainTextEdit(entry.note if entry else "")
        self.note.setMaximumHeight(110)
        root.addWidget(self.note)
        self.error = plain("")
        self.error.setStyleSheet("color:#A4262C;")
        root.addWidget(self.error)
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("Сохранить", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setProperty("primary", True)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.status.currentIndexChanged.connect(self.update_date)
        self.precision.currentIndexChanged.connect(self.update_date)
        self.update_date()

    def update_date(self, *_):
        precision = self.precision.currentData()
        self.date_box.setVisible(self.status.currentData() == "read")
        self.day.setVisible(precision == "day")
        self.month_box.setVisible(precision == "month")
        self.year.setVisible(precision == "year")
        self.date_hint.setText({"unknown": "Учтём в общем числе прочитанных, но не в месяце или году.",
            "year": "Учтём в годовом итоге. По месяцам и кварталам распределять не будем.",
            "month": "Учтём в итогах месяца, квартала и года. Точный день не нужен.",
            "day": "Дата завершения книги; её можно исправить позже."}[precision])

    def accept(self):
        e = deepcopy(self.entry) if self.entry else Entry(self.title.text().strip(), self.author.text().strip())
        e.status = self.status.currentData()
        e.rating = self.rating.currentData()
        e.precision = self.precision.currentData()
        e.finished = {"unknown": "", "year": f"{self.year.value():04d}",
                      "month": f"{self.month_year.value():04d}-{self.month.currentIndex() + 1:02d}",
                      "day": self.day.date().toString("yyyy-MM-dd")}[e.precision]
        e.note = self.note.toPlainText().strip()
        try:
            validate(e)
        except ValueError as err:
            self.error.setText(str(err))
            return
        self.entry = e
        super().accept()


class BulkDialog(QDialog):
    def __init__(self, entries: list[Entry], parent=None):
        super().__init__(parent)
        setup_dialog(self, "Ранее прочитанные книги")
        self.resize(640, 520)
        self.entries = entries
        root = QVBoxLayout(self)
        root.addWidget(plain("Отметьте книги. Сохраним их как прочитанные ранее, без даты и без придуманной оценки."))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Название или автор")
        root.addWidget(self.search)
        self.list = QListWidget()
        for e in entries:
            item = QListWidgetItem(f"{e.title} — {e.author}")
            item.setData(Qt.ItemDataRole.UserRole, e.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.list.addItem(item)
        root.addWidget(self.list)
        self.search.textChanged.connect(self.filter)
        buttons = QDialogButtonBox()
        buttons.addButton("Отметить прочитанными", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def filter(self, text):
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text.casefold() not in item.text().casefold())

    def selected_entries(self):
        keys = {self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked}
        result = []
        for e in self.entries:
            if e.key in keys:
                e = deepcopy(e)
                e.status, e.precision, e.finished = "read", "unknown", ""
                result.append(e)
        return result


class PreferencesDialog(QDialog):
    def __init__(self, prefs: dict, parent=None):
        super().__init__(parent)
        setup_dialog(self, "Предпочтения для рекомендаций")
        self.resize(570, 600)
        root = QVBoxLayout(self)
        root.addWidget(plain("Жанры и исключения задаёте вы. Низкая оценка книги не исключает весь жанр или автора."))
        self.genres, self.excluded = {}, {}
        form = QFormLayout()
        for genre in GENRE_TERMS:
            box = QWidget()
            line = QHBoxLayout(box)
            a, b = QCheckBox("Нравится"), QCheckBox("Не предлагать")
            a.setChecked(genre in prefs.get("genres", []))
            b.setChecked(genre in prefs.get("exclude_genres", []))
            a.toggled.connect(lambda on, other=b: other.setChecked(False) if on else None)
            b.toggled.connect(lambda on, other=a: other.setChecked(False) if on else None)
            line.addWidget(a)
            line.addWidget(b)
            form.addRow(genre, box)
            self.genres[genre], self.excluded[genre] = a, b
        root.addLayout(form)
        root.addWidget(muted("Любимые авторы · каждый с новой строки"))
        self.authors = QPlainTextEdit("\n".join(prefs.get("authors", [])))
        self.authors.setMaximumHeight(90)
        root.addWidget(self.authors)
        root.addWidget(muted("Не предлагать авторов · каждый с новой строки"))
        self.no_authors = QPlainTextEdit("\n".join(prefs.get("exclude_authors", [])))
        self.no_authors.setMaximumHeight(70)
        root.addWidget(self.no_authors)
        buttons = QDialogButtonBox()
        buttons.addButton("Сохранить", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("Отмена", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self):
        return {"genres": [g for g, c in self.genres.items() if c.isChecked()],
                "exclude_genres": [g for g, c in self.excluded.items() if c.isChecked()],
                "authors": list(dict.fromkeys(s.strip() for s in self.authors.toPlainText().splitlines() if s.strip())),
                "exclude_authors": list(dict.fromkeys(s.strip() for s in self.no_authors.toPlainText().splitlines() if s.strip()))}
