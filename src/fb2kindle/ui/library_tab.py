"""Вкладка «Библиотека»: авторы и книги из папки FB2, статус конвертации, сведения из Википедии."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter,
                               QStackedWidget, QToolButton, QMenu, QVBoxLayout, QWidget)

from .. import core, library, wiki
from .common import (avatar, bold, button, card, cover_placeholder, flags, hline, muted, rounded, set_primary,
                     show_in_explorer, style_chip, ui_call)

MISSING = ("none", "changed", "error")
FILTERS = [("all", "Все"), ("none", "Не сконвертированы"), ("done", "Готовы"), ("changed", "FB2 изменился")]
SMALL, BIG = QSize(36, 52), QSize(150, 222)


def matches_filter(status: str, f: str) -> bool:
    if f == "all":
        return True
    if f == "none":
        return status in ("none", "queued", "run", "error")
    return status == f


class ElidedLabel(QLabel):
    """Подпись, которая обрезается многоточием, если не помещается."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(20)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        p.setFont(self.font())
        t = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        p.drawText(self.rect(), flags(Qt.AlignmentFlag.AlignLeft, Qt.AlignmentFlag.AlignVCenter), t)


class AuthorRow(QWidget):
    def __init__(self, tab: "LibraryTab", g: library.AuthorGroup):
        super().__init__()
        self.key = g.key
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 5, 8, 5)
        lay.setSpacing(8)
        self.check = QCheckBox()
        self.check.setToolTip("Выбрать автора для конвертации")
        self.check.setChecked(g.key in tab.checked)
        self.check.toggled.connect(lambda on: tab.set_checked(g.key, on))
        lay.addWidget(self.check)
        av = QLabel()
        av.setPixmap(avatar(g.initials, 32))
        av.setFixedSize(32, 32)
        lay.addWidget(av)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.name = ElidedLabel(g.sort_name)
        self.sub = QLabel("")
        f = self.sub.font()
        f.setPointSizeF(8.5)
        self.sub.setFont(f)
        col.addWidget(self.name)
        col.addWidget(self.sub)
        lay.addLayout(col, 1)
        self.count = muted("")
        lay.addWidget(self.count)

    def update_counts(self, done: int, total: int, missing: int):
        self.count.setText(f"{done} / {total}")
        if missing:
            self.sub.setText(f"не готово: {missing}")
            self.sub.setStyleSheet("color:#7A4D00;")
        else:
            self.sub.setText("всё сконвертировано")
            self.sub.setStyleSheet("color:#0E5C0E;")


class BookRow(QWidget):
    def __init__(self, tab: "LibraryTab", book: core.Book, group: library.AuthorGroup):
        super().__init__()
        self.tab, self.book, self.group = tab, book, group
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(12)
        self.cover = QLabel()
        self.cover.setFixedSize(SMALL)
        lay.addWidget(self.cover)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title = ElidedLabel(book.display_title)
        f = self.title.font()
        f.setWeight(f.Weight.DemiBold)
        self.title.setFont(f)
        sub = " · ".join(x for x in (book.series_text, book.year, book.genre_text) if x)
        self.sub = ElidedLabel(sub or Path(book.path).name)
        self.sub.setProperty("muted", True)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        self.reading_chip = QLabel()
        self.reading_chip.setTextFormat(Qt.TextFormat.PlainText)
        self.reading_chip.setStyleSheet("color:#005FB8; font-size:11px;")
        col.addWidget(self.reading_chip)
        lay.addLayout(col, 1)
        self.chip = QLabel()
        self.chip.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        lay.addWidget(self.chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self.action = QPushButton()
        self.action.setFixedWidth(150)
        self.action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action.clicked.connect(lambda: tab.book_action(self.book, self.group))
        lay.addWidget(self.action)
        self.refresh()

    def refresh(self):
        st = self.tab.status_of(self.book)
        style_chip(self.chip, st)
        if st == "error":
            self.chip.setToolTip(self.tab.errors.get(str(self.book.path), ""))
        label, primary, enabled = action_for(st)
        self.action.setText(label)
        self.action.setEnabled(enabled)
        set_primary(self.action, primary)
        self.refresh_reading()

    def refresh_reading(self):
        from ..reading import STATUSES
        tracker = getattr(self.tab.main, "reading_tab", None)
        entry = tracker.entry_for_book(self.book, self.group.name) if tracker else None
        self.reading_chip.setText((STATUSES[entry.status] + (" · " + "★" * entry.rating if entry.rating else "")) if entry else "")
        self.reading_chip.setVisible(entry is not None)


def action_for(status: str) -> tuple[str, bool, bool]:
    return {
        "none": ("Конвертировать", True, True),
        "changed": ("Обновить", False, True),
        "error": ("Повторить", False, True),
        "done": ("Показать в папке", False, True),
        "queued": ("В очереди", False, False),
        "run": ("Конвертируется…", False, False),
    }.get(status, ("Конвертировать", True, True))


class LibraryTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.setObjectName("page")
        self.main = main
        self.s: core.Settings = main.s
        self.groups: list[library.AuthorGroup] = []
        self.by_key: dict[str, library.AuthorGroup] = {}
        self.group_of: dict[str, library.AuthorGroup] = {}     # путь книги → автор
        self.status: dict[str, str] = {}
        self.outputs: dict[str, Path] = {}
        self.errors: dict[str, str] = {}
        self.prev: dict[str, str] = {}
        self.checked: set[str] = set()
        self.current_key: str | None = None
        self.current_book: core.Book | None = None
        self.filter = "all"
        self.author_rows: dict[str, AuthorRow] = {}
        self.book_rows: dict[str, BookRow] = {}
        self.small: dict[str, QPixmap] = {}
        self.big: dict[str, QPixmap] = {}
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.wiki_cache = wiki.WikiCache()
        self.scanning = False
        self.scanned_once = False
        # очередь конвертации
        self.pending: list[core.Job] = []
        self.queue_lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.current_conv: core.Converter | None = None
        self.q_total = 0
        self.q_done = 0
        self.q_ok = 0
        self.q_err = 0
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(200)
        self._search_timer.timeout.connect(self.rebuild_authors)
        self._reading_search = None
        self._reading_author = ""
        self._build()

    # ------------------------------------------------------------------ UI --

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 10)
        root.setSpacing(10)

        folders = card()
        fl = QHBoxLayout(folders)
        fl.setContentsMargins(14, 10, 14, 10)
        fl.setSpacing(8)
        fl.addWidget(muted("Книги FB2"))
        self.ed_lib = QLineEdit(self.s.library_root)
        self.ed_lib.setReadOnly(True)
        self.ed_lib.setPlaceholderText("Папка не выбрана")
        fl.addWidget(self.ed_lib, 1)
        fl.addWidget(button("Изменить…", slot=self.choose_library))
        fl.addSpacing(10)
        fl.addWidget(muted("Книги для Kindle"))
        self.ed_kindle = QLineEdit(self.s.kindle_root)
        self.ed_kindle.setReadOnly(True)
        fl.addWidget(self.ed_kindle, 1)
        fl.addWidget(button("Изменить…", slot=self.choose_kindle))
        fl.addSpacing(10)
        self.btn_rescan = QToolButton()
        self.btn_rescan.setText("Обновить")
        self.btn_rescan.setToolTip("Перечитать папки: новые, удалённые и изменённые книги")
        self.btn_rescan.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.btn_rescan.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_rescan.clicked.connect(self.rescan)
        menu = QMenu(self.btn_rescan)
        menu.addAction("Сбросить данные библиотеки…").triggered.connect(lambda: self.reset_data())
        self.btn_rescan.setMenu(menu)
        fl.addWidget(self.btn_rescan)
        self.ch_wiki = QCheckBox("Википедия")
        self.ch_wiki.setToolTip("Загружать справки об авторах и описания книг без аннотации из Википедии")
        self.ch_wiki.setChecked(self.s.wiki_enabled)
        self.ch_wiki.toggled.connect(self._toggle_wiki)
        fl.addWidget(self.ch_wiki)
        root.addWidget(folders)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        # Заглушка, пока папка не выбрана
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.addStretch(1)
        box = card()
        box.setMaximumWidth(520)
        bl = QVBoxLayout(box)
        bl.setContentsMargins(28, 24, 28, 24)
        t = bold("Выберите папку с книгами FB2", 14)
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(t)
        d = muted("Программа найдёт все книги в ней и в подпапках, разложит их по авторам и покажет, "
                  "какие уже сконвертированы для Kindle.")
        d.setWordWrap(True)
        d.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(d)
        bl.addSpacing(8)
        b = button("Выбрать папку…", primary=True, slot=self.choose_library)
        bl.addWidget(b, alignment=Qt.AlignmentFlag.AlignCenter)
        h = QHBoxLayout()
        h.addStretch(1)
        h.addWidget(box)
        h.addStretch(1)
        el.addLayout(h)
        el.addStretch(2)
        self.stack.addWidget(empty)

        # Загрузка
        loading = QWidget()
        ll = QVBoxLayout(loading)
        ll.addStretch(1)
        self.lbl_loading = bold("Читаю библиотеку…", 12)
        self.lbl_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(self.lbl_loading)
        self.lbl_loading_sub = muted("Первый раз это может занять минуту. Потом библиотека открывается сразу.")
        self.lbl_loading_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ll.addWidget(self.lbl_loading_sub)
        ll.addStretch(2)
        self.stack.addWidget(loading)

        # Сама библиотека
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._build_authors())
        split.addWidget(self._build_books())
        split.addWidget(self._build_details())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([290, 620, 330])
        self.stack.addWidget(split)

        foot = QHBoxLayout()
        self.lbl_summary = muted("")
        foot.addWidget(self.lbl_summary)
        foot.addStretch(1)
        self.lbl_queue = QLabel("")
        foot.addWidget(self.lbl_queue)
        self.btn_stop = button("Остановить", slot=self.stop_queue)
        self.btn_stop.setVisible(False)
        foot.addWidget(self.btn_stop)
        root.addLayout(foot)

        self.stack.setCurrentIndex(0 if not self.s.library_root else 1)

    def _build_authors(self) -> QWidget:
        c = card()
        c.setMinimumWidth(240)
        lay = QVBoxLayout(c)
        lay.setContentsMargins(10, 12, 10, 8)
        lay.setSpacing(8)
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Поиск: автор или книга")
        self.ed_search.setClearButtonEnabled(True)
        self.ed_search.textChanged.connect(lambda *_: self._search_timer.start())
        lay.addWidget(self.ed_search)
        self.btn_reading_author = button("Все книги автора", slot=self.search_reading_author)
        self.btn_reading_author.setVisible(False)
        lay.addWidget(self.btn_reading_author)
        src = QHBoxLayout()
        src.addWidget(muted("Имя автора:"))
        from .settings_dialog import Choice
        self.cb_author_src = Choice([(True, "из названия папки"), (False, "из книги (FB2)")], self.s.author_from_folder,
                                    "Из названия папки: «Мьевилль Чайна» → «Чайна Мьевилль». "
                                    "Фамилию из нескольких слов отделите запятой: «Ле Гуин, Урсула».")
        self.cb_author_src.currentIndexChanged.connect(self._author_source_changed)
        src.addWidget(self.cb_author_src, 1)
        lay.addLayout(src)
        hdr = QHBoxLayout()
        self.ch_all = QCheckBox("Авторы: 0")
        self.ch_all.setToolTip("Выбрать всех авторов в списке (с учётом поиска и фильтра)")
        self.ch_all.clicked.connect(self.toggle_all)
        hdr.addWidget(self.ch_all)
        hdr.addStretch(1)
        hdr.addWidget(muted("готово / всего", 8.5))
        lay.addLayout(hdr)
        self.selbar = QFrame()
        self.selbar.setObjectName("selbar")
        sl = QVBoxLayout(self.selbar)
        sl.setContentsMargins(10, 8, 10, 8)
        sl.setSpacing(6)
        self.lbl_sel = QLabel("")
        self.lbl_sel.setWordWrap(True)
        sl.addWidget(self.lbl_sel)
        self.btn_conv_sel = button("Сконвертировать выбранных", primary=True, slot=self.convert_checked)
        sl.addWidget(self.btn_conv_sel)
        sl.addWidget(button("Снять выбор", slot=self.clear_checked))
        self.selbar.setVisible(False)
        lay.addWidget(self.selbar)
        self.lst_authors = QListWidget()
        self.lst_authors.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.lst_authors.currentItemChanged.connect(self._on_author_item)
        lay.addWidget(self.lst_authors, 1)
        self.lbl_no_authors = muted("Ничего не найдено.")
        self.lbl_no_authors.setVisible(False)
        lay.addWidget(self.lbl_no_authors)
        return c

    def _build_books(self) -> QWidget:
        c = card()
        c.setMinimumWidth(420)
        lay = QVBoxLayout(c)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(18, 16, 18, 14)
        head.setSpacing(16)
        self.av_author = QLabel()
        self.av_author.setFixedSize(56, 56)
        head.addWidget(self.av_author, alignment=Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        self.lbl_author = bold("", 15)
        col.addWidget(self.lbl_author)
        self.lbl_author_desc = muted("")
        self.lbl_author_desc.setWordWrap(True)
        col.addWidget(self.lbl_author_desc)
        self.lbl_bio = QLabel("")
        self.lbl_bio.setWordWrap(True)
        self.lbl_bio.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(self.lbl_bio)
        self.lbl_source = muted("", 8.5)
        self.lbl_source.setOpenExternalLinks(True)
        col.addWidget(self.lbl_source)
        head.addLayout(col, 1)
        self.btn_missing = button("Сконвертировать недостающие", primary=True, slot=self.convert_author_missing)
        head.addWidget(self.btn_missing, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        lay.addWidget(hline())
        bar = QHBoxLayout()
        bar.setContentsMargins(18, 10, 18, 8)
        bar.setSpacing(6)
        self.filter_group = QButtonGroup(self)
        self.filter_buttons = {}
        for key, text in FILTERS:
            b = button(text, chip=True)
            b.setChecked(key == self.filter)
            b.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            self.filter_group.addButton(b)
            self.filter_buttons[key] = b
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.lst_books = QListWidget()
        self.lst_books.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.lst_books.setContentsMargins(8, 0, 8, 0)
        self.lst_books.setStyleSheet("QListWidget { padding: 0 8px; }")
        self.lst_books.currentItemChanged.connect(self._on_book_item)
        lay.addWidget(self.lst_books, 1)
        self.lbl_no_books = muted("У этого автора нет книг с таким статусом.")
        self.lbl_no_books.setContentsMargins(20, 10, 20, 10)
        self.lbl_no_books.setVisible(False)
        lay.addWidget(self.lbl_no_books)
        return c

    def _build_details(self) -> QWidget:
        c = card()
        c.setMinimumWidth(280)
        outer = QVBoxLayout(c)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setMinimumWidth(240)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(6)
        self.det_cover = QLabel()
        self.det_cover.setFixedSize(BIG)
        lay.addWidget(self.det_cover, alignment=Qt.AlignmentFlag.AlignHCenter)
        lay.addSpacing(10)
        self.det_title = bold("", 13)
        self.det_title.setWordWrap(True)
        lay.addWidget(self.det_title)
        self.det_author = muted("")
        lay.addWidget(self.det_author)
        lay.addSpacing(6)
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(5)
        self.det_fields = {}
        for key, label in (("series", "Серия"), ("year", "Год"), ("genre", "Жанр"), ("fb2", "FB2"), ("kindle", "Kindle")):
            if key in ("fb2", "kindle"):
                v = ElidedLabel("")      # длинные пути без пробелов не переносятся — обрезаем
            else:
                v = QLabel("")
                v.setWordWrap(True)
                v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(muted(label), v)
            self.det_fields[key] = v
        lay.addLayout(form)
        lay.addSpacing(8)
        self.det_ann_label = bold("", 9)
        self.det_ann_label.setStyleSheet("color:#5F5F5F;")
        lay.addWidget(self.det_ann_label)
        self.det_ann = QLabel("")
        self.det_ann.setWordWrap(True)
        self.det_ann.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.det_ann.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.det_ann)
        lay.addSpacing(10)
        btns = QVBoxLayout()
        btns.setSpacing(6)
        self.det_action = button("Конвертировать", primary=True,
                                 slot=lambda: self.current_book and self.book_action(self.current_book, self.group_of[str(self.current_book.path)]))
        btns.addWidget(self.det_action)
        btns.addWidget(button("Показать FB2 в папке", slot=lambda: self.current_book and show_in_explorer(self.current_book.path)))
        self.reading_detail = QLabel("")
        self.reading_detail.setTextFormat(Qt.TextFormat.PlainText)
        self.reading_detail.setWordWrap(True)
        btns.addWidget(self.reading_detail)
        self.btn_reading = button("Статус чтения и оценка…", slot=self.edit_reading)
        self.btn_reading.setEnabled(False)
        btns.addWidget(self.btn_reading)
        lay.addLayout(btns)
        lay.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return c

    # -------------------------------------------------------- Сканирование --

    def ensure_loaded(self):
        if self.s.library_root and not self.scanned_once and not self.scanning:
            self.rescan()

    def choose_library(self):
        path = QFileDialog.getExistingDirectory(self, "Папка с книгами FB2", self.s.library_root)
        if path:
            self.s.library_root = str(Path(path))
            self.ed_lib.setText(self.s.library_root)
            self.s.save()
            self.rescan()

    def choose_kindle(self):
        path = QFileDialog.getExistingDirectory(self, "Папка с книгами для Kindle", self.s.kindle_root)
        if path:
            self.s.kindle_root = str(Path(path))
            self.ed_kindle.setText(self.s.kindle_root)
            self.s.save()
            self.rescan()

    def rescan(self):
        if self.scanning:
            return
        root = Path(self.s.library_root) if self.s.library_root else None
        if not root or not root.is_dir():
            self.stack.setCurrentIndex(0)
            if root:
                QMessageBox.warning(self, core.APP_NAME, f"Папка не найдена:\n{root}")
            return
        if self.busy:
            QMessageBox.information(self, core.APP_NAME, "Дождитесь окончания конвертации.")
            return
        self.scanning = True
        self.btn_rescan.setEnabled(False)
        if not self.groups:
            self.stack.setCurrentIndex(1)
        self.lbl_summary.setText("Читаю библиотеку…")
        kroot = Path(self.s.kindle_root) if self.s.kindle_root else None

        def progress(done, total):
            ui_call(lambda: self._scan_progress(done, total))

        def work():
            try:
                books = library.scan_library(root, library.MetaCache(), progress)
                groups = library.group_by_author(books, root, self.s.author_from_folder)
                kindle = library.KindleIndex(kroot)
                status, outputs = {}, {}
                for g in groups:
                    for b in g.books:
                        st, out = library.book_status(b, g.key, kindle, self.main.conv_index)
                        status[str(b.path)] = st
                        if out:
                            outputs[str(b.path)] = out
                ui_call(lambda: self._on_scanned(groups, status, outputs))
            except Exception as e:  # noqa: BLE001
                err = str(e)
                ui_call(lambda: self._on_scan_error(err))

        threading.Thread(target=work, daemon=True).start()

    def _scan_progress(self, done, total):
        text = f"Читаю библиотеку… {done} из {total}"
        self.lbl_loading.setText(text)
        self.lbl_summary.setText(text)

    def _on_scan_error(self, err: str):
        self.scanning = False
        self.btn_rescan.setEnabled(True)
        self.lbl_summary.setText("")
        QMessageBox.warning(self, core.APP_NAME, f"Не удалось прочитать библиотеку:\n{err}")

    def _on_scanned(self, groups, status, outputs):
        self.scanning = False
        self.scanned_once = True
        self.btn_rescan.setEnabled(True)
        self.groups = groups
        self.by_key = {g.key: g for g in groups}
        self.group_of = {str(b.path): g for g in groups for b in g.books}
        self.status, self.outputs, self.errors = status, outputs, {}
        self.checked &= set(self.by_key)
        self.small.clear()
        self.big.clear()
        self.stack.setCurrentIndex(2)
        if self.current_key not in self.by_key:
            self.current_key = groups[0].key if groups else None
            self.current_book = None
        self.rebuild_authors()
        self.update_summary()
        if not groups:
            self.lbl_summary.setText("В папке нет книг FB2.")
        tracker = getattr(self.main, "reading_tab", None)
        if tracker:
            tracker.refresh()
        if self._reading_search:
            self.apply_reading_search()

    # ------------------------------------------------------------- Статусы --

    def status_of(self, book: core.Book) -> str:
        return self.status.get(str(book.path), "none")

    def counts(self, g: library.AuthorGroup) -> tuple[int, int, int]:
        st = [self.status_of(b) for b in g.books]
        return sum(s == "done" for s in st), len(st), sum(s in MISSING for s in st)

    def set_status(self, path: str, st: str):
        self.status[path] = st
        row = self.book_rows.get(path)
        if row:
            row.refresh()
        g = self.group_of.get(path)
        if g and g.key in self.author_rows:
            self.author_rows[g.key].update_counts(*self.counts(g))
        if self.current_book and str(self.current_book.path) == path:
            self._refresh_detail_status()
        if g and g.key == self.current_key:
            self._refresh_missing_button()
        self.update_summary()
        self._refresh_selbar()

    def update_summary(self):
        all_st = list(self.status.values())
        done = sum(s == "done" for s in all_st)
        none = sum(s in ("none", "queued", "run", "error") for s in all_st)
        changed = sum(s == "changed" for s in all_st)
        if self.groups:
            self.lbl_summary.setText(f"Авторов: {len(self.groups)} · книг: {len(all_st)} · готово {done} · "
                                     f"не сконвертировано {none} · FB2 изменился {changed}")
        counts = {"all": len(all_st), "none": none, "done": done, "changed": changed}
        for key, text in FILTERS:
            self.filter_buttons[key].setText(f"{text} · {counts[key]}")

    def on_external_conversion(self, src: Path, out: Path):
        """Книгу сконвертировали на вкладке «Конвертация» — обновить статус, если она есть в библиотеке."""
        key = str(src)
        if key in self.group_of:
            self.outputs[key] = out
            self.set_status(key, "done")

    # -------------------------------------------------------------- Авторы --

    def visible_groups(self) -> list[library.AuthorGroup]:
        q = self.ed_search.text().strip().lower().replace("ё", "е")
        res = []
        for g in self.groups:
            if q and q not in g.sort_name.lower().replace("ё", "е") and q not in g.name.lower().replace("ё", "е"):
                if not any(q in b.display_title.lower().replace("ё", "е") for b in g.books):
                    continue
            if self.filter != "all" and not any(matches_filter(self.status_of(b), self.filter) for b in g.books):
                continue
            res.append(g)
        return res

    def rebuild_authors(self):
        groups = self.visible_groups()
        self.lst_authors.blockSignals(True)
        self.lst_authors.clear()
        self.author_rows.clear()
        current_item = None
        for g in groups:
            row = AuthorRow(self, g)
            row.update_counts(*self.counts(g))
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, g.key)
            it.setSizeHint(QSize(100, 50))
            self.lst_authors.addItem(it)
            self.lst_authors.setItemWidget(it, row)
            self.author_rows[g.key] = row
            if g.key == self.current_key:
                current_item = it
        if current_item is None and groups:
            current_item = self.lst_authors.item(0)
        if current_item is not None:
            self.lst_authors.setCurrentItem(current_item)
        self.lst_authors.blockSignals(False)
        self.lbl_no_authors.setVisible(not groups)
        self.ch_all.setText(f"Авторы: {len(groups)}")
        self._refresh_check_all()
        key = current_item.data(Qt.ItemDataRole.UserRole) if current_item is not None else None
        if key != self.current_key or not self.book_rows:
            self.show_author(key)
        else:
            self.rebuild_books()

    def _on_author_item(self, cur, _prev):
        if cur is not None:
            self.show_author(cur.data(Qt.ItemDataRole.UserRole))

    def show_author(self, key: str | None):
        if key != self.current_key:
            self.current_book = None
        self.current_key = key
        g = self.by_key.get(key) if key else None
        if not g:
            self.lbl_author.setText("")
            self.lbl_author_desc.setText("")
            self.lbl_bio.setText("")
            self.lbl_source.setText("")
            self.av_author.clear()
            self.rebuild_books()
            return
        self.lbl_author.setText(g.name)
        self.lbl_author_desc.setText(f"книг: {len(g.books)}")
        self.av_author.setPixmap(avatar(g.initials, 56, bg="#DDE7F2", fg="#2D4A6B"))
        self.lbl_bio.setText("")
        self.lbl_source.setText("")
        self.rebuild_books()
        self._load_author_wiki(g)

    def set_checked(self, key: str, on: bool):
        if on:
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self._refresh_selbar()
        self._refresh_check_all()

    def toggle_all(self):
        keys = [g.key for g in self.visible_groups()]
        on = not all(k in self.checked for k in keys)
        for k in keys:
            (self.checked.add if on else self.checked.discard)(k)
            row = self.author_rows.get(k)
            if row:
                row.check.blockSignals(True)
                row.check.setChecked(on)
                row.check.blockSignals(False)
        self._refresh_selbar()
        self._refresh_check_all()

    def clear_checked(self):
        self.checked.clear()
        for row in self.author_rows.values():
            row.check.blockSignals(True)
            row.check.setChecked(False)
            row.check.blockSignals(False)
        self._refresh_selbar()
        self._refresh_check_all()

    def _refresh_check_all(self):
        keys = [g.key for g in self.visible_groups()]
        self.ch_all.blockSignals(True)
        self.ch_all.setChecked(bool(keys) and all(k in self.checked for k in keys))
        self.ch_all.blockSignals(False)

    def _missing_books(self, g: library.AuthorGroup) -> list[core.Book]:
        return [b for b in g.books if self.status_of(b) in MISSING]

    def _refresh_selbar(self):
        groups = [self.by_key[k] for k in self.checked if k in self.by_key]
        self.selbar.setVisible(bool(groups))
        n = sum(len(self._missing_books(g)) for g in groups)
        self.lbl_sel.setText(f"Выбрано авторов: {len(groups)} · не готово книг: {n}")
        self.btn_conv_sel.setEnabled(n > 0)

    def convert_checked(self):
        items = [(b, self.by_key[k]) for k in sorted(self.checked) if k in self.by_key for b in self._missing_books(self.by_key[k])]
        if not items:
            return
        if self.enqueue(items):
            self.clear_checked()

    # --------------------------------------------------------------- Книги --

    def set_filter(self, key: str):
        self.filter = key
        self.rebuild_authors()

    def rebuild_books(self):
        g = self.by_key.get(self.current_key) if self.current_key else None
        self.lst_books.blockSignals(True)
        self.lst_books.clear()
        self.book_rows.clear()
        books = [b for b in (g.books if g else []) if matches_filter(self.status_of(b), self.filter)]
        q = self.ed_search.text().strip().lower().replace("ё", "е")
        if q and g and q not in g.sort_name.lower().replace("ё", "е") and q not in g.name.lower().replace("ё", "е"):
            books = [b for b in books if q in b.display_title.lower().replace("ё", "е")]
        current_item = None
        for b in books:
            row = BookRow(self, b, g)
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, str(b.path))
            it.setSizeHint(QSize(100, 82 if not row.reading_chip.isHidden() else 66))
            self.lst_books.addItem(it)
            self.lst_books.setItemWidget(it, row)
            self.book_rows[str(b.path)] = row
            self._set_small_cover(row)
            if self.current_book and str(self.current_book.path) == str(b.path):
                current_item = it
        if current_item is None and books:
            current_item = self.lst_books.item(0)
        if current_item is not None:
            self.lst_books.setCurrentItem(current_item)
        self.lst_books.blockSignals(False)
        self.lbl_no_books.setVisible(bool(g) and not books)
        self._refresh_missing_button()
        if current_item is not None:
            self.show_book(self.group_of[current_item.data(Qt.ItemDataRole.UserRole)], current_item.data(Qt.ItemDataRole.UserRole))
        elif g and g.books:
            self.show_book(g, str(g.books[0].path))

    def _refresh_missing_button(self):
        g = self.by_key.get(self.current_key) if self.current_key else None
        n = len(self._missing_books(g)) if g else 0
        self.btn_missing.setVisible(n > 0)
        self.btn_missing.setText(f"Сконвертировать недостающие: {n}")

    def convert_author_missing(self):
        g = self.by_key.get(self.current_key) if self.current_key else None
        if g:
            self.enqueue([(b, g) for b in self._missing_books(g)])

    def _on_book_item(self, cur, _prev):
        if cur is not None:
            path = cur.data(Qt.ItemDataRole.UserRole)
            self.show_book(self.group_of[path], path)

    def show_book(self, g: library.AuthorGroup, path: str):
        b = next((x for x in g.books if str(x.path) == path), None)
        if b is None:
            return
        self.current_book = b
        self.det_title.setText(b.display_title)
        self.det_author.setText(g.name)
        f = self.det_fields
        f["series"].setText(b.series_text or "—")
        f["year"].setText(b.year or "—")
        f["genre"].setText(b.genre_text or (", ".join(b.genres) if b.genres else "—"))
        try:
            rel = b.path.relative_to(Path(self.s.library_root))
            f["fb2"].setText(str(rel))
        except ValueError:
            f["fb2"].setText(str(b.path))
        f["fb2"].setToolTip(str(b.path))
        if b.annotation:
            self.det_ann_label.setText("Аннотация (из FB2)")
            self.det_ann.setText(b.annotation)
        elif self.s.wiki_enabled:
            self.det_ann_label.setText("Описание")
            self.det_ann.setText("Ищу описание в Википедии…")
            self._load_book_wiki(b, g)
        else:
            self.det_ann_label.setText("Аннотация")
            self.det_ann.setText("В книге нет аннотации.")
        self._refresh_detail_status()
        self.refresh_reading_detail()
        self._set_big_cover(b, g)

    def refresh_reading_detail(self):
        from ..reading import STATUSES, date_label
        tracker = getattr(self.main, "reading_tab", None)
        b = self.current_book
        self.btn_reading.setEnabled(bool(tracker and b))
        g = self.group_of.get(str(b.path)) if b else None
        entry = tracker.entry_for_book(b, g.name if g else "") if tracker and b else None
        self.reading_detail.setText("\n".join(x for x in
            (STATUSES[entry.status], "★" * entry.rating, date_label(entry)) if x) if entry else "")

    def edit_reading(self):
        tracker = getattr(self.main, "reading_tab", None)
        if tracker and self.current_book:
            g = self.group_of.get(str(self.current_book.path))
            tracker.edit_book(self.current_book, g.name if g else "")

    def search_from_reading(self, title, author=""):
        self._reading_search = (title, author)
        self._reading_author = author
        self.btn_reading_author.setVisible(bool(author))
        if self.scanned_once:
            self.apply_reading_search()
        else:
            self.ensure_loaded()
            if not self.s.library_root:
                self.main.notify("Выберите папку с FB2, чтобы искать в библиотеке.", 12000)

    def apply_reading_search(self):
        title, author = self._reading_search
        self._reading_search = None
        self.filter_buttons["all"].setChecked(True)
        self.filter = "all"
        self.ed_search.setText(title)
        self._search_timer.stop()
        self.rebuild_authors()
        matches = [(g, b) for g in self.groups for b in g.books
                   if library.norm_title(b.display_title) == library.norm_title(title)
                   and (not author or library.norm_words(author) in
                        (library.norm_words(g.name), library.norm_words(b.first_author)))]
        if len(matches) == 1:
            g, b = matches[0]
            self.current_book = b
            for i in range(self.lst_authors.count()):
                it = self.lst_authors.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == g.key:
                    self.lst_authors.setCurrentItem(it)
                    break
            self.show_author(g.key)
            for i in range(self.lst_books.count()):
                it = self.lst_books.item(i)
                if it.data(Qt.ItemDataRole.UserRole) == str(b.path):
                    self.lst_books.setCurrentItem(it)
                    break
        elif not self.visible_groups():
            self.main.notify("По этому запросу ничего не найдено. Попробуйте другое название или все книги автора. Для добавления нужен FB2 или FB2.ZIP.", 20000)

    def search_reading_author(self):
        if self._reading_author:
            self.ed_search.setText(self._reading_author)

    def _refresh_detail_status(self):
        b = self.current_book
        if not b:
            return
        st = self.status_of(b)
        out = self.outputs.get(str(b.path))
        if st in ("done", "changed") and out:
            try:
                self.det_fields["kindle"].setText(str(out.relative_to(Path(self.s.kindle_root))))
            except ValueError:
                self.det_fields["kindle"].setText(str(out))
            self.det_fields["kindle"].setToolTip(str(out))
        elif st == "error":
            self.det_fields["kindle"].setText("ошибка: " + self.errors.get(str(b.path), ""))
            self.det_fields["kindle"].setToolTip(self.errors.get(str(b.path), ""))
        else:
            self.det_fields["kindle"].setText({"queued": "в очереди", "run": "конвертируется…"}.get(st, "ещё не сконвертирована"))
        label, primary, enabled = action_for(st)
        self.det_action.setText(label)
        self.det_action.setEnabled(enabled)
        set_primary(self.det_action, primary)

    def book_action(self, b: core.Book, g: library.AuthorGroup):
        st = self.status_of(b)
        if st == "done":
            out = self.outputs.get(str(b.path))
            if out:
                show_in_explorer(out)
            return
        if st in ("none", "changed", "error"):
            self.enqueue([(b, g)])

    # ------------------------------------------------------------- Обложки --

    def _seed(self, b: core.Book) -> int:
        return sum(map(ord, b.display_title)) % 6

    def _set_small_cover(self, row: BookRow):
        key = str(row.book.path)
        if key in self.small:
            row.cover.setPixmap(self.small[key])
            return
        row.cover.setPixmap(cover_placeholder(row.book.display_title, "", SMALL.width(), SMALL.height(), self._seed(row.book)))
        if row.book.has_cover:
            self._load_cover(row.book)

    def _set_big_cover(self, b: core.Book, g: library.AuthorGroup):
        key = str(b.path)
        if key in self.big:
            self.det_cover.setPixmap(self.big[key])
        else:
            self.det_cover.setPixmap(cover_placeholder(b.display_title, g.name, BIG.width(), BIG.height(), self._seed(b)))
            if b.has_cover:
                self._load_cover(b)

    def _load_cover(self, b: core.Book):
        key = str(b.path)

        def work():
            img = QImage()
            tp = library.thumb_path(b)
            if tp.is_file():
                img.load(str(tp))
            if img.isNull():
                data = core.read_cover(b.path)
                if not data or not img.loadFromData(data):
                    return
                img = img.scaledToHeight(BIG.height() * 2, Qt.TransformationMode.SmoothTransformation)
                img.save(str(tp), "JPG", 88)
            ui_call(lambda: self._apply_cover(key, img))

        self.pool.submit(work)

    def _apply_cover(self, key: str, img: QImage):
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            return
        self.small[key] = rounded(pm, SMALL.width(), SMALL.height(), 3)
        self.big[key] = rounded(pm, BIG.width(), BIG.height(), 4)
        row = self.book_rows.get(key)
        if row:
            row.cover.setPixmap(self.small[key])
        if self.current_book and str(self.current_book.path) == key:
            self.det_cover.setPixmap(self.big[key])

    # ------------------------------------------------------------ Википедия --

    def _load_author_wiki(self, g: library.AuthorGroup):
        if not self.s.wiki_enabled:
            return
        ckey = "a:" + g.key
        cached = self.wiki_cache.get(ckey)
        if cached is not None:
            self._apply_author_wiki(g.key, cached)
            return
        self.lbl_source.setText("Ищу сведения в Википедии…")
        last = g.last

        def work():
            try:
                info = wiki.author_info(g.name, last)
                if info.get("thumb"):
                    try:
                        info["photo"] = str(wiki.download_thumb(info["thumb"]))
                    except Exception:
                        pass
                self.wiki_cache.put(ckey, info)
            except Exception:
                info = {"offline": True}
            ui_call(lambda: self._apply_author_wiki(g.key, info))

        self.pool.submit(work)

    def _apply_author_wiki(self, key: str, info: dict):
        if key != self.current_key:
            return
        g = self.by_key.get(key)
        if info.get("offline"):
            self.lbl_source.setText("Нет связи с Википедией — сведения загрузятся позже.")
            return
        if not info.get("extract"):
            self.lbl_source.setText("В Википедии статьи об авторе не нашлось.")
            return
        if info.get("description"):
            self.lbl_author_desc.setText(info["description"])
        self.lbl_bio.setText(info["extract"])
        url = info.get("url", "")
        self.lbl_source.setText(f'Источник: <a href="{url}">Википедия</a>' if url else "Источник: Википедия")
        photo = info.get("photo")
        if photo and Path(photo).is_file() and g:
            pm = QPixmap(photo)
            if not pm.isNull():
                self.av_author.setPixmap(avatar(g.initials, 56, photo=pm))

    def _load_book_wiki(self, b: core.Book, g: library.AuthorGroup):
        ckey = "b:" + library.norm_title(b.display_title) + "|" + g.key
        cached = self.wiki_cache.get(ckey)
        if cached is not None:
            self._apply_book_wiki(str(b.path), cached)
            return
        last = g.last

        def work():
            try:
                info = wiki.book_info(b.display_title, last)
                self.wiki_cache.put(ckey, info)
            except Exception:
                info = {"offline": True}
            ui_call(lambda: self._apply_book_wiki(str(b.path), info))

        self.pool.submit(work)

    def _apply_book_wiki(self, path: str, info: dict):
        if not self.current_book or str(self.current_book.path) != path:
            return
        if info.get("offline"):
            self.det_ann_label.setText("Описание")
            self.det_ann.setText("В книге нет аннотации, а Википедия сейчас недоступна.")
        elif info.get("extract"):
            self.det_ann_label.setText("Описание (из Википедии)")
            self.det_ann.setText(info["extract"])
        else:
            self.det_ann_label.setText("Описание")
            self.det_ann.setText("Аннотации нет ни в книге, ни в Википедии.")

    # ------------------------------------------------------------- Очередь --

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def enqueue(self, items: list[tuple[core.Book, library.AuthorGroup]]) -> bool:
        if not self.s.kindle_root:
            QMessageBox.information(self, core.APP_NAME, "Сначала выберите папку для книг Kindle.")
            self.choose_kindle()
            if not self.s.kindle_root:
                return False
        fbc = self.main.require_fbc()
        if not fbc:
            return False
        self.main.convert_tab.book_settings.collect()
        s = self.s.copy(if_exists="replace")
        added = 0
        with self.queue_lock:
            for b, g in items:
                key = str(b.path)
                st = self.status_of(b)
                if st in ("queued", "run"):
                    continue
                self.prev[key] = st
                existing = self.outputs.get(key) if st in ("changed", "done") else None
                self.pending.append(core.Job(b, library.target_path(b, g, s, existing), tag=key))
                added += 1
            self.q_total += added
        for b, _g in items:
            if self.status_of(b) not in ("queued", "run"):
                self.set_status(str(b.path), "queued")
        if added and not self.busy:
            self.worker = threading.Thread(target=self._work, args=(s, fbc), daemon=True)
            self.worker.start()
        self._refresh_queue_label()
        return added > 0

    def _work(self, s: core.Settings, fbc: Path):
        while True:
            with self.queue_lock:
                jobs, self.pending = self.pending, []
                if not jobs:
                    self.current_conv = None
                    break
                conv = core.Converter(jobs, s, fbc, on_update=lambda j: ui_call(lambda: self._on_job(j)),
                                      on_finish=lambda: None, index=self.main.conv_index)
                self.current_conv = conv
            conv.run()
        ui_call(self._on_queue_finished)

    def _on_job(self, job: core.Job):
        key = job.tag
        if job.status == "run":
            self.set_status(key, "run")
            return
        if job.status in ("done", "skip"):
            self.outputs[key] = job.output
            self.errors.pop(key, None)
            self.set_status(key, "done")
            self.q_ok += 1
        elif job.status == "error":
            self.errors[key] = job.message.replace("Ошибка: ", "")
            self.set_status(key, "error")
            self.q_err += 1
        elif job.status == "cancel":
            self.set_status(key, self.prev.get(key, "none"))
        self.q_done += 1
        self._refresh_queue_label()

    def _on_queue_finished(self):
        ok, err = self.q_ok, self.q_err
        self.q_total = self.q_done = self.q_ok = self.q_err = 0
        self._refresh_queue_label()
        self.main.notify(f"Сконвертировано книг: {ok}" + (f", ошибок: {err} (подробности — в строке книги)" if err else ""))

    def stop_queue(self):
        with self.queue_lock:
            pending, self.pending = self.pending, []
            conv = self.current_conv
        for j in pending:
            self.set_status(j.tag, self.prev.get(j.tag, "none"))
            self.q_done += 1
        if conv:
            conv.cancel()
        self._refresh_queue_label()

    def _refresh_queue_label(self):
        active = self.q_total > 0 and self.q_done < self.q_total
        self.btn_stop.setVisible(active)
        self.lbl_queue.setText(f"Конвертация: {self.q_done} из {self.q_total}" if active else "")

    def _author_source_changed(self, *_):
        value = self.cb_author_src.value()
        if value == self.s.author_from_folder:
            return
        self.s.author_from_folder = value
        self.s.save()
        self.checked.clear()
        self.current_key = None
        if self.s.library_root:
            self.rescan()

    def reset_data(self, confirm: bool = True):
        if self.busy or self.scanning:
            QMessageBox.information(self, core.APP_NAME, "Дождитесь окончания конвертации или чтения библиотеки.")
            return
        if confirm and QMessageBox.question(
                self, core.APP_NAME,
                "Сбросить данные библиотеки?\n\nПрограмма забудет сохранённые сведения о книгах, обложки и данные "
                "из Википедии и перечитает библиотеку заново.\n\nКниги FB2 и готовые файлы для Kindle не изменятся.") \
                != QMessageBox.StandardButton.Yes:
            return
        library.reset_caches()
        self.wiki_cache = wiki.WikiCache()
        self.small.clear()
        self.big.clear()
        self.groups = []
        self.checked.clear()
        self.scanned_once = False
        if self.s.library_root:
            self.rescan()
        self.main.notify("Данные библиотеки сброшены")

    def _toggle_wiki(self, on: bool):
        self.s.wiki_enabled = on
        self.s.save()
        if self.current_key:
            self.show_author(self.current_key)

    def settings_changed(self):
        pass

    def shutdown(self):
        self.stop_queue()
        self.pool.shutdown(wait=False, cancel_futures=True)
