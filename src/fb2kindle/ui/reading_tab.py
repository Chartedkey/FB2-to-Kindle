"""Моё чтение: списки, статистика, отчёты и локальные редакционные рекомендации."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from html import escape
from pathlib import Path
import threading
import time
import weakref

from PySide6.QtCore import Qt, QTimer, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QProgressBar, QPushButton, QScrollArea, QSpinBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import core, editorial, library, reading, recommendations
from ..reading_covers import CoverLoader
from .common import bold as base_bold, button, card, muted, ui_call, cover_placeholder, rounded
from .reading_dialogs import BulkDialog, EntryDialog, PreferencesDialog, plain, setup_dialog


def bold(text, size=10):
    label = base_bold(text, size)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setStyleSheet(f"font-size:{size}pt; font-weight:600;")
    return label


class ReadingTab(QWidget):
    def __init__(self, main, store: reading.ReadingStore, catalog: editorial.Catalog | None, catalog_error=""):
        super().__init__()
        self.main, self.store, self.catalog = main, store, catalog
        self.catalog_error = catalog_error
        self.setObjectName("page")
        self.view = "overview"
        self.entries = []
        self.by_key = {}
        self.by_path = {}
        self.candidates = []
        self.undo_entry = None
        self.undo_key = ""
        self.worker = None
        self.cancel = threading.Event()
        self.closing = False
        self.cover_loader = CoverLoader(self)
        self.cover_loader.ready.connect(self.cover_ready)
        self.cover_labels, self.cover_trees = {}, {}
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 10)
        root.setSpacing(16)
        self.nav = QListWidget()
        self.nav.setFixedWidth(195)
        self.nav.setSpacing(3)
        self.nav.setStyleSheet("QListWidget::item { padding:8px 10px; } QListWidget::item:selected { background:#E3EFFB; color:#004A8F; }")
        for key, title in (("overview", "Обзор"), ("wish", "Хочу прочитать"), ("reading", "Читаю"),
                           ("read", "Прочитано"), ("paused", "Отложено"), ("stats", "Статистика"), ("recs", "Рекомендации")):
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(180, 36))
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.nav.addItem(item)
        self.nav.currentItemChanged.connect(self.navigate)
        root.addWidget(self.nav)
        right = QVBoxLayout()
        title = QHBoxLayout()
        title.addWidget(bold("Моё чтение", 18))
        title.addStretch(1)
        self.btn_undo = button("Отменить последнюю отметку", slot=self.undo)
        self.btn_undo.setEnabled(False)
        title.addWidget(self.btn_undo)
        title.addWidget(button("Добавить книгу…", slot=lambda: self.edit_entry()))
        title.addWidget(button("Резервная копия…", slot=self.backup))
        right.addLayout(title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        right.addWidget(self.scroll, 1)
        root.addLayout(right, 1)
        self.nav.setCurrentRow(0)
        self.refresh()
        self.network_timer = QTimer(self)
        self.network_timer.setInterval(60_000)
        self.network_timer.timeout.connect(self.network_tick)
        self.network_timer.start()
        QTimer.singleShot(5000, self.network_tick)

    def navigate(self, item, _previous=None):
        if item:
            self.view = item.data(Qt.ItemDataRole.UserRole)
            self.refresh()

    def go(self, key):
        for i in range(self.nav.count()):
            if self.nav.item(i).data(Qt.ItemDataRole.UserRole) == key:
                self.nav.setCurrentRow(i)
                if self.view != key:
                    self.view = key
                    self.refresh()
                return

    def refresh(self):
        if self.closing:
            return
        try:
            self.entries = self.store.all()
            self.by_key = {e.key: e for e in self.entries}
            self.by_path = {p: e for e in self.entries for p in e.paths}
            self.local_cover_paths = {e.key: e.paths for e in self.library_entries()}
            try:
                self.remote_cover_urls = {c["key"]: c["cover_url"] for c in self.catalog.candidates()
                                          if c.get("cover_url")} if self.catalog else {}
            except Exception:
                self.remote_cover_urls = {}
            self.cover_labels, self.cover_trees = {}, {}
            for i in range(self.nav.count()):
                it = self.nav.item(i)
                k = it.data(Qt.ItemDataRole.UserRole)
                if k in reading.STATUSES:
                    n = sum(e.status == k for e in self.entries)
                    it.setText(f"{reading.STATUSES[k]}   {n}")
            old = self.scroll.takeWidget()
            if old:
                old.deleteLater()
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.setContentsMargins(0, 0, 8, 12)
            lay.setSpacing(14)
            self.scroll.setWidget(page)
            if self.view in reading.STATUSES:
                self.build_list(lay)
            elif self.view == "recs":
                self.build_recs(lay)
            elif self.view == "stats":
                self.build_stats(lay)
            else:
                self.build_overview(lay)
            lay.addStretch(1)
        except Exception as e:
            self.main.notify(f"Не удалось прочитать историю: {e}", 15000)

    def entry_for_book(self, book, author=""):
        key = reading.Entry.from_book(book, author).key
        return self.by_key.get(key) or self.by_path.get(str(book.path))

    def library_entries(self):
        return [reading.Entry.from_book(b, g.name) for g in self.main.library_tab.groups for b in g.books if not b.error]

    def changed(self):
        self.refresh()
        tab = self.main.library_tab
        for row in list(tab.book_rows.values()):
            row.refresh_reading()
        from PySide6.QtCore import QSize
        for i in range(tab.lst_books.count()):
            item = tab.lst_books.item(i)
            row = tab.book_rows.get(item.data(Qt.ItemDataRole.UserRole))
            if row:
                item.setSizeHint(QSize(100, 82 if not row.reading_chip.isHidden() else 66))
        tab.refresh_reading_detail()

    def edit_book(self, book, author="", read=False):
        existing = self.entry_for_book(book, author)
        entry = deepcopy(existing) if existing else reading.Entry.from_book(book, author)
        if str(book.path) not in entry.paths:
            entry.paths.append(str(book.path))
        self.edit_entry(entry, read=read)

    def edit_entry(self, entry=None, read=False):
        dialog = EntryDialog(entry, self, read=read)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.save_entry(dialog.entry)

    def save_entry(self, entry):
        try:
            previous = self.store.get(entry.key)
            if previous:
                for attr in ("genres", "paths", "refs"):
                    setattr(entry, attr, list(dict.fromkeys(getattr(previous, attr) + getattr(entry, attr))))
                if not entry.series:
                    entry.series, entry.series_num = previous.series, previous.series_num
                entry.cover_url = entry.cover_url or previous.cover_url
            self.store.save(entry)
        except Exception as e:
            QMessageBox.warning(self, core.APP_NAME, f"Не удалось сохранить отметку.\n{e}")
            return False
        self.undo_entry, self.undo_key = previous, entry.key
        self.btn_undo.setEnabled(True)
        self.changed()
        self.main.notify("Отметка сохранена. Списки и рекомендации обновлены.")
        return True

    def undo(self):
        if not self.undo_key:
            return
        try:
            if self.undo_entry:
                self.store.save(self.undo_entry)
            else:
                self.store.remove(self.undo_key)
        except Exception as e:
            QMessageBox.warning(self, core.APP_NAME, str(e))
            return
        self.undo_key, self.undo_entry = "", None
        self.btn_undo.setEnabled(False)
        self.changed()
        self.main.notify("Последняя отметка отменена")

    def summary(self, lay, year=None):
        today = date.today()
        year = year or today.year
        row = QHBoxLayout()
        values = [(f"{reading.MONTHS[today.month - 1]} {today.year}", len(reading.completed(self.entries, today.year, today.month)), "С известным месяцем"),
                  (f"За {year} год", len(reading.completed(self.entries, year)), "Включая записи «только год»"),
                  ("Всего прочитано", len(reading.completed(self.entries)), "Включая книги без даты")]
        for title, n, hint in values:
            c = card()
            l = QVBoxLayout(c)
            l.setContentsMargins(18, 14, 18, 14)
            l.addWidget(muted(title))
            l.addWidget(bold(str(n), 25))
            l.addWidget(muted(hint, 8.5))
            row.addWidget(c)
        lay.addLayout(row)

    def build_overview(self, lay):
        self.summary(lay)
        unseen = self.due_reports()
        if unseen:
            c = card()
            line = QHBoxLayout(c)
            line.addWidget(plain("Готовы итоги завершившегося периода."))
            for key, year, quarter in unseen:
                line.addWidget(button(f"{quarter} квартал {year}" if quarter else f"{year} год",
                    slot=lambda _=False, y=year, q=quarter, k=key: self.show_report(y, q, k)))
            lay.addWidget(c)
        lay.addWidget(bold("Сейчас читаю", 13))
        active = [e for e in self.entries if e.status == "reading"]
        if not active:
            lay.addWidget(muted("Выберите книгу в библиотеке или добавьте её вручную. Файл необязателен."))
        for e in active[:4]:
            lay.addWidget(self.entry_card(e))
        lay.addWidget(bold("Недавно прочитано", 13))
        recent = sorted(reading.completed(self.entries), key=lambda e: e.finished or "", reverse=True)
        for e in recent[:5]:
            lay.addWidget(self.entry_card(e))
        if not recent:
            lay.addWidget(muted("Здесь появятся ваши прочитанные книги. Ранее прочитанное можно добавить без точной даты."))
        row = QHBoxLayout()
        row.addWidget(button("Добавить ранее прочитанное…", slot=self.bulk))
        row.addWidget(button("Статистика и итоги", slot=lambda: self.go("stats")))
        row.addStretch(1)
        lay.addLayout(row)

    def due_reports(self):
        periods = [p for p in reading.previous_periods(date.today()) if reading.completed(self.entries, p[1], quarter=p[2])]
        return [p for p in periods if p[0] not in self.store.pref("reports_seen", [])]

    def entry_card(self, e):
        c = card()
        row = QHBoxLayout(c)
        row.addWidget(self.cover_label(e))
        col = QVBoxLayout()
        col.addWidget(plain(e.title))
        col.addWidget(plain(e.author))
        detail = " · ".join(s for s in (reading.STATUSES[e.status], "★" * e.rating, reading.date_label(e)) if s)
        label = plain(detail)
        label.setStyleSheet("color:#5F5F5F;")
        col.addWidget(label)
        if e.note:
            col.addWidget(plain(e.note[:200]))
        row.addLayout(col, 1)
        row.addWidget(button("Изменить…", slot=lambda: self.edit_entry(e)))
        row.addWidget(button("Найти в библиотеке", slot=lambda: self.find_in_library(e.title, e.author)))
        return c

    def build_list(self, lay):
        header = QHBoxLayout()
        header.addWidget(bold(reading.STATUSES[self.view], 15))
        header.addStretch(1)
        header.addWidget(button("Ранее прочитанное…", slot=self.bulk))
        lay.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию или автору")
        lay.addWidget(self.search)
        tree = QTreeWidget()
        tree.setColumnCount(5)
        tree.setHeaderLabels(["Книга", "Автор", "Оценка", "Когда прочитано", "Заметка"])
        tree.setRootIsDecorated(False)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        tree.setMinimumHeight(300)
        tree.setIconSize(QSize(38, 56))
        tree_entries = {}
        for e in self.entries:
            if e.status != self.view:
                continue
            item = QTreeWidgetItem([e.title, e.author, "★" * e.rating or "—", reading.date_label(e), e.note.replace("\n", " ")[:80]])
            item.setData(0, Qt.ItemDataRole.UserRole, e.key)
            item.setToolTip(4, e.note)
            item.setIcon(0, QIcon(cover_placeholder(e.title, e.author, 38, 56, sum(map(ord, e.title)))))
            item.setSizeHint(0, QSize(180, 68))
            tree_entries[e.key] = e
            tree.addTopLevelItem(item)
        for col in range(4):
            tree.resizeColumnToContents(col)
        tree.setColumnWidth(0, min(300, max(180, tree.columnWidth(0))))
        tree.setColumnWidth(1, min(220, max(150, tree.columnWidth(1))))
        lay.addWidget(tree)
        def visible_covers():
            try:
                for i in range(tree.topLevelItemCount()):
                    item = tree.topLevelItem(i)
                    if item.isHidden() or not tree.visualItemRect(item).intersects(tree.viewport().rect()):
                        continue
                    entry = tree_entries[item.data(0, Qt.ItemDataRole.UserRole)]
                    key, image = self.request_cover(entry)
                    if image is not None:
                        item.setIcon(0, QIcon(rounded(QPixmap.fromImage(image), 38, 56)))
                    elif key:
                        bindings = self.cover_trees.setdefault(key, [])
                        if not any(ref() is tree and entry_key == entry.key for ref, entry_key in bindings):
                            bindings.append((weakref.ref(tree), entry.key))
            except RuntimeError:
                pass  # вкладка уже перестроена
        tree.verticalScrollBar().valueChanged.connect(lambda *_: visible_covers())
        QTimer.singleShot(100, visible_covers)
        def filter_rows(text):
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                item.setHidden(text.casefold() not in (item.text(0) + " " + item.text(1)).casefold())
            QTimer.singleShot(0, visible_covers)
        self.search.textChanged.connect(filter_rows)
        def selected():
            item = tree.currentItem()
            return self.by_key.get(item.data(0, Qt.ItemDataRole.UserRole)) if item else None
        tree.itemDoubleClicked.connect(lambda *_: self.edit_entry(selected()) if selected() else None)
        row = QHBoxLayout()
        row.addWidget(button("Изменить отметку…", slot=lambda: self.edit_entry(selected()) if selected() else None))
        row.addWidget(button("Найти в библиотеке", slot=lambda: self.find_in_library(selected().title, selected().author) if selected() else None))
        row.addWidget(button("Удалить отметку…", slot=lambda: self.remove_entry(selected()) if selected() else None))
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(muted("Неизвестные даты учитываются только в общем числе прочитанных. Файлы книг не изменяются."))

    def remove_entry(self, entry):
        if QMessageBox.question(self, core.APP_NAME, "Удалить только отметку и заметку об этой книге? Файл книги останется.") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.remove(entry.key)
        except Exception as e:
            QMessageBox.warning(self, core.APP_NAME, str(e))
            return
        self.undo_key, self.undo_entry = entry.key, entry
        self.btn_undo.setEnabled(True)
        self.changed()

    def bulk(self):
        entries = {e.key: e for e in self.library_entries()}
        entries.update(self.by_key)
        entries = [e for e in entries.values() if e.status != "read"]
        if not entries:
            self.main.notify("Нет книг для массовой отметки. Загрузите библиотеку или добавьте книгу вручную.")
            self.main.library_tab.ensure_loaded()
            return
        dialog = BulkDialog(sorted(entries, key=lambda e: (e.author, e.title)), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected = dialog.selected_entries()
            try:
                self.store.save_many(selected)
            except Exception as e:
                QMessageBox.warning(self, core.APP_NAME, str(e))
                return
            self.undo_key = ""
            self.btn_undo.setEnabled(False)
            self.changed()
            self.main.notify(f"Отмечено ранее прочитанных книг: {len(selected)}")

    def build_stats(self, lay):
        line = QHBoxLayout()
        line.addWidget(bold("Статистика и итоги", 15))
        self.year_choice = QComboBox()
        today = date.today()
        years = sorted({today.year, today.year - 1} | {int(e.finished[:4]) for e in self.entries if e.finished}, reverse=True)
        self.year_choice.addItems(list(map(str, years)))
        selected = self.store.pref("stats_year", today.year)
        self.year_choice.setCurrentText(str(selected) if selected in years else str(today.year))
        year = int(self.year_choice.currentText())
        self.year_choice.currentTextChanged.connect(self.change_stats_year)
        line.addStretch(1)
        line.addWidget(self.year_choice)
        lay.addLayout(line)
        self.summary(lay, year)
        c = card()
        grid = QGridLayout(c)
        counts = [len(reading.completed(self.entries, year, m)) for m in range(1, 13)]
        maximum = max([1] + counts)
        for i, n in enumerate(counts):
            grid.addWidget(muted(reading.MONTHS[i]), i, 0)
            bar = QProgressBar()
            bar.setMaximum(maximum)
            bar.setValue(n)
            bar.setTextVisible(False)
            bar.setMinimumHeight(7)
            grid.addWidget(bar, i, 1)
            grid.addWidget(plain(str(n)), i, 2)
        grid.setColumnStretch(1, 1)
        lay.addWidget(c)
        year_only = sum(e.precision == "year" for e in reading.completed(self.entries, year))
        unknown = sum(e.precision == "unknown" for e in reading.completed(self.entries))
        lay.addWidget(plain(f"Без известного месяца за {year} год: {year_only}. Учтены в годовом итоге, но не на графике.\n"
                            f"Прочитано без даты за всё время: {unknown}."))
        row = QHBoxLayout()
        for q in range(1, 5):
            row.addWidget(button(f"{q} квартал", slot=lambda _=False, n=q: self.show_report(year, n)))
        row.addWidget(button("Итог года", primary=True, slot=lambda: self.show_report(year)))
        lay.addLayout(row)

    def change_stats_year(self, value):
        self.store.set_pref("stats_year", int(value))
        self.refresh()

    def show_report(self, year, quarter=None, seen_key=None):
        entries = reading.completed(self.entries, year, quarter=quarter)
        dialog = QDialog(self)
        setup_dialog(dialog, "Итоги чтения")
        dialog.resize(650, 550)
        root = QVBoxLayout(dialog)
        root.addWidget(bold(f"{quarter} квартал {year}" if quarter else f"{year} год", 18))
        root.addWidget(bold(f"Прочитано книг: {len(entries)}", 22))
        root.addWidget(plain(f"Авторов: {len(set(e.author for e in entries))}. "
            f"Оценено книг: {sum(bool(e.rating) for e in entries)}."))
        if year == date.today().year and (not quarter or quarter >= (date.today().month - 1) // 3 + 1):
            root.addWidget(muted("Промежуточный итог на " + date.today().strftime("%d.%m.%Y")))
        listing = QListWidget()
        for e in sorted(entries, key=lambda e: e.finished, reverse=True):
            listing.addItem(f"{e.title} — {e.author}   {'★' * e.rating}\n{reading.date_label(e)}")
        root.addWidget(listing)
        root.addWidget(plain("Книги без даты здесь не учитываются. " +
            ("Записи «только год» входят в годовой итог." if quarter is None else "Записи «только год» не распределяются по кварталам.")))
        close = button("Закрыть", slot=dialog.accept)
        root.addWidget(close)
        dialog.exec()
        if seen_key:
            seen = self.store.pref("reports_seen", [])
            self.store.set_pref("reports_seen", list(dict.fromkeys(seen + [seen_key])))
            self.refresh()

    def build_recs(self, lay):
        header = QHBoxLayout()
        header.addWidget(bold("Что почитать дальше", 15))
        self.source_choice = QComboBox()
        self.source_choice.addItem("Редакционные материалы LiveLib", "editorial")
        self.source_choice.addItem("Моя библиотека", "local")
        self.source_choice.setCurrentIndex(self.source_choice.findData(self.store.pref("rec_source", "editorial")))
        self.source_choice.currentIndexChanged.connect(self.change_source)
        header.addWidget(self.source_choice)
        header.addStretch(1)
        header.addWidget(button("Мои предпочтения…", slot=self.preferences))
        lay.addLayout(header)
        if self.source_choice.currentData() == "editorial":
            lay.addWidget(self.network_card())
            candidates = self.catalog.candidates() if self.catalog else []
        else:
            candidates = recommendations.local_candidates(self.main.library_tab.groups)
            if not self.main.library_tab.scanned_once:
                lay.addWidget(button("Загрузить библиотеку", slot=self.main.library_tab.ensure_loaded))
        self.candidates = recommendations.rank(candidates, self.entries, self.store.pref("taste", {}))
        if not self.candidates:
            lay.addWidget(plain("Пока нет рекомендаций. Загрузите каталог или библиотеку. Прочитанные, отложенные книги и книги, которые вы читаете, не предлагаются."))
        for candidate in self.candidates:
            lay.addWidget(self.rec_card(candidate))

    def change_source(self, *_):
        self.store.set_pref("rec_source", self.source_choice.currentData())
        self.refresh()

    def preferences(self):
        dialog = PreferencesDialog(self.store.pref("taste", {}), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.set_pref("taste", dialog.values())
            self.refresh()

    def rec_card(self, c):
        outer = card()
        lay = QVBoxLayout(outer)
        lay.setContentsMargins(18, 14, 18, 14)
        header = QHBoxLayout()
        header.addWidget(self.cover_label(self.candidate_entry(c)), 0, Qt.AlignmentFlag.AlignTop)
        text_column = QVBoxLayout()
        title_row = QHBoxLayout()
        if c.get("url"):
            link = QLabel(f'<a href="{escape(c["url"], quote=True)}">{escape(c["title"])} ↗</a>')
            link.setOpenExternalLinks(True)
            link.setWordWrap(True)
        else:
            link = plain(c["title"])
        title_row.addWidget(link, 1)
        if c.get("planned"):
            title_row.addWidget(muted("В «Хочу прочитать»"))
        text_column.addLayout(title_row)
        text_column.addWidget(plain(c["author"]))
        text_column.addWidget(plain(c["reason"]))
        header.addLayout(text_column, 1)
        lay.addLayout(header)
        for source in c.get("sources", [])[:3]:
            link = QLabel(f'LiveLib Редакция · <a href="{escape(source["url"], quote=True)}">{escape(source["title"])} ↗</a>')
            link.setWordWrap(True)
            link.setOpenExternalLinks(True)
            lay.addWidget(link)
        row = QHBoxLayout()
        row.addWidget(button("Найти в библиотеке", primary=True, slot=lambda: self.find_in_library(c["title"], c["author"])))
        row.addWidget(button("Уже читал…", slot=lambda: self.mark_candidate(c, "read")))
        wish = button("В планах" if c.get("planned") else "Хочу прочитать", slot=lambda: self.mark_candidate(c, "wish"))
        wish.setEnabled(not c.get("planned"))
        row.addWidget(wish)
        row.addStretch(1)
        lay.addLayout(row)
        return outer

    def mark_candidate(self, c, status):
        entry = self.candidate_entry(c)
        entry.refs = list(dict.fromkeys(entry.refs + c.get("refs", [])))
        if c.get("path") and c["path"] not in entry.paths:
            entry.paths.append(c["path"])
        entry.status = status
        if status == "read":
            self.edit_entry(entry, read=True)
        else:
            self.save_entry(entry)

    def candidate_entry(self, c):
        entry = deepcopy(self.by_key.get(c["key"])) if c["key"] in self.by_key else reading.Entry(c["title"], c["author"], genres=c.get("genres", []))
        entry.cover_url = c.get("cover_url") or entry.cover_url
        if c.get("path") and c["path"] not in entry.paths:
            entry.paths.append(c["path"])
        return entry

    def request_cover(self, entry):
        paths = list(dict.fromkeys(self.local_cover_paths.get(entry.key, []) + entry.paths))
        return self.cover_loader.request(paths, entry.cover_url or self.remote_cover_urls.get(entry.key, ""))

    def cover_label(self, entry):
        label = QLabel()
        label.setObjectName("readingCover")
        label.setFixedSize(48, 72)
        label.setToolTip(entry.title)
        label.setPixmap(cover_placeholder(entry.title, entry.author, 48, 72, sum(map(ord, entry.title))))
        key, image = self.request_cover(entry)
        if image is not None:
            label.setPixmap(rounded(QPixmap.fromImage(image), 48, 72))
        elif key:
            self.cover_labels.setdefault(key, []).append(weakref.ref(label))
        return label

    def cover_ready(self, key, image):
        if self.closing or image.isNull():
            return
        pixmap = QPixmap.fromImage(image)
        for ref in self.cover_labels.pop(key, []):
            label = ref()
            if label is not None:
                try:
                    label.setPixmap(rounded(pixmap, 48, 72))
                except RuntimeError:
                    pass
        for ref, entry_key in self.cover_trees.pop(key, []):
            tree = ref()
            if tree is not None:
                try:
                    for i in range(tree.topLevelItemCount()):
                        item = tree.topLevelItem(i)
                        if item.data(0, Qt.ItemDataRole.UserRole) == entry_key:
                            item.setIcon(0, QIcon(rounded(pixmap, 38, 56)))
                except RuntimeError:
                    pass

    def find_in_library(self, title, author=""):
        self.main.tabs.setCurrentWidget(self.main.library_tab)
        self.main.library_tab.search_from_reading(title, author)

    def network_card(self):
        c = card()
        lay = QVBoxLayout(c)
        if self.catalog is None:
            lay.addWidget(plain("Каталог LiveLib недоступен. История чтения и подбор из библиотеки работают. Файл каталога не изменён.\n" + self.catalog_error))
            return c
        docs = self.catalog.documents()
        full = sum(bool(d.get("complete")) for d in docs)
        books = len(self.catalog.candidates())
        self.network_status = plain(f"Обнаружено материалов: {len(docs)} · сверено: {full} · книг в каталоге: {books}")
        lay.addWidget(self.network_status)
        message = self.catalog.state("message", "Загрузите редакционные материалы. История чтения никуда не отправляется.")
        lay.addWidget(plain(message))
        retry = self.catalog.state("retry_after", 0)
        if retry > time.time():
            lay.addWidget(plain("Повторная попытка не раньше " + datetime.fromtimestamp(retry).strftime("%d.%m %H:%M")))
        lay.addWidget(muted("Полнота всего архива LiveLib не гарантируется. Уже загруженные подборки доступны без интернета.", 8.5))
        row = QHBoxLayout()
        active = self.catalog.state("enabled", False)
        self.network_button = button("Пауза" if active else "Продолжить загрузку", slot=self.toggle_network)
        row.addWidget(self.network_button)
        weekly = QCheckBox("Проверять новые материалы раз в неделю")
        weekly.setChecked(self.catalog.state("weekly", False))
        weekly.toggled.connect(lambda on: self.catalog.set_state("weekly", on))
        row.addWidget(weekly)
        row.addWidget(button("Проверить сейчас", slot=self.check_now))
        row.addStretch(1)
        lay.addLayout(row)
        return c

    def toggle_network(self):
        enabled = not self.catalog.state("enabled", False)
        self.catalog.set_state("enabled", enabled)
        if not enabled:
            self.cancel.set()
        else:
            if not self.catalog.state("last_cycle", 0):
                self.catalog.set_state("last_cycle", time.time())
            self.network_tick()
        self.refresh()

    def check_now(self):
        if self.worker and self.worker.is_alive():
            self.main.notify("Загрузка уже идёт. Можно поставить её на паузу.")
            return
        if self.catalog.state("retry_after", 0) > time.time():
            self.main.notify("LiveLib или сеть требуют паузы. Сохранённый каталог доступен.", 10000)
            return
        self.catalog.reset_cycle()
        self.catalog.set_state("enabled", True)
        self.network_tick()
        self.refresh()

    def network_tick(self):
        if self.closing or self.catalog is None or (self.worker and self.worker.is_alive()):
            return
        if self.catalog.state("retry_after", 0) > time.time():
            return
        last_cycle = self.catalog.state("last_cycle", 0)
        cycle_finished = not last_cycle or self.catalog.state("last_complete", 0) >= last_cycle
        if self.catalog.state("weekly", False) and cycle_finished and time.time() - last_cycle >= editorial.WEEK:
            self.catalog.reset_cycle()
            self.catalog.set_state("enabled", True)
        if not self.catalog.state("enabled", False):
            return
        self.cancel = threading.Event()
        def work():
            try:
                self.catalog.run_batch(self.cancel, progress=lambda: ui_call(self.network_progress))
            except Exception as e:
                msg = str(e)
                ui_call(lambda: self.main.notify("Каталог недоступен: " + msg, 15000) if not self.closing else None)
            ui_call(self.network_progress)
        self.worker = threading.Thread(target=work, daemon=True, name="LiveLib-catalog")
        self.worker.start()

    def network_progress(self):
        if not self.closing and self.view == "recs":
            self.refresh()

    def shutdown(self):
        self.closing = True
        self.cancel.set()
        self.network_timer.stop()
        self.cover_loader.shutdown()

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить историю чтения", "reading-backup.sqlite3", "База истории (*.sqlite3)")
        if path:
            try:
                self.store.backup(Path(path))
                self.main.notify("Резервная копия истории сохранена")
            except Exception as e:
                QMessageBox.warning(self, core.APP_NAME, f"Не удалось сохранить копию:\n{e}")
