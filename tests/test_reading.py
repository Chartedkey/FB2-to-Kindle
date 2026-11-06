import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fb2kindle import core, library, reading, recommendations


def test_precision_and_reports(tmp_path):
    store = reading.ReadingStore(tmp_path / "reading.sqlite3")
    for entry in [reading.Entry("День", "Автор", "read", precision="day", finished="2024-02-29"),
                  reading.Entry("Месяц", "Автор", "read", precision="month", finished="2024-02"),
                  reading.Entry("Год", "Автор", "read", precision="year", finished="2024"),
                  reading.Entry("Ранее", "Автор", "read"), reading.Entry("Читаю", "Автор", "reading")]:
        store.save(entry)
    entries = reading.ReadingStore(store.path).all()
    assert len(reading.completed(entries)) == 4
    assert len(reading.completed(entries, 2024)) == 3
    assert len(reading.completed(entries, 2024, month=2)) == 2
    assert len(reading.completed(entries, 2024, quarter=1)) == 2
    assert len(reading.completed(entries, 2024, quarter=4)) == 0
    assert "неизвестна" in reading.date_label(next(e for e in entries if e.title == "Ранее"))


@pytest.mark.parametrize("precision,finished", [("day", "2024-02-30"), ("month", "2024-13"), ("year", "2099"),
                                               ("day", "2024-1-01"), ("unknown-type", "")])
def test_invalid_dates(precision, finished):
    with pytest.raises(ValueError):
        reading.validate(reading.Entry("Книга", "Автор", "read", precision=precision, finished=finished), date(2026, 11, 6))


def test_dates_do_not_use_creation_time(tmp_path):
    store = reading.ReadingStore(tmp_path / "reading.db")
    e = reading.Entry("История", "Автор", "read", precision="unknown", finished="2026-11-06")
    store.save(e)
    assert store.get(e.key).finished == ""
    assert reading.completed(store.all(), date.today().year) == []


def test_library_reset_moves_and_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    core.Settings(library_root="unchanged").save()
    original = core.Settings.path().read_bytes()
    store = reading.ReadingStore()
    book = core.Book(tmp_path / "old.fb2", title="Я, робот", authors=[core.Author(first="Айзек", last="Азимов")])
    e = reading.Entry.from_book(book)
    e.status, e.rating = "read", 5
    store.save(e)
    (core.data_dir() / "library-cache.json").write_text("{}")
    library.reset_caches()
    moved = core.Book(tmp_path / "new.fb2", title=book.title, authors=book.authors)
    assert store.for_book(moved).rating == 5
    assert core.Settings.path().read_bytes() == original
    assert list((core.data_dir() / "backups").glob("before-reading-*/settings.json"))
    backup = tmp_path / "history-backup.sqlite3"
    store.backup(backup)
    assert reading.ReadingStore(backup).get(e.key).status == "read"
    store.remove(e.key)
    assert reading.ReadingStore(backup).get(e.key).rating == 5


def candidate(title, author="Автор", genres=None, sources=None, refs=None):
    return {"key": reading.identity(title, author), "title": title, "author": author,
            "genres": genres or [], "sources": sources or [], "refs": refs or []}


def test_recommendations_exclude_history_and_explicit_exclusions():
    candidates = [candidate("Прочитана"), candidate("Читаю"), candidate("Отложена"), candidate("Новая", "Другой"),
                  candidate("Не люблю", "Исключённый"), candidate("Триллер", genres=["Триллеры"])]
    entries = [reading.Entry("Прочитана", "Автор", "read"), reading.Entry("Читаю", "Автор", "reading"),
               reading.Entry("Отложена", "Автор", "paused")]
    result = recommendations.rank(candidates, entries, {"exclude_authors": ["Исключённый"], "exclude_genres": ["Триллеры"]})
    assert [c["title"] for c in result] == ["Новая"]


def test_low_rating_does_not_ban_genre_or_author():
    entries = [reading.Entry("Плохой детектив", "Автор", "read", rating=1)]
    choices = [candidate("Другой детектив", "Автор", ["Детективы"]), candidate("Другая книга", "Другой")]
    prefs = {"genres": ["Детективы"], "authors": ["Автор"]}
    result = recommendations.rank(choices, entries, prefs)
    assert result[0]["title"] == "Другой детектив"
    assert prefs == {"genres": ["Детективы"], "authors": ["Автор"]}


def test_ratings_affect_shared_editorial_collection():
    source = {"id": "collection:1", "title": "Подборка", "url": "https://www.livelib.ru/selection/1"}
    e = reading.Entry("Ориентир", "Автор", "read", rating=5)
    choices = [candidate("Другая", "Б"), candidate(e.title, e.author, sources=[source]), candidate("Похожая", "А", sources=[source])]
    result = recommendations.rank(choices, [e], {})
    assert result[0]["title"] == "Похожая"
    assert "понравились" in result[0]["reason"]
    e.rating = 1
    assert recommendations.rank(choices, [e], {})[0]["title"] == "Другая"


def test_external_refs_exclude_other_title():
    entry = reading.Entry("Другой перевод", "Автор", "read", refs=["work:123"])
    assert recommendations.rank([candidate("Название из каталога", refs=["work:123"])], [entry], {}) == []


def test_next_in_series_and_diversity():
    e = reading.Entry("Начало", "А", "read", rating=5, series="Цикл", series_num=1)
    candidates = [{**candidate("Продолжение", "А"), "series": "Цикл", "series_num": 2},
                  candidate("Ещё", "А"), candidate("Новое", "Б")]
    result = recommendations.rank(candidates, [e], {})
    assert result[0]["title"] == "Продолжение"
    assert "Следующая" in result[0]["reason"]
    assert result[1]["author"] == "Б"


def test_bulk_is_atomic(tmp_path):
    store = reading.ReadingStore(tmp_path / "reading.db")
    with pytest.raises(ValueError):
        store.save_many([reading.Entry("Первая", "А", "read"), reading.Entry("", "Б", "read")])
    assert store.all() == []
