"""Настройки книги: основные (встроены во вкладку «Конвертация») и окно «Дополнительно»."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout,
                               QLabel, QSlider, QVBoxLayout, QWidget)

from .. import core
from .common import button, hline

FOOTNOTES = [("float", "Всплывающие"), ("floatRenumbered", "Всплывающие, новая нумерация"), ("default", "Обычные (переход в конец книги)")]
TOC = [("before", "В начале книги"), ("after", "В конце книги"), ("none", "Не добавлять")]
TITLES = [("plain", "Только название"), ("series", "С серией: (Серия 02) Название")]
AUTHORS = [("first_last", "Имя Фамилия"), ("last_first", "Фамилия Имя")]
PARAGRAPHS = [("indent", "Отступ первой строки"), ("spacing", "Интервал между абзацами")]
ALIGN = [("justify", "По ширине"), ("left", "По левому краю")]
KIND = [(False, "Документы (обложки видны)"), (True, "Книги")]
FORMATS = [("kfx", "KFX (рекомендуется)"), ("azw8", "AZW8")]
WORKERS = [(0, "Автоматически"), (1, "1"), (2, "2"), (4, "4"), (6, "6")]


class Choice(QComboBox):
    """Выпадающий список: хранит значения настроек, показывает подписи."""

    def __init__(self, options, value=None, tip: str = ""):
        super().__init__()
        self.options = options
        for _v, label in options:
            self.addItem(label)
        self.set_value(value if value is not None else options[0][0])
        if tip:
            self.setToolTip(tip)

    def set_value(self, value):
        for i, (v, _l) in enumerate(self.options):
            if v == value:
                self.setCurrentIndex(i)
                return
        self.setCurrentIndex(0)

    def value(self):
        return self.options[max(0, self.currentIndex())][0]


def check(text: str, tip: str = "") -> QCheckBox:
    c = QCheckBox(text)
    if tip:
        c.setToolTip(tip)
    return c


class BookSettings(QWidget):
    """Основные настройки книги (общие для обеих вкладок)."""

    def __init__(self, s: core.Settings, parent=None):
        super().__init__(parent)
        self.s = s
        form = QGridLayout(self)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        self.cb_footnotes = Choice(FOOTNOTES, s.footnotes, "Всплывающие — сноска открывается окошком поверх страницы.")
        self.cb_toc = Choice(TOC, s.toc_page, "Страница с содержанием. Меню «Перейти» на Kindle работает всегда.")
        self.cb_title = Choice(TITLES, s.title_style, "Как книга подписана в библиотеке Kindle. На имя файла не влияет.")
        self.cb_author = Choice(AUTHORS, s.author_style, "Влияет на сортировку по автору на Kindle.")
        rows = [("Сноски:", self.cb_footnotes), ("Оглавление внутри книги:", self.cb_toc),
                ("Название в библиотеке Kindle:", self.cb_title), ("Автор в библиотеке Kindle:", self.cb_author)]
        for i, (label, w) in enumerate(rows):
            form.addWidget(QLabel(label), i, 0)
            form.addWidget(w, i, 1)
        self.ch_hyph = check("Переносы слов", "Без переносов при выравнивании по ширине между словами появляются большие пробелы.")
        self.ch_chap = check("Каждая глава с новой страницы", "Части книги всегда начинаются с новой страницы, галочка — для глав внутри частей.")
        self.ch_cover = check("Создать обложку, если её нет", "Простая обложка с автором и названием.")
        row = len(rows)
        form.addWidget(self.ch_hyph, row, 0)
        form.addWidget(self.ch_chap, row, 1)
        form.addWidget(self.ch_cover, row + 1, 0, 1, 2)
        self.btn_adv = button("Дополнительно…", slot=self.open_advanced)
        form.addWidget(self.btn_adv, row + 2, 0, alignment=Qt.AlignmentFlag.AlignLeft)
        form.setColumnStretch(1, 1)
        self.load()
        for cb in (self.cb_footnotes, self.cb_toc, self.cb_title, self.cb_author):
            cb.currentIndexChanged.connect(lambda *_: self.collect())
        for ch in (self.ch_hyph, self.ch_chap, self.ch_cover):
            ch.toggled.connect(lambda *_: self.collect())

    def load(self):
        s = self.s
        self.cb_footnotes.set_value(s.footnotes)
        self.cb_toc.set_value(s.toc_page)
        self.cb_title.set_value(s.title_style)
        self.cb_author.set_value(s.author_style)
        self.ch_hyph.setChecked(s.hyphenation)
        self.ch_chap.setChecked(s.chapter_new_page)
        self.ch_cover.setChecked(s.generate_cover)

    def collect(self):
        s = self.s
        s.footnotes = self.cb_footnotes.value()
        s.toc_page = self.cb_toc.value()
        s.title_style = self.cb_title.value()
        s.author_style = self.cb_author.value()
        s.hyphenation = self.ch_hyph.isChecked()
        s.chapter_new_page = self.ch_chap.isChecked()
        s.generate_cover = self.ch_cover.isChecked()

    def open_advanced(self):
        self.collect()
        dlg = AdvancedDialog(self.s, self.window())
        if dlg.exec():
            self.s.save()
            self.window().settings_changed()


class AdvancedDialog(QDialog):
    def __init__(self, s: core.Settings, parent=None):
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("Дополнительные настройки")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        self.ch_annot = check("Аннотация отдельной страницей", "Описание книги из FB2 — в начале книги.")
        self.ch_drop = check("Буквица в начале глав", "Первая буква главы крупно, как в бумажных книгах.")
        self.ch_dash = check("Исправлять тире в диалогах", "Дефисы и короткие тире в репликах превращаются в длинное тире «—».")
        self.ch_vign = check("Украшения у заголовков", "Небольшие орнаменты под названием книги и заголовками глав.")
        for c in (self.ch_annot, self.ch_drop, self.ch_dash, self.ch_vign):
            lay.addWidget(c)
        lay.addSpacing(6)
        lay.addWidget(hline())
        lay.addSpacing(6)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.cb_par = Choice(PARAGRAPHS, s.paragraph_style)
        self.cb_align = Choice(ALIGN, s.text_align)
        self.cb_kind = Choice(KIND, s.as_ebook, "«Документы» — Kindle сам показывает обложку закинутой по кабелю книги. "
                                               "«Книги» — книга в разделе книг, но обложка может не отображаться.")
        self.cb_fmt = Choice(FORMATS, s.output_format, "AZW8 — тот же KFX с другим расширением.")
        self.cb_workers = Choice(WORKERS, s.workers, "Сколько книг обрабатывать одновременно.")
        form.addRow("Абзацы:", self.cb_par)
        form.addRow("Выравнивание текста:", self.cb_align)
        form.addRow("Показывать на Kindle как:", self.cb_kind)
        form.addRow("Формат файла:", self.cb_fmt)
        form.addRow("Конвертировать одновременно:", self.cb_workers)
        q = QHBoxLayout()
        self.sl_q = QSlider(Qt.Orientation.Horizontal)
        self.sl_q.setRange(40, 100)
        self.lbl_q = QLabel()
        self.lbl_q.setMinimumWidth(28)
        self.sl_q.valueChanged.connect(lambda v: self.lbl_q.setText(str(v)))
        self.sl_q.setToolTip("Меньше — файл легче, картинки хуже. 90 — хороший баланс.")
        q.addWidget(self.sl_q)
        q.addWidget(self.lbl_q)
        form.addRow("Качество картинок:", q)
        lay.addLayout(form)
        lay.addSpacing(10)
        bb = QDialogButtonBox()
        self.btn_reset = button("Сбросить", slot=self.reset)
        bb.addButton(self.btn_reset, QDialogButtonBox.ButtonRole.ResetRole)
        ok = button("Готово", primary=True)
        bb.addButton(ok, QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(button("Отмена"), QDialogButtonBox.ButtonRole.RejectRole)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.load(s)

    def load(self, s: core.Settings):
        self.ch_annot.setChecked(s.annotation)
        self.ch_drop.setChecked(s.dropcaps)
        self.ch_dash.setChecked(s.fix_dashes)
        self.ch_vign.setChecked(s.vignettes)
        self.cb_par.set_value(s.paragraph_style)
        self.cb_align.set_value(s.text_align)
        self.cb_kind.set_value(s.as_ebook)
        self.cb_fmt.set_value(s.output_format)
        self.cb_workers.set_value(s.workers)
        self.sl_q.setValue(s.jpeg_quality)
        self.lbl_q.setText(str(s.jpeg_quality))

    def reset(self):
        self.load(core.Settings())

    def accept(self):
        s = self.s
        s.annotation = self.ch_annot.isChecked()
        s.dropcaps = self.ch_drop.isChecked()
        s.fix_dashes = self.ch_dash.isChecked()
        s.vignettes = self.ch_vign.isChecked()
        s.paragraph_style = self.cb_par.value()
        s.text_align = self.cb_align.value()
        s.as_ebook = self.cb_kind.value()
        s.output_format = self.cb_fmt.value()
        s.workers = self.cb_workers.value()
        s.jpeg_quality = self.sl_q.value()
        super().accept()
