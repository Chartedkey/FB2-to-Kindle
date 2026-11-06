"""История чтения. Отдельная база, не участвующая в конвертации и её кэшах."""
from __future__ import annotations

import calendar
import hashlib
import json
import re
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import core, library

STATUSES = {"wish": "Хочу прочитать", "reading": "Читаю", "read": "Прочитано", "paused": "Отложено"}
PRECISIONS = {"unknown": "Прочитано ранее — дата неизвестна", "day": "Точная дата",
              "month": "Месяц и год", "year": "Только год"}
MONTHS = ("Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь")


def identity(title: str, author: str) -> str:
    # Только точное нормализованное совпадение. Другой перевод автоматически не угадываем.
    return hashlib.sha256((library.norm_title(title) + "|" + library.norm_words(author)).encode()).hexdigest()


@dataclass
class Entry:
    title: str
    author: str
    status: str = "wish"
    rating: int = 0
    precision: str = "unknown"
    finished: str = ""
    note: str = ""
    genres: list[str] = field(default_factory=list)
    series: str = ""
    series_num: int = 0
    paths: list[str] = field(default_factory=list)
    refs: list[str] = field(default_factory=list)
    key: str = ""
    cover_url: str = ""

    def __post_init__(self):
        if not self.key:
            self.key = identity(self.title, self.author)

    @classmethod
    def from_book(cls, book: core.Book, author: str = "") -> "Entry":
        return cls(book.display_title, book.first_author or author, genres=[core.GENRES.get(g, g) for g in book.genres],
                   series=book.series, series_num=book.series_num, paths=[str(book.path)])


def validate(entry: Entry, today: date | None = None):
    today = today or date.today()
    if not entry.title.strip() or not entry.author.strip():
        raise ValueError("Укажите название и автора.")
    if entry.status not in STATUSES or type(entry.rating) is not int or not 0 <= entry.rating <= 5:
        raise ValueError("Некорректный статус или оценка.")
    if entry.precision not in PRECISIONS:
        raise ValueError("Некорректная точность даты.")
    if entry.status != "read":
        entry.precision, entry.finished = "unknown", ""
        return
    if entry.precision == "unknown":
        entry.finished = ""
        return
    patterns = {"year": r"\d{4}", "month": r"\d{4}-\d{2}", "day": r"\d{4}-\d{2}-\d{2}"}
    if not re.fullmatch(patterns[entry.precision], entry.finished):
        raise ValueError("Укажите дату с выбранной точностью.")
    try:
        parts = list(map(int, entry.finished.split("-")))
        d = date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError as e:
        raise ValueError("Такой даты не существует.") from e
    if d > today:
        raise ValueError("Дата прочтения не может быть в будущем.")


def date_label(e: Entry) -> str:
    if e.status != "read":
        return ""
    if e.precision == "unknown":
        return "Прочитано ранее · дата неизвестна"
    if e.precision == "year":
        return e.finished + " год · месяц неизвестен"
    if e.precision == "month":
        y, m = e.finished.split("-")
        return f"{MONTHS[int(m) - 1]} {y} · день неизвестен"
    y, m, d = e.finished.split("-")
    return f"{d}.{m}.{y}"


def completed(entries: list[Entry], year: int | None = None, month: int | None = None,
              quarter: int | None = None) -> list[Entry]:
    result = []
    for e in entries:
        if e.status != "read":
            continue
        if year is not None and (e.precision == "unknown" or not e.finished.startswith(f"{year:04d}")):
            continue
        if month is not None or quarter is not None:
            if e.precision not in ("month", "day"):
                continue
            m = int(e.finished[5:7])
            if month is not None and m != month:
                continue
            if quarter is not None and (m - 1) // 3 + 1 != quarter:
                continue
        result.append(e)
    return result


def previous_periods(today: date) -> list[tuple[str, int, int | None]]:
    q = (today.month - 1) // 3 + 1
    return [(f"year:{today.year - 1}", today.year - 1, None),
            (f"quarter:{today.year if q > 1 else today.year - 1}:{q - 1 if q > 1 else 4}",
             today.year if q > 1 else today.year - 1, q - 1 if q > 1 else 4)]


class ReadingStore:
    def __init__(self, path: Path | None = None):
        self.path = path or core.data_dir() / "reading.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Старые форматы настроек/конвертаций не мигрируются. Снимок для ручного возврата.
        if path is None and not self.path.exists():
            backup = core.data_dir() / "backups" / ("before-reading-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
            for name in ("settings.json", "conversions.json"):
                src = core.data_dir() / name
                if src.is_file():
                    backup.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, backup / name)
        with self.connect() as db:
            v = db.execute("PRAGMA user_version").fetchone()[0]
            if v not in (0, 1):
                raise ValueError("История создана более новой версией приложения. Файл не изменён.")
            db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def all(self) -> list[Entry]:
        with self.connect() as db:
            return [Entry(**json.loads(row[0])) for row in db.execute("SELECT payload FROM entries ORDER BY rowid DESC")]

    def get(self, key: str) -> Entry | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM entries WHERE key=?", (key,)).fetchone()
        return Entry(**json.loads(row[0])) if row else None

    def for_book(self, book: core.Book, author: str = "") -> Entry | None:
        entry = self.get(Entry.from_book(book, author).key)
        if entry:
            return entry
        return next((e for e in self.all() if str(book.path) in e.paths), None)

    def save(self, entry: Entry):
        validate(entry)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO entries VALUES (?,?)", (entry.key, json.dumps(asdict(entry), ensure_ascii=False)))

    def save_many(self, entries: list[Entry]):
        for entry in entries:
            validate(entry)
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO entries VALUES (?,?)", [(e.key, json.dumps(asdict(e), ensure_ascii=False)) for e in entries])

    def remove(self, key: str):
        with self.connect() as db:
            db.execute("DELETE FROM entries WHERE key=?", (key,))

    def pref(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM preferences WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_pref(self, key: str, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO preferences VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def backup(self, destination: Path):
        if destination.resolve() == self.path.resolve():
            raise ValueError("Выберите другой файл для резервной копии.")
        with self.connect() as source:
            dest = sqlite3.connect(destination)
            try:
                source.backup(dest)
            finally:
                dest.close()
