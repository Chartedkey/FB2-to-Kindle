import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures import make_library
from fb2kindle import core, reading
from fb2kindle.ui.main_window import MainWindow, create_app
from fb2kindle.ui.reading_dialogs import EntryDialog
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QDialog


def wait(app, cond, seconds=15):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(.01)
    return False


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = make_library(tmp_path / "Книги")
    settings = core.Settings(library_root=str(root), kindle_root=str(tmp_path / "Kindle"), wiki_enabled=False)
    app = create_app([])
    win = MainWindow(settings)
    win.show()
    win.tabs.setCurrentIndex(1)
    assert wait(app, lambda: win.library_tab.scanned_once)
    yield app, win
    win.close()
    app.processEvents()


def test_library_tracker_recommendations_and_reset(window):
    app, win = window
    tracker = win.reading_tab
    assert tracker is not None
    assert win.tabs.tabText(2) == "Моё чтение"
    lib = win.library_tab
    g = next(g for g in lib.groups if g.name == "Айзек Азимов")
    book = next(b for b in g.books if b.title == "Я, робот")
    conversion_before = dict(lib.status)
    entry = reading.Entry.from_book(book)
    entry.status, entry.rating = "read", 5
    assert tracker.save_entry(entry)
    assert tracker.entry_for_book(book).rating == 5
    assert lib.status == conversion_before
    lib.show_author(g.key)
    assert lib.book_rows[str(book.path)].reading_chip.text() == "Прочитано · ★★★★★"
    tracker.go("recs")
    tracker.store.set_pref("rec_source", "local")
    tracker.refresh()
    assert book.title not in [c["title"] for c in tracker.candidates]
    tracker.find_in_library("Конец Вечности", "Айзек Азимов")
    assert win.tabs.currentWidget() is lib
    assert lib.current_book.title == "Конец Вечности"
    tracker.find_in_library("Совсем другое название", "Айзек Азимов")
    assert lib.ed_search.text() == "Совсем другое название"
    assert lib.btn_reading_author.isVisible()
    lib.search_reading_author()
    assert lib.ed_search.text() == "Айзек Азимов"
    lib.reset_data(confirm=False)
    assert wait(app, lambda: lib.scanned_once and not lib.scanning)
    assert tracker.store.get(entry.key).rating == 5
    tracker.undo()
    assert tracker.store.get(entry.key) is None
    assert lib.status == conversion_before


def test_dialog_precision_and_dark_palette(window):
    app, win = window
    old = app.palette()
    dark = QPalette(old)
    dark.setColor(QPalette.ColorRole.Window, QColor("#111111"))
    dark.setColor(QPalette.ColorRole.ButtonText, QColor("white"))
    app.setPalette(dark)
    dialog = EntryDialog(reading.Entry("Книга", "Автор"), win, read=True)
    dialog.show()
    app.processEvents()
    assert dialog.save_button.text() == "Сохранить"
    assert dialog.save_button.palette().color(QPalette.ColorRole.ButtonText).name() == "#ffffff"
    assert dialog.save_button.palette().color(QPalette.ColorRole.Button).name() == "#005fb8"
    cancel = next(b for b in dialog.findChildren(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton)
                  if b.text() == "Отмена")
    assert cancel.palette().color(QPalette.ColorRole.ButtonText).name() == "#1b1b1b"
    assert dialog.palette().color(QPalette.ColorRole.Window).lightness() > 200
    dialog.rating.setCurrentIndex(5)
    dialog.precision.setCurrentIndex(dialog.precision.findData("year"))
    dialog.year.setValue(2024)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.entry.finished == "2024" and dialog.entry.rating == 5
    win.reading_tab.save_entry(dialog.entry)
    assert len(reading.completed(win.reading_tab.store.all(), 2024)) == 1
    assert reading.completed(win.reading_tab.store.all(), 2024, month=1) == []
    app.setPalette(old)


def test_broken_history_does_not_block_converter(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = core.data_dir() / "reading.sqlite3"
    original = b'not a sqlite database'
    path.write_bytes(original)
    app = create_app([])
    win = MainWindow(core.Settings(wiki_enabled=False))
    assert win.reading_tab is None
    assert win.tabs.count() == 3
    assert path.read_bytes() == original
    assert win.convert_tab is not None
    win.close()
    app.processEvents()


def test_broken_catalog_keeps_history_available(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = core.data_dir() / "editorial.sqlite3"
    original = b'not a catalog database'
    path.write_bytes(original)
    app = create_app([])
    win = MainWindow(core.Settings(wiki_enabled=False))
    tracker = win.reading_tab
    assert tracker is not None and tracker.catalog is None
    assert tracker.save_entry(reading.Entry("Моя книга", "Мой автор", "read"))
    tracker.go("recs")
    assert tracker.store.all()[0].title == "Моя книга"
    assert path.read_bytes() == original
    win.close()
    app.processEvents()


def test_covers_in_overview_list_and_recommendations(window):
    from PySide6.QtWidgets import QLabel, QTreeWidget
    from PySide6.QtGui import QImage
    app, win = window
    tracker = win.reading_tab
    group = next(g for g in win.library_tab.groups if g.name == 'Айзек Азимов')
    book = next(b for b in group.books if b.title == 'Я, робот')
    entry = reading.Entry.from_book(book)
    entry.status = 'read'
    tracker.save_entry(entry)
    source = QImage.fromData(core.read_cover(book.path))
    expected = source.pixelColor(source.width() // 2, source.height() // 2).name()
    def loaded():
        labels = [l for l in tracker.findChildren(QLabel, 'readingCover') if l.toolTip() == book.title and l.isVisible()]
        return any(l.pixmap().toImage().pixelColor(24, 36).name() == expected for l in labels)
    win.tabs.setCurrentIndex(2)
    tracker.go('overview')
    assert wait(app, loaded)
    tracker.go('read')
    app.processEvents()
    tree = next(t for t in tracker.findChildren(QTreeWidget) if t.isVisible())
    assert not tree.topLevelItem(0).icon(0).isNull()
    tracker.store.set_pref('rec_source', 'local')
    tracker.go('recs')
    app.processEvents()
    assert any(l.isVisible() for l in tracker.findChildren(QLabel, 'readingCover'))
