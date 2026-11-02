"""Проверки с интернетом (в автосборке не блокируют выпуск)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fb2kindle import updater, wiki  # noqa: E402


def test_latest_release():
    r = updater.latest_release()
    print(r.version, r.asset_name, r.size, r.sha256[:12])
    assert r.version and r.asset_url.endswith("-windows.zip") and len(r.sha256) == 64



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
