"""Вкладка полного каталога ZIP-коллекции."""

from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path
from zipfile import ZipFile

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import archive_catalog as catalog, core, library
from .common import bold, button, card, ui_call

ROLE = Qt.ItemDataRole.UserRole
CHECKED = Qt.CheckState.Checked


class ArchiveTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("page")
        self.busy = False
        self.loaded = False
        self.closed = False
        self.rescan_needed = False
        self.library_update_pending = False
        self.offset = 0
        self.search_query = ""
        self.search_pending = False
        self.variants = {}
        self.present = {}
        self.related = set()
        self.missing_archives = set()
        self.checked = set()
        self.details = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 10)
        paths = QHBoxLayout()
        self.inpx_button = button("Выбрать INPX…", slot=self.choose_inpx)
        self.zip_button = button("Папка с ZIP…", slot=self.choose_zip)
        self.refresh_button = button("Обновить каталог", slot=lambda: self.load(refresh=True))
        paths.addWidget(self.inpx_button)
        paths.addWidget(self.zip_button)
        paths.addWidget(self.refresh_button)
        self.sources = QLabel()
        self.sources.setTextFormat(Qt.TextFormat.PlainText)
        self.sources.setWordWrap(True)
        paths.addWidget(self.sources, 1)
        layout.addLayout(paths)
        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        split = QSplitter(Qt.Orientation.Horizontal)
        left = card()
        side = QVBoxLayout(left)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по автору или названию")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.search_changed)
        side.addWidget(self.search)
        self.authors = QListWidget()
        self.authors.setStyleSheet("QListWidget::item { padding:5px 8px; } QListWidget::item:selected { color:#1B1B1B; }")
        self.authors.currentItemChanged.connect(self.author_changed)
        side.addWidget(self.authors)
        pagination = QHBoxLayout()
        self.prev = button("Назад", slot=lambda: self.load_authors(self.offset - 200))
        self.next = button("Далее", slot=lambda: self.load_authors(self.offset + 200))
        pagination.addWidget(self.prev)
        pagination.addWidget(self.next)
        side.addLayout(pagination)
        split.addWidget(left)

        right = card()
        body = QVBoxLayout(right)
        self.heading = bold("Книги автора", 15)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        body.addWidget(self.heading)
        self.filter = QComboBox()
        self.filter.addItems(["Все", "Уже есть", "Нет у меня"])
        self.filter.currentIndexChanged.connect(self.populate)
        body.addWidget(self.filter)
        self.books = QTreeWidget()
        self.books.setColumnCount(3)
        self.books.setHeaderLabels(["Произведение / вариант", "В моей библиотеке", "Сведения"])
        self.books.setColumnWidth(0, 255)
        self.books.setColumnWidth(1, 150)
        self.books.itemChanged.connect(self.toggle_item)
        self.books.currentItemChanged.connect(self.update_actions)
        self.books.itemDoubleClicked.connect(lambda *_: self.inspect())
        body.addWidget(self.books, 1)
        actions = QHBoxLayout()
        self.add = button("Добавить выбранные", primary=True, slot=self.import_selected)
        self.select_all = button("Выбрать недостающие", slot=self.select_missing)
        self.inspect_button = button("Сведения о вариантах…", slot=self.inspect)
        self.open_button = button("Открыть FB2", slot=self.open_current)
        for item in (self.add, self.select_all, self.inspect_button, self.open_button):
            actions.addWidget(item)
        actions.addStretch(1)
        body.addLayout(actions)
        split.addWidget(right)
        split.setSizes([310, 910])
        layout.addWidget(split, 1)
        self.debounce = QTimer(self, interval=240, singleShot=True)
        self.debounce.timeout.connect(lambda: self.load_authors(0))
        self.main.library_tab.books_loaded.connect(self.library_refreshed)
        self.show_sources()
        self.update_actions()

    @property
    def index(self):
        return catalog.Catalog(core.data_dir() / "archive-catalog.sqlite3")

    def show_sources(self):
        settings = self.main.s
        paths = [p for p in (settings.archive_index, settings.archive_root) if p]
        self.sources.setText("  ·  ".join(paths))
        if not settings.archive_index:
            self.message.setText("Выберите файл .inpx и папку с архивами ZIP.")
        elif not settings.archive_root:
            self.message.setText("Выберите папку с архивами ZIP.")

    def choose_inpx(self):
        settings = self.main.s
        source, _ = QFileDialog.getOpenFileName(self, "Индекс коллекции MyHomeLib", settings.archive_index, "INPX (*.inpx)")
        if source:
            settings.archive_index = source
            if not settings.archive_root:
                settings.archive_root = str(Path(source).parent)
            self.load(refresh=True)

    def choose_zip(self):
        settings = self.main.s
        folder = QFileDialog.getExistingDirectory(self, "Папка с архивами ZIP", settings.archive_root)
        if folder:
            settings.archive_root = folder
            self.show_sources()
            settings.save()
            self.author_changed(self.authors.currentItem())

    def ensure_loaded(self):
        if not self.loaded and self.main.s.archive_index and not self.busy:
            self.load()

    def run(self, message, work, done):
        if self.busy:
            return
        self.busy = True
        self.message.setText(message)
        self.update_actions()

        def finish(result, error):
            if self.closed:
                return
            self.busy = False
            self.message.setText(error)
            self.update_actions()
            if not error:
                done(result)
            if self.busy:
                return
            if self.search_pending:
                self.search_pending = False
                self.debounce.start()
            elif self.library_update_pending:
                self.library_refreshed()

        def worker():
            try:
                result = work()
            except Exception as err:
                message = str(err) or type(err).__name__
                ui_call(lambda: finish(None, message))
            else:
                ui_call(lambda: finish(result, ""))

        threading.Thread(target=worker, daemon=True).start()

    def load(self, refresh=False):
        self.show_sources()
        if not self.main.s.archive_index:
            return
        source = Path(self.main.s.archive_index)
        index = self.index
        self.loaded = False
        self.authors.clear()
        self.clear_books()

        def done(_):
            self.loaded = True
            self.main.s.save()
            self.load_authors(0)

        self.run("Читаю индекс коллекции…", lambda: index.build(source, refresh=refresh), done)

    def search_changed(self):
        if self.loaded:
            self.debounce.start()

    def load_authors(self, offset):
        if not self.loaded:
            return
        if self.busy:
            self.search_pending = True
            return
        index, query = self.index, self.search.text()
        offset = max(0, offset)

        def done(rows):
            self.offset = offset
            self.library_update_pending = False
            self.search_query = query
            self.more_authors = len(rows) > 200
            self.authors.blockSignals(True)
            self.authors.clear()
            for key, name in rows[:200]:
                item = QListWidgetItem(name)
                item.setData(ROLE, key)
                self.authors.addItem(item)
            self.authors.blockSignals(False)
            if rows:
                self.authors.setCurrentRow(0)
            else:
                self.clear_books()
                self.message.setText("Ничего не найдено.")
            self.update_actions()

        self.run("Ищу в каталоге…", lambda: index.authors(query, offset, 201), done)

    def clear_books(self):
        self.variants = {}
        self.present = {}
        self.related = set()
        self.missing_archives = set()
        self.details = {}
        self.checked.clear()
        self.books.clear()
        self.heading.setText("Книги автора")
        self.update_actions()

    def author_changed(self, item, *_, refreshed=False):
        if not item or not self.loaded or self.busy:
            return
        if not refreshed:
            self.clear_books()
        self.author_name, self.author_key = item.text(), item.data(ROLE)
        author, index = self.author_key, self.index
        settings, personal = self.main.s, self.main.library_tab
        self.personal_ready = personal.scanned_root == settings.library_root and bool(settings.library_root)
        local = tuple(personal.groups) if self.personal_ready else ()
        root = Path(settings.library_root)
        zip_root = Path(settings.archive_root) if settings.archive_root else None

        def work():
            books = index.books(author)
            archives = catalog.archive_paths(zip_root) if zip_root and zip_root.is_dir() else {}
            candidates, folders, imported = catalog.local_candidates(local, author, root)
            related = {b.key for b in books if library.norm_title(b.title) in candidates}
            unavailable = {b.key for b in books if Path(b.archive).name.casefold() not in archives}
            return books, archives, folders, related, unavailable, catalog.match_variants(books, candidates, imported, archives)

        def done(result):
            books, self.archives, self.folders, self.related, self.missing_archives, self.present = result
            self.variants = {book.key: book for book in books}
            self.populate()
            if not self.archives:
                self.message.setText("Выберите папку с архивами ZIP.")

        self.run("Сверяю книги автора с личной библиотекой…", work, done)

    def library_refreshed(self):
        self.library_update_pending = self.isVisible() and self.busy
        if self.isVisible() and not self.busy and self.loaded:
            self.author_changed(self.authors.currentItem(), refreshed=True)

    def display_details(self, book):
        if book.key in self.details:
            return self.details[book.key]
        fields = [f"Язык: {book.language}" if book.language else "",
                  f"Серия: {book.series}" if book.series else "",
                  f"ID: {book.identifier}"]
        return " · ".join(filter(None, fields))

    def populate(self, *_):
        if not self.variants:
            self.update_actions()
            return
        selected = self.current_key()
        expanded = {self.books.topLevelItem(i).text(0) for i in range(self.books.topLevelItemCount())
                    if self.books.topLevelItem(i).isExpanded()}
        groups = defaultdict(list)
        for book in self.variants.values():
            groups[library.norm_title(book.title)].append(book)
        self.books.blockSignals(True)
        self.books.clear()
        total_present = sum(any(b.key in self.present for b in books) for books in groups.values())
        self.heading.setText(f"{self.author_name} · в моей библиотеке {total_present} из {len(groups)}")
        mode = self.filter.currentIndex()
        query = self.search_query.strip().casefold().replace("ё", "е")
        title_query = library.norm_title(query) if (
            query and query not in self.author_name.casefold().replace("ё", "е")
            and library.norm_words(query) not in self.author_key
        ) else ""
        for key, books in groups.items():
            if title_query and title_query not in key:
                continue
            visible = [b for b in books if mode == 0 or (b.key in self.present) == (mode == 1)]
            if not visible:
                continue
            parent = None
            if len(books) > 1:
                parent = QTreeWidgetItem([books[0].title, f"Вариантов: {len(books)}"])
                parent.setData(0, ROLE + 1, [list(book.key) for book in visible])
                self.books.addTopLevelItem(parent)
                parent.setExpanded(parent.text(0) in expanded)
            for book in visible:
                key = book.key
                status = ("Уже есть" if key in self.present else "Архив не найден" if key in self.missing_archives
                          else "Есть другая версия" if key in self.related else
                          "Нет у меня" if self.personal_ready else "Библиотека не сверена")
                item = QTreeWidgetItem([f"Вариант {book.identifier}" if parent else book.title,
                                        status, self.display_details(book)])
                item.setToolTip(2, self.display_details(book))
                item.setData(0, ROLE, list(key))
                if key not in self.present and key not in self.missing_archives:
                    item.setCheckState(0, CHECKED if key in self.checked else Qt.CheckState.Unchecked)
                if parent:
                    parent.addChild(item)
                else:
                    self.books.addTopLevelItem(item)
                if key == selected:
                    self.books.setCurrentItem(item)
                    if parent:
                        parent.setExpanded(True)
        self.books.blockSignals(False)
        if not self.books.currentItem() and self.books.topLevelItemCount():
            self.books.setCurrentItem(self.books.topLevelItem(0))
        self.update_actions()

    def current_key(self):
        item = self.books.currentItem()
        return tuple(item.data(0, ROLE) or ()) if item else ()

    def toggle_item(self, item, _column):
        key = tuple(item.data(0, ROLE) or ())
        if key in self.variants and key not in self.present and key not in self.missing_archives:
            if item.checkState(0) == CHECKED:
                self.checked.add(key)
            else:
                self.checked.discard(key)
        self.update_actions()

    def select_missing(self):
        groups = defaultdict(list)
        for book in self.variants.values():
            groups[library.norm_title(book.title)].append(book)
        ambiguous = 0
        for books in groups.values():
            if any(b.key in self.present or b.key in self.related for b in books):
                continue
            if len(books) > 1:
                ambiguous += 1
            elif books[0].key not in self.missing_archives:
                self.checked.add(books[0].key)
        self.populate()
        if ambiguous:
            self.message.setText(f"У {ambiguous} произведений несколько вариантов. Раскройте их и отметьте нужные.")

    def inspect(self):
        item = self.books.currentItem()
        if not item:
            return
        keys = [self.current_key()] if self.current_key() else [tuple(k) for k in item.data(0, ROLE + 1) or []]
        variants = [self.variants[key] for key in keys if key not in self.missing_archives]
        if not variants:
            return
        archives = self.archives

        def work():
            result = {}
            for variant in variants:
                with ZipFile(catalog.archive_path(archives, variant)) as archive:
                    fields = catalog.metadata(archive, catalog.member_info(archive, variant))
                if "Язык" not in fields and variant.language:
                    fields["Язык"] = variant.language
                result[variant.key] = " · ".join(f"{key}: {value}" for key, value in fields.items())
                result[variant.key] = result[variant.key] or "Отличия не указаны"
            return result

        def done(result):
            self.details.update(result)
            self.populate()
            for row in range(self.books.topLevelItemCount()):
                top = self.books.topLevelItem(row)
                if top.data(0, ROLE + 1) == [list(key) for key in keys]:
                    top.setExpanded(True)
            if len(result) == 1:
                self.message.setText(next(iter(result.values())))

        self.run("Читаю сведения об изданиях…", work, done)

    def open_current(self):
        path = self.present.get(self.current_key())
        if path and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve()))):
            self.message.setText("У Windows нет приложения для открытия этого файла FB2.")

    def import_selected(self):
        if not self.checked:
            return
        settings, personal = self.main.s, self.main.library_tab
        if not settings.library_root or not Path(settings.library_root).is_dir():
            personal.choose_library()
            if not settings.library_root or not Path(settings.library_root).is_dir():
                return
        root = Path(settings.library_root)
        if personal.scanning or personal.busy or getattr(personal, "scanned_root", None) != settings.library_root:
            if not personal.scanning and not personal.busy:
                personal.rescan()
            self.message.setText("Дождитесь окончания обновления личной библиотеки и повторите добавление.")
            return
        variants = [book for key, book in self.variants.items() if key in self.checked and key not in self.present]
        folders = sorted(self.folders, key=lambda p: str(p).casefold())
        destination = folders[0] if len(folders) == 1 else root / core.safe_name(self.author_name)
        archives = self.archives

        def done(result):
            paths, errors = result
            self.present.update(paths)
            self.checked.difference_update(paths)
            self.populate()
            message = f"Добавлено FB2: {len(paths)}"
            if errors:
                message += "\n" + "\n".join(errors[:4])
            self.message.setText(message)
            if paths:
                self.rescan_needed = True
                self.refresh_personal()

        self.run("Добавляю выбранные книги…", lambda: catalog.import_variants(variants, root, destination, archives), done)

    def refresh_personal(self):
        if self.closed or not self.rescan_needed:
            return
        personal = self.main.library_tab
        if personal.scanning or personal.busy:
            QTimer.singleShot(300, self.refresh_personal)
        else:
            self.rescan_needed = False
            personal.rescan()

    def update_actions(self, *_):
        ready = self.loaded and not self.busy
        for widget in (self.inpx_button, self.zip_button, self.refresh_button, self.authors,
                       self.books, self.filter):
            widget.setEnabled(not self.busy)
        self.prev.setEnabled(ready and self.offset > 0)
        self.next.setEnabled(ready and getattr(self, "more_authors", False))
        self.add.setEnabled(ready and bool(self.checked))
        self.add.setText(f"Добавить выбранные ({len(self.checked)})" if self.checked else "Добавить выбранные")
        self.select_all.setEnabled(ready and bool(self.variants))
        self.inspect_button.setEnabled(ready and self.books.currentItem() is not None)
        self.open_button.setEnabled(ready and self.current_key() in self.present)
