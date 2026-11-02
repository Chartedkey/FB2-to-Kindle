import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fb2kindle import core  # noqa: E402
from fixtures import make_library  # noqa: E402


@pytest.fixture()
def lib(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    return make_library(tmp_path / "Книги")


def test_metadata(lib):
    b = core.read_metadata(lib / "Мьевилль Чайна" / "Шрам.fb2")
    assert b.title == "Шрам"
    assert b.first_author == "Чайна Мьевилль"
    assert b.authors[0].sort_name() == "Мьевилль Чайна"
    assert (b.series, b.series_num) == ("Бас-Лаг", 2)
    assert b.series_text == "Бас-Лаг, кн. 2"
    assert b.year == "2002"
    assert b.genre_text == "Фэнтези"
    assert b.annotation.startswith("Аннотация книги «Шрам».")
    assert "\n" in b.annotation
    assert b.has_cover
    z = core.read_metadata(lib / "Мьевилль Чайна" / "Город и город.fb2.zip")
    assert z.title == "Город и город: роман"
    bad = core.read_metadata(lib / "Мьевилль Чайна" / "Битая книга.fb2")
    assert bad.error


def test_cover(lib):
    data = core.read_cover(lib / "Мьевилль Чайна" / "Кракен.fb2")
    assert data and data[:2] == b"\xff\xd8"
    assert core.read_cover(lib / "Мьевилль Чайна" / "Нью-Кробюзон..fb2") is None
    assert core.read_cover(lib / "Мьевилль Чайна" / "Город и город.fb2.zip")[:2] == b"\xff\xd8"


def test_safe_name():
    assert core.safe_name("Рим / После Рима?") == "Рим - После Рима"
    assert core.safe_name("Город и город: роман") == "Город и город - роман"
    assert core.safe_name("Нью-Кробюзон.") == "Нью-Кробюзон"
    assert core.safe_name("CON") == "_CON"
    assert core.safe_name('A "b"') == "A «b»"


def test_plan_outputs(lib):
    books = [core.read_metadata(p) for p in core.find_books([str(lib)])]
    s = core.Settings(output_dir="/out", layout="per_author")
    names = {p.as_posix() for p in core.plan_outputs(books, s)}
    assert "/out/Чайна Мьевилль/Шрам.kfx" in names
    assert "/out/Айзек Азимов/Я, робот.kfx" in names


def test_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    s = core.Settings.load()
    assert s.as_ebook is False
    s.workers = 1
    s.save()
    assert core.Settings.load().workers == 1


def _fbc():
    p = core.fbc_path()
    if not p:
        pytest.skip("fbc не найден")
    return p


def test_conversion_parallel(lib, tmp_path):
    """Несколько книг одновременно — у каждой своя рабочая папка (ошибка fbc-panic.log в Windows)."""
    fbc = _fbc()
    books = [core.read_metadata(p) for p in core.find_books([str(lib)])]
    s = core.Settings(output_dir=str(tmp_path / "out"), layout="per_author", workers=4)
    jobs = [core.Job(b, t) for b, t in zip(books, core.plan_outputs(books, s))]
    idx = core.ConversionIndex(tmp_path / "conv.json")
    done = threading.Event()
    core.Converter(jobs, s, fbc, lambda j: None, done.set, index=idx).start()
    assert done.wait(300)
    status = {j.book.path.name: j.status for j in jobs}
    assert status.pop("Битая книга.fb2") == "error"
    assert set(status.values()) == {"done"}, [(j.book.path.name, j.message, j.details[-400:]) for j in jobs]
    assert (tmp_path / "out" / "Чайна Мьевилль" / "Кракен.kfx").stat().st_size > 1000
    assert idx.get(lib / "Мьевилль Чайна" / "Кракен.fb2")["output"].endswith("Кракен.kfx")


def test_folder_author():
    assert core.folder_author("Мьевилль Чайна") == ("Чайна Мьевилль", "Мьевилль Чайна", "Мьевилль")
    assert core.folder_author("Моэм Сомерсет Уильям")[0] == "Сомерсет Уильям Моэм"
    assert core.folder_author("Ле Гуин, Урсула")[:2] == ("Урсула Ле Гуин", "Ле Гуин Урсула")
    assert core.folder_author("Гомер") == ("Гомер", "Гомер", "Гомер")
