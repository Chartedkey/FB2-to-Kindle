"""Проверка окна без экрана (QT_QPA_PLATFORM=offscreen): весь путь пользователя и снимки экрана."""

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fb2kindle import core  # noqa: E402
from fixtures import make_library  # noqa: E402

SHOTS = Path(__file__).resolve().parents[1] / "screenshots"


def wait(app, cond, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.03)
    app.processEvents()
    return cond()


def shot(widget, name):
    SHOTS.mkdir(exist_ok=True)
    widget.grab().save(str(SHOTS / f"{name}.png"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    lib = make_library(tmp_path / "Книги")
    kindle = tmp_path / "Для Kindle"
    (kindle / "Айзек Азимов").mkdir(parents=True)
    (kindle / "Айзек Азимов" / "Я, робот.kfx").write_bytes(b"x")  # сконвертирована раньше
    s = core.Settings.load()
    s.library_root, s.kindle_root, s.output_dir = str(lib), str(kindle), str(kindle)
    s.save()
    return s, lib, kindle


def test_full_flow(env):
    from fb2kindle.ui.main_window import MainWindow, create_app
    from fb2kindle.ui.settings_dialog import AdvancedDialog

    s, lib, kindle = env
    fbc = core.fbc_path()
    app = create_app([])
    win = MainWindow(s)
    win.resize(1320, 840)
    win.tabs.setCurrentIndex(0)
    win.show()
    wait(app, lambda: False, 0.5)
    shot(win, "01-convert-empty")

    # Вкладка «Конвертация»
    ct = win.convert_tab
    ct.add_paths([str(lib / "Мьевилль Чайна")])
    assert wait(app, lambda: len(ct.books) == 5)
    assert ct.ed_folder.text() == "Чайна Мьевилль"
    targets = [ct.tree.topLevelItem(i).text(3) for i in range(5)]
    assert any(t.replace("\\", "/") == "Чайна Мьевилль/Кракен.kfx" for t in targets), targets
    shot(win, "02-convert-list")
    if fbc:
        ct.start()
        assert wait(app, lambda: not ct.busy, 300)
        st = {j.book.path.name: j.status for j in ct.jobs}
        assert st.pop("Битая книга.fb2") == "error"
        assert set(st.values()) == {"done"}, [(j.book.path.name, j.message) for j in ct.jobs]
        assert (kindle / "Чайна Мьевилль" / "Кракен.kfx").is_file()
        shot(win, "03-convert-done")

    # Вкладка «Библиотека»
    lt = win.library_tab
    win.tabs.setCurrentIndex(1)
    assert wait(app, lambda: lt.scanned_once, 60)
    assert [g.sort_name for g in lt.groups] == ["Азимов Айзек", "Мьевилль Чайна"]
    asimov = lt.groups[0]
    robot = next(b for b in asimov.books if b.title == "Я, робот")
    assert lt.status_of(robot) == "done"
    eternity = next(b for b in asimov.books if b.title == "Конец Вечности")
    assert lt.status_of(eternity) == "none"
    m = lt.groups[1]
    lt.lst_authors.setCurrentRow(1)
    assert wait(app, lambda: lt.current_key == m.key)
    if fbc:
        krak = next(b for b in m.books if b.title == "Кракен")
        assert lt.status_of(krak) == "done"
    # подождать обложки и Википедию
    wait(app, lambda: "Ищу" not in lt.lbl_source.text(), 25)
    wait(app, lambda: False, 1.5)
    shot(win, "04-library-author")

    # книга без аннотации → описание из Википедии или сообщение
    nk = next(b for b in m.books if b.title == "Нью-Кробюзон.")
    lt.show_book(m, str(nk.path))
    wait(app, lambda: "Ищу" not in lt.det_ann.text(), 25)
    shot(win, "05-library-no-annotation")

    # Фильтр и поиск
    lt.set_filter("none")
    wait(app, lambda: False, 0.3)
    shot(win, "06-library-filter")
    lt.filter_buttons["all"].setChecked(True)
    lt.set_filter("all")
    lt.ed_search.setText("робот")
    assert wait(app, lambda: list(lt.author_rows) == [asimov.key], 5)
    lt.ed_search.setText("")
    assert wait(app, lambda: len(lt.author_rows) == 2, 5)

    # Несколько авторов сразу
    lt.toggle_all()
    assert lt.checked == {asimov.key, m.key}
    assert lt.selbar.isVisibleTo(win)
    wait(app, lambda: False, 0.3)
    shot(win, "07-library-selection")
    if fbc:
        lt.convert_checked()
        assert wait(app, lambda: not lt.busy and lt.q_total == 0, 300)
        statuses = {b.path.name: lt.status_of(b) for g in lt.groups for b in g.books}
        assert statuses.pop("Битая книга.fb2") == "error"
        assert set(statuses.values()) == {"done"}, statuses
        assert (kindle / "Айзек Азимов" / "Конец Вечности.kfx").is_file()
        wait(app, lambda: False, 0.5)
        shot(win, "08-library-done")

    # Источник имени автора и сброс данных
    lt.cb_author_src.set_value(False)
    assert wait(app, lambda: not lt.scanning and lt.groups, 60)
    assert s.author_from_folder is False
    lt.cb_author_src.set_value(True)
    assert wait(app, lambda: not lt.scanning and lt.groups, 60)
    lt.reset_data(confirm=False)
    assert wait(app, lambda: lt.scanned_once and not lt.scanning, 60)
    assert [g.name for g in lt.groups] == ["Айзек Азимов", "Чайна Мьевилль"]
    assert len(lt.groups[1].books) == 5            # битая книга тоже у автора — по папке
    if fbc:
        assert lt.status_of(next(b for b in lt.groups[0].books if b.title == "Конец Вечности")) == "done"
    wait(app, lambda: False, 1.0)
    shot(win, "09-library-after-reset")
    lt.btn_rescan.menu().popup(lt.btn_rescan.mapToGlobal(lt.btn_rescan.rect().bottomLeft()))
    wait(app, lambda: False, 0.3)
    shot(win, "09b-rescan-menu")
    lt.btn_rescan.menu().hide()

    dlg = AdvancedDialog(s, win)
    dlg.show()
    wait(app, lambda: False, 0.3)
    shot(dlg, "10-advanced")
    dlg.reject()

    win.close()
    wait(app, lambda: False, 0.3)
