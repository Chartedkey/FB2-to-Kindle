import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fb2kindle import core, library  # noqa: E402
from fixtures import make_library  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = make_library(tmp_path / "Книги")
    kindle = tmp_path / "Для Kindle"
    return root, kindle


def test_scan_and_group(env):
    root, _ = env
    cache = library.MetaCache()
    seen = []
    books = library.scan_library(root, cache, progress=lambda d, t: seen.append((d, t)))
    assert len(books) == 7 and seen[-1] == (7, 7)
    groups = library.group_by_author(books, root)
    names = [g.sort_name for g in groups]
    assert names == ["Азимов Айзек", "Мьевилль Чайна"]  # битая книга без автора → имя папки «Мьевилль Чайна»
    m = groups[1]
    assert m.name == "Чайна Мьевилль" and m.initials == "ЧМ"
    assert len(m.books) == 5
    # серия идёт первой
    assert m.books[0].title == "Шрам"
    # повторное сканирование берёт данные из кэша
    cache2 = library.MetaCache()
    t = time.time()
    again = library.scan_library(root, cache2)
    assert len(again) == 7 and time.time() - t < 2


def test_status(env, tmp_path):
    root, kindle = env
    books = library.scan_library(root, library.MetaCache())
    groups = {g.sort_name: g for g in library.group_by_author(books, root)}
    m = groups["Мьевилль Чайна"]
    conv = core.ConversionIndex(tmp_path / "conv.json")
    # Готовый файл в папке «Чайна Мьевилль» (так их делала вкладка «Конвертация»)
    (kindle / "Чайна Мьевилль").mkdir(parents=True)
    (kindle / "Чайна Мьевилль" / "Кракен.kfx").write_bytes(b"x")
    # Название с двоеточием → «Город и город - роман.kfx»
    (kindle / "Чайна Мьевилль" / "Город и город - роман.kfx").write_bytes(b"x")
    # Книга, которую сохранили в папку с другим порядком имени
    (kindle / "Мьевилль Чайна").mkdir()
    (kindle / "Мьевилль Чайна" / "Шрам.kfx").write_bytes(b"x")
    idx = library.KindleIndex(kindle)
    st = {b.title: library.book_status(b, m.key, idx, conv)[0] for b in m.books}
    assert st["Кракен"] == "done"
    assert st["Город и город: роман"] == "done"
    assert st["Шрам"] == "done"
    assert st["Нью-Кробюзон."] == "none"
    # FB2 изменился после конвертации
    krak = next(b for b in m.books if b.title == "Кракен")
    out = kindle / "Чайна Мьевилль" / "Кракен.kfx"
    conv.record(krak.path, out)
    assert library.book_status(krak, m.key, idx, conv) == ("done", out)
    later = time.time() + 100
    os.utime(krak.path, (later, later))
    krak2 = core.read_metadata(krak.path)
    assert library.book_status(krak2, m.key, idx, conv)[0] == "changed"
    # Куда сохранять
    s = core.Settings(kindle_root=str(kindle))
    new = next(b for b in m.books if b.title == "Нью-Кробюзон.")
    assert library.target_path(new, m, s, None) == kindle / "Чайна Мьевилль" / "Нью-Кробюзон.kfx"
    assert library.target_path(krak2, m, s, out) == out


def test_norm():
    assert library.norm_words("Мьевилль Чайна") == library.norm_words("чайна  мьевилль")
    assert library.norm_title("Город и город - роман") == library.norm_title("Город и город: роман")
