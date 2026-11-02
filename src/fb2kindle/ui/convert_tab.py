"""Вкладка «Конвертация»: выбранные файлы → папка автора."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QRadioButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import core
from .common import button, card, muted, open_folder, ui_call
from .settings_dialog import BookSettings, Choice

IF_EXISTS = [("skip", "Пропустить"), ("replace", "Заменить")]
STATUS_COLOR = {"run": "#004A8F", "done": "#0E5C0E", "skip": "#6B6B6B", "error": "#A4262C", "cancel": "#6B6B6B"}


class ConvertTab(QWidget):
    COLS = ["№", "Книга", "Автор", "Сохранится как", "Статус"]

    def __init__(self, main):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self.s: core.Settings = main.s
        self.books: list[core.Book] = []
        self.jobs: list[core.Job] = []
        self.converter: core.Converter | None = None
        self.folder_touched = bool(self.s.folder_name)
        self.setAcceptDrops(True)
        self._build()
        self._load_settings()

    # ------------------------------------------------------------------ UI --

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)

        bar = QHBoxLayout()
        self.btn_files = button("Добавить файлы…", slot=self.add_files)
        self.btn_folder = button("Добавить папку…", slot=self.add_folder)
        self.btn_remove = button("Убрать выбранные", slot=self.remove_selected)
        self.btn_clear = button("Очистить список", slot=self.clear_list)
        for b in (self.btn_files, self.btn_folder, self.btn_remove, self.btn_clear):
            bar.addWidget(b)
        bar.addStretch(1)
        self.lbl_count = muted("")
        self.lbl_count.setMinimumWidth(170)
        self.lbl_count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar.addWidget(self.lbl_count)
        root.addLayout(bar)

        table_card = card()
        tl = QVBoxLayout(table_card)
        tl.setContentsMargins(1, 1, 1, 1)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(self.COLS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setUniformRowHeights(True)
        h = self.tree.header()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(0, 44)
        self.tree.setColumnWidth(1, 260)
        self.tree.setColumnWidth(2, 170)
        self.tree.setColumnWidth(3, 280)
        self.tree.itemDoubleClicked.connect(self._details)
        tl.addWidget(self.tree)
        self.empty = muted("Перетащите сюда книги FB2 или нажмите «Добавить файлы…» / «Добавить папку…»")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setParent(self.tree.viewport())
        root.addWidget(table_card, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        out = QGroupBox("Куда сохранить")
        g = QGridLayout(out)
        g.setVerticalSpacing(8)
        g.addWidget(QLabel("Папка:"), 0, 0)
        self.ed_out = QLineEdit()
        self.ed_out.textChanged.connect(self._refresh_targets)
        g.addWidget(self.ed_out, 0, 1)
        self.btn_out = button("Обзор…", slot=self.choose_output)
        g.addWidget(self.btn_out, 0, 2)
        self.rb_one = QRadioButton("Папка автора:")
        self.ed_folder = QLineEdit()
        self.ed_folder.setToolTip("Имя подставляется из книг само. Можно исправить — все книги из списка попадут в эту папку.")
        self.ed_folder.textEdited.connect(lambda *_: setattr(self, "folder_touched", True))
        self.ed_folder.textChanged.connect(self._refresh_targets)
        self.rb_auto = QRadioButton("Отдельная папка для каждого автора (автоматически)")
        self.rb_flat = QRadioButton("Отдельные файлы, без папки")
        self.layout_group = QButtonGroup(self)
        for rb in (self.rb_one, self.rb_auto, self.rb_flat):
            self.layout_group.addButton(rb)
            rb.toggled.connect(self._on_layout)
        g.addWidget(self.rb_one, 1, 0)
        g.addWidget(self.ed_folder, 1, 1, 1, 2)
        g.addWidget(self.rb_auto, 2, 0, 1, 3)
        g.addWidget(self.rb_flat, 3, 0, 1, 3)
        ex = QHBoxLayout()
        ex.addWidget(QLabel("Если такой файл уже есть:"))
        self.cb_exists = Choice(IF_EXISTS, self.s.if_exists)
        ex.addWidget(self.cb_exists)
        ex.addStretch(1)
        g.addLayout(ex, 4, 0, 1, 3)
        g.setColumnStretch(1, 1)
        bottom.addWidget(out, 1)

        opts = QGroupBox("Настройки книги")
        ol = QVBoxLayout(opts)
        self.book_settings = BookSettings(self.s, self)
        ol.addWidget(self.book_settings)
        ol.addStretch(1)
        bottom.addWidget(opts, 1)
        root.addLayout(bottom)

        foot = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        foot.addWidget(self.progress, 1)
        self.lbl_progress = QLabel("")
        self.lbl_progress.setMinimumWidth(180)
        foot.addWidget(self.lbl_progress)
        self.btn_open = button("Открыть папку", slot=self.open_output)
        foot.addWidget(self.btn_open)
        self.btn_go = button("Конвертировать", primary=True, slot=self.toggle_convert)
        self.btn_go.setMinimumWidth(150)
        foot.addWidget(self.btn_go)
        root.addLayout(foot)

        self.lock_widgets = [self.btn_files, self.btn_folder, self.btn_remove, self.btn_clear, self.ed_out, self.btn_out,
                             self.rb_one, self.rb_auto, self.rb_flat, self.ed_folder, self.cb_exists, self.book_settings]

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_empty()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_empty()

    def _place_empty(self):
        vp = self.tree.viewport()
        self.empty.setGeometry(0, 0, vp.width(), vp.height())
        self.empty.setVisible(not self.books)

    # ------------------------------------------------------------ Настройки --

    def _load_settings(self):
        s = self.s
        self.ed_out.setText(s.output_dir)
        self.ed_folder.setText(s.folder_name)
        {"one_folder": self.rb_one, "per_author": self.rb_auto, "flat": self.rb_flat}.get(s.layout, self.rb_one).setChecked(True)
        self.cb_exists.set_value(s.if_exists)
        self._on_layout()

    def collect(self) -> core.Settings:
        s = self.s
        s.output_dir = self.ed_out.text().strip()
        s.layout = "one_folder" if self.rb_one.isChecked() else "per_author" if self.rb_auto.isChecked() else "flat"
        s.folder_name = self.ed_folder.text().strip()
        s.if_exists = self.cb_exists.value()
        self.book_settings.collect()
        return s

    def _on_layout(self, *_):
        if self.converter is None:
            self.ed_folder.setEnabled(self.rb_one.isChecked())
        self._refresh_targets()

    # -------------------------------------------------------------- Список --

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self.converter is None:
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)

    def add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Выберите книги", "", "Книги FB2 (*.fb2 *.fb2.zip *.zip);;Все файлы (*.*)")
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Выберите папку с книгами (подпапки тоже просматриваются)")
        if path:
            self.add_paths([path])

    def add_paths(self, paths: list[str]):
        self.lbl_count.setText("Читаю книги…")

        def work():
            files = core.find_books(paths)
            known = {os.path.normcase(str(b.path.resolve())) for b in self.books}
            new = [core.read_metadata(f) for f in files if os.path.normcase(str(f.resolve())) not in known]
            ui_call(lambda: self._on_added(new, len(files)))

        threading.Thread(target=work, daemon=True).start()

    def _on_added(self, new: list[core.Book], found: int):
        if not found:
            QMessageBox.information(self, core.APP_NAME, "Книг FB2 не найдено.")
        self.books.extend(new)
        self.jobs = []
        if not self.folder_touched:
            self.ed_folder.setText(core.most_common_author(self.books))
        self._rebuild()

    def remove_selected(self):
        if self.converter:
            return
        rows = {self.tree.indexOfTopLevelItem(i) for i in self.tree.selectedItems()}
        if not rows:
            return
        self.books = [b for i, b in enumerate(self.books) if i not in rows]
        self.jobs = []
        if not self.folder_touched:
            self.ed_folder.setText(core.most_common_author(self.books))
        self._rebuild()

    def clear_list(self):
        if self.converter:
            return
        self.books, self.jobs = [], []
        self.folder_touched = False
        self.ed_folder.setText("")
        self._rebuild()

    def _rebuild(self):
        self.tree.clear()
        for i, b in enumerate(self.books):
            it = QTreeWidgetItem([str(i + 1), b.display_title, b.author_display, "", "Ожидает"])
            it.setTextAlignment(0, Qt.AlignmentFlag.AlignCenter)
            if b.error:
                it.setText(4, f"Не прочитать описание: {b.error}")
                self._color(it, "error")
            self.tree.addTopLevelItem(it)
        self._refresh_targets()
        n = len(self.books)
        self.lbl_count.setText(f"Книг в списке: {n}" if n else "")
        self.progress.setRange(0, max(1, n))
        self.progress.setValue(0)
        self.lbl_progress.setText("")
        self._place_empty()

    def _color(self, it: QTreeWidgetItem, status: str):
        color = QColor(STATUS_COLOR.get(status, "#1B1B1B"))
        for c in range(len(self.COLS)):
            it.setForeground(c, QBrush(color))

    def _refresh_targets(self, *_):
        if not self.books:
            return
        s = self.collect()
        root = Path(s.output_dir or ".")
        for i, path in enumerate(core.plan_outputs(self.books, s)):
            it = self.tree.topLevelItem(i)
            if it is None:
                continue
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            it.setText(3, str(rel))

    # --------------------------------------------------------- Конвертация --

    def toggle_convert(self):
        if self.converter:
            self.converter.cancel()
            self.btn_go.setEnabled(False)
            self.btn_go.setText("Останавливаю…")
            return
        self.start()

    def start(self):
        if not self.books:
            QMessageBox.information(self, core.APP_NAME, "Сначала добавьте книги.")
            return
        s = self.collect()
        if not s.output_dir:
            QMessageBox.warning(self, core.APP_NAME, "Выберите папку, куда сохранять книги.")
            return
        if s.layout == "one_folder" and not s.folder_name:
            QMessageBox.warning(self, core.APP_NAME, "Впишите имя папки автора или выберите другой вариант сохранения.")
            self.ed_folder.setFocus()
            return
        fbc = self.main.require_fbc()
        if not fbc:
            return
        s.save()
        targets = core.plan_outputs(self.books, s)
        self.jobs = [core.Job(b, t, tag=i) for i, (b, t) in enumerate(zip(self.books, targets))]
        for j in self.jobs:
            self._update(j)
        self.progress.setRange(0, len(self.jobs))
        self._progress()
        self._lock(True)
        self.converter = core.Converter(self.jobs, s.copy(), fbc,
                                        on_update=lambda j: ui_call(lambda: self._on_update(j)),
                                        on_finish=lambda: ui_call(self._on_finished),
                                        index=self.main.conv_index)
        self.converter.start()

    def _on_update(self, job: core.Job):
        self._update(job)
        self._progress()
        if job.status == "done":
            self.main.library_tab.on_external_conversion(job.book.path, job.output)

    def _update(self, job: core.Job):
        it = self.tree.topLevelItem(job.tag)
        if it is None:
            return
        it.setText(4, job.message or "Ожидает")
        self._color(it, job.status)
        if job.status == "run":
            self.tree.scrollToItem(it)

    def _progress(self):
        total = len(self.jobs)
        finished = sum(j.status in ("done", "skip", "error", "cancel") for j in self.jobs)
        errors = sum(j.status == "error" for j in self.jobs)
        self.progress.setValue(finished)
        self.lbl_progress.setText(f"{finished} из {total}" + (f" · ошибок: {errors}" if errors else ""))

    def _on_finished(self):
        self.converter = None
        self._lock(False)
        done = sum(j.status == "done" for j in self.jobs)
        skipped = sum(j.status == "skip" for j in self.jobs)
        errors = sum(j.status == "error" for j in self.jobs)
        parts = [f"Готово: {done}"]
        if skipped:
            parts.append(f"пропущено: {skipped}")
        if errors:
            parts.append(f"ошибок: {errors}")
        self.lbl_progress.setText(", ".join(parts))
        self.main.notify(", ".join(parts))

    def _lock(self, busy: bool):
        for w in self.lock_widgets:
            w.setEnabled(not busy)
        self.btn_go.setEnabled(True)
        self.btn_go.setText("Остановить" if busy else "Конвертировать")
        if not busy:
            self._on_layout()

    @property
    def busy(self) -> bool:
        return self.converter is not None

    def _details(self, it: QTreeWidgetItem, _col: int):
        idx = self.tree.indexOfTopLevelItem(it)
        if idx >= len(self.jobs):
            return
        job = self.jobs[idx]
        if job.status == "done":
            open_folder(job.output.parent)
            return
        if not job.details:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Подробности — {job.book.display_title}")
        dlg.resize(780, 440)
        lay = QVBoxLayout(dlg)
        txt = QPlainTextEdit(f"Файл: {job.book.path}\n\n{job.details}")
        txt.setReadOnly(True)
        txt.setStyleSheet("font-family: Consolas, monospace; font-size: 9pt;")
        lay.addWidget(txt)
        dlg.exec()

    # --------------------------------------------------------------- Разное --

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Куда сохранять книги", self.ed_out.text())
        if path:
            self.ed_out.setText(str(Path(path)))

    def open_output(self):
        s = self.collect()
        target = Path(s.output_dir)
        if s.layout == "one_folder" and s.folder_name and (target / core.safe_name(s.folder_name)).is_dir():
            target = target / core.safe_name(s.folder_name)
        if not target.is_dir():
            QMessageBox.information(self, core.APP_NAME, "Папка пока не создана — она появится после конвертации.")
            return
        open_folder(target)

    def settings_changed(self):
        self._refresh_targets()
