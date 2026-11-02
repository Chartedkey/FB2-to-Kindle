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


def test_author_from_folder(tmp_path, monkeypatch):
    """Разные написания автора в FB2 одной папки — один автор; имя и фамилия меняются местами."""
    from fixtures import fb2
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "Книги"
    b = root / "Беккет Сэмюэл"
    b.mkdir(parents=True)
    (b / "1.fb2").write_bytes(fb2("В ожидании Годо", "Самуэль", "Беккет"))
    (b / "2.fb2").write_bytes(fb2("Мёрфи", "Сэмюэл", "Беккетт"))
    (b / "Пьесы").mkdir()
    (b / "Пьесы" / "3.fb2").write_bytes(fb2("Конец игры", "Сэмюэль", "Беккет"))
    m = root / "Моэм Сомерсет Уильям"
    m.mkdir()
    (m / "1.fb2").write_bytes(fb2("Луна и грош", "Уильям", "Моэм"))
    lg = root / "Ле Гуин, Урсула"
    lg.mkdir()
    (lg / "1.fb2").write_bytes(fb2("Волшебник Земноморья", "Урсула", "Ле Гуин"))
    (root / "Без папки.fb2").write_bytes(fb2("Пикник на обочине", "Аркадий", "Стругацкий"))
    books = library.scan_library(root, library.MetaCache())
    groups = library.group_by_author(books, root)
    by = {g.sort_name: g for g in groups}
    assert set(by) == {"Беккет Сэмюэл", "Моэм Сомерсет Уильям", "Ле Гуин Урсула", "Стругацкий Аркадий"}
    assert by["Беккет Сэмюэл"].name == "Сэмюэл Беккет" and len(by["Беккет Сэмюэл"].books) == 3
    assert by["Беккет Сэмюэл"].last == "Беккет"
    assert by["Моэм Сомерсет Уильям"].name == "Сомерсет Уильям Моэм"
    assert by["Ле Гуин Урсула"].name == "Урсула Ле Гуин" and by["Ле Гуин Урсула"].last == "Ле Гуин"
    assert by["Стругацкий Аркадий"].name == "Аркадий Стругацкий"   # книга в корне — автор из FB2
    # Папка для Kindle — «Имя Фамилия»
    s = core.Settings(kindle_root=str(tmp_path / "K"))
    g = by["Беккет Сэмюэл"]
    assert library.target_path(g.books[0], g, s, None).parent.name == "Сэмюэл Беккет"
    # Режим «из книги»: написания снова разные
    meta = {g.sort_name for g in library.group_by_author(books, root, by_folder=False)}
    assert {"Беккет Самуэль", "Беккетт Сэмюэл", "Беккет Сэмюэль"} <= meta


def test_reset_caches(env):
    root, _ = env
    library.scan_library(root, library.MetaCache())
    cache = core.data_dir() / "library-cache.json"
    assert cache.is_file()
    (core.data_dir() / "covers").mkdir(exist_ok=True)
    conv = core.data_dir() / "conversions.json"
    conv.write_text("{}", encoding="utf-8")
    library.reset_caches()
    assert not cache.exists() and not (core.data_dir() / "covers").exists()
    assert conv.exists()   # журнал конвертаций не трогаем
