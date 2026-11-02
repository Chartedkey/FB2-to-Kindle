"""Поиск в Википедии (нужен интернет; в автосборке не блокирует выпуск)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fb2kindle import wiki  # noqa: E402


def test_author():
    for name, last in (("Чайна Мьевилль", "Мьевилль"), ("Айзек Азимов", "Азимов"), ("Джо Аберкромби", "Аберкромби")):
        info = wiki.author_info(name, last)
        print(name, "→", info.get("title"), "|", info.get("description"), "|", info.get("extract", "")[:120])
        assert info.get("extract"), name


def test_book():
    found = 0
    for title, last in (("Кракен", "Мьевилль"), ("Город и город", "Мьевилль"), ("Конец Вечности", "Азимов")):
        info = wiki.book_info(title, last)
        print(title, "→", info.get("title"), "|", info.get("extract", "")[:120])
        found += bool(info.get("extract"))
    assert found >= 2


def test_unknown_author():
    assert wiki.author_info("Несуществующий Автор Тестович", "Тестович") == {}
