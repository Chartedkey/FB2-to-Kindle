"""Локальный индекс INPX и импорт выбранных FB2 из архивов коллекции."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from . import core, library

DEFAULT_FIELDS = "AUTHOR;GENRE;TITLE;SERIES;SERNO;FILE;SIZE;LIBID;DEL;EXT;DATE;LANG;LIBRATE;KEYWORDS"
UNKNOWN_AUTHOR = "Без автора"
MAX_FB2 = 128 * 1024 * 1024


def author_matches(author_key: str, query: str) -> bool:
    """Части имени ищутся как начала слов в любом порядке."""
    words = author_key.split()
    parts = library.norm_words(query).split()
    return bool(parts) and all(any(word.startswith(part) for word in words) for part in parts)


def language_code(value: str) -> str:
    return value.strip().casefold().replace("_", "-")


@dataclass(frozen=True)
class Variant:
    archive: str
    member: str
    title: str
    language: str
    series: str
    number: str
    added: str
    identifier: str

    @property
    def key(self):
        return self.archive, self.member

    @property
    def token(self):
        return hashlib.sha256("\0".join(self.key).encode()).hexdigest()[:12]


class Catalog:
    def __init__(self, path: Path):
        self.path = path

    def build(self, source: Path, *, refresh=False):
        stat = source.stat()
        signature = f"3|{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS source (signature TEXT NOT NULL)")
            row = db.execute("SELECT signature FROM source").fetchone()
            if not refresh and row and row[0] == signature:
                return
            with ZipFile(source) as bundle, db:
                db.execute("BEGIN")
                for table in ("author_books", "authors", "books"):
                    db.execute(f"DROP TABLE IF EXISTS {table}")
                db.execute("""CREATE TABLE books (
                    archive TEXT, member TEXT, title TEXT, title_key TEXT, language TEXT,
                    series TEXT, number TEXT, added TEXT, identifier TEXT,
                    PRIMARY KEY (archive, member))""")
                db.execute("CREATE TABLE authors (key TEXT PRIMARY KEY, name TEXT, search TEXT, folder TEXT)")
                db.execute("""CREATE TABLE author_books (
                    author TEXT, archive TEXT, member TEXT,
                    PRIMARY KEY(author, archive, member))""")
                self._read(bundle, db)
                db.execute("CREATE INDEX books_authors ON author_books(archive, member)")
                if not db.execute("SELECT 1 FROM books LIMIT 1").fetchone():
                    raise ValueError("В индексе нет записей FB2.")
                db.execute("DELETE FROM source")
                db.execute("INSERT INTO source VALUES (?)", (signature,))

    @staticmethod
    def _read(bundle, db):
        names = {Path(name).name.casefold(): name for name in bundle.namelist()}
        structure = names.get("structure.info")
        fields = bundle.read(structure).decode("utf-8-sig") if structure else DEFAULT_FIELDS
        columns = fields.strip().rstrip(";").upper().split(";")
        columns = [column.strip() for column in columns]
        if not {"AUTHOR", "TITLE", "FILE", "EXT"} <= set(columns):
            raise ValueError("В structure.info отсутствуют обязательные поля INPX.")
        for part in bundle.infolist():
            if part.is_dir() or not part.filename.casefold().endswith(".inp"):
                continue
            with bundle.open(part) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig") as stream:
                for line in stream:
                    values = line.rstrip("\r\n").split("\x04")
                    if not line.strip():
                        continue
                    row = dict(zip(columns, values))
                    if len(values) < len(columns):
                        raise ValueError(f"Неполная запись в {part.filename}.")
                    member = row["FILE"].strip()
                    ext = row["EXT"].strip().lstrip(".").casefold()
                    if row.get("DEL", "").strip() not in ("", "0") or not member or ext != "fb2":
                        continue
                    if not member.casefold().endswith(".fb2"):
                        member += ".fb2"
                    archive = row.get("FOLDER", "").strip() or Path(part.filename).stem
                    if not archive.casefold().endswith(".zip"):
                        archive += ".zip"
                    archive = archive.replace("\\", "/")
                    title = row["TITLE"].strip() or member
                    db.execute("INSERT OR IGNORE INTO books VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (archive, member, title, library.norm_title(title), row.get("LANG", ""),
                                row.get("SERIES", ""), row.get("SERNO", ""), row.get("DATE", ""),
                                row.get("LIBID", "") or Path(member).stem))
                    for author in (row["AUTHOR"].strip(":").split(":") or [""]):
                        last, first, middle = ([word.strip() for word in author.split(",")] + ["", ""])[:3]
                        given = " ".join(filter(None, (first, middle)))
                        name = " ".join(filter(None, (given, last))) or UNKNOWN_AUTHOR
                        folder = f"{last}, {given}" if " " in last and given else (
                            " ".join(filter(None, (last, given))) or UNKNOWN_AUTHOR)
                        key = library.norm_words(name)
                        search = folder.casefold().replace("ё", "е")
                        db.execute("INSERT OR IGNORE INTO authors VALUES (?, ?, ?, ?)", (key, name, search, folder))
                        db.execute("INSERT OR IGNORE INTO author_books VALUES (?, ?, ?)", (key, archive, member))

    def authors(self, query="", offset=0, limit=200):
        title = library.norm_title(query)
        with closing(sqlite3.connect(self.path)) as db:
            if not title:
                return db.execute("SELECT key, name FROM authors ORDER BY search, key LIMIT ? OFFSET ?",
                                  (limit, offset)).fetchall()
            db.create_function("name_matches", 1, lambda key: author_matches(key, query), deterministic=True)
            return db.execute("""SELECT a.key, a.name FROM authors a
                WHERE name_matches(a.key) OR a.key IN (
                    SELECT x.author FROM books b JOIN author_books x USING (archive, member)
                    WHERE instr(b.title_key, ?))
                ORDER BY name_matches(a.key) DESC, a.search, a.key LIMIT ? OFFSET ?""",
                (title, limit, offset)).fetchall()

    def languages(self):
        with closing(sqlite3.connect(self.path)) as db:
            return {language_code(value) for value, in db.execute("SELECT DISTINCT language FROM books")}

    def author_folder(self, author):
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT folder FROM authors WHERE key = ?", (author,)).fetchone()
            return row[0] if row else UNKNOWN_AUTHOR

    def books(self, author):
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("""SELECT archive, member, title, language, series, number, added, identifier
                FROM author_books JOIN books USING (archive, member)
                WHERE author = ? ORDER BY title_key, title, language, archive, member""", (author,))
            return [Variant(*row) for row in rows]


def archive_paths(root: Path):
    return {p.name.casefold(): p for p in root.iterdir() if p.is_file() and p.suffix.casefold() == ".zip"}


def archive_path(archives, variant):
    path = archives.get(Path(variant.archive).name.casefold())
    if path is None:
        raise FileNotFoundError(f"Архив не найден: {variant.archive}")
    return path


def member_info(archive, variant):
    try:
        info = archive.getinfo(variant.member)
    except KeyError:
        matches = [i for i in archive.infolist()
                   if Path(i.filename).name.casefold() == Path(variant.member).name.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Файл {variant.member} не найден однозначно в {variant.archive}.") from None
        info = matches[0]
    if info.is_dir() or info.file_size > MAX_FB2:
        raise ValueError(f"Недопустимый размер FB2: {variant.member}")
    return info


def read_cover(archives, variant):
    with ZipFile(archive_path(archives, variant)) as archive:
        return core.cover_from_fb2(archive.read(member_info(archive, variant)))


def metadata(archive, info):
    with archive.open(info) as stream:
        text = core.decode_fb2(stream.read(256 * 1024))
    result = {}
    for section in ("title-info", "publish-info"):
        match = re.search(rf"<(?:\w+:)?{section}\b.*?</(?:\w+:)?{section}>", text, re.S)
        if not match:
            continue
        try:
            node = ET.fromstring(core._strip_ns(match[0]))
        except ET.ParseError:
            continue
        if section == "title-info":
            names = []
            for person in node.findall("translator"):
                name = " ".join(value for field in ("first-name", "middle-name", "last-name")
                                if (value := core._text(person.find(field))))
                names.append(name or core._text(person.find("nickname")))
            result["Переводчик"] = ", ".join(filter(None, names))
            result["Язык"] = core._text(node.find("lang"))
        else:
            for field, label in (("publisher", "Издательство"), ("year", "Год издания"), ("isbn", "ISBN")):
                result[label] = core._text(node.find(field))
    return {label: value for label, value in result.items() if value}


def local_candidates(groups, author, root):
    titles = {}
    folders = set()
    imported = {}
    for group in groups:
        for book in group.books:
            if author not in (group.key, *(library.norm_words(a.display()) for a in book.authors)):
                continue
            if book.error:
                continue
            keys = {library.norm_title(book.display_title)}
            if not book.import_variant:
                stem = core.ARCHIVE_VARIANT_SUFFIX.sub("", core.stem_of(book.path))
                keys.add(library.norm_title(stem))
            for key in keys:
                titles.setdefault(key, []).append(book.path)
            if book.import_variant:
                imported.setdefault(book.import_variant, []).append(book.path)
            try:
                relative = book.path.relative_to(root)
                if len(relative.parts) > 1:
                    folders.add(root / relative.parts[0])
            except ValueError:
                pass
    return titles, folders, imported


def fingerprint(path, size):
    if path.suffix.casefold() == ".fb2" and path.stat().st_size != size:
        return None
    archive, stream = core._open_fb2(path)
    try:
        with stream:
            left = size
            digest = hashlib.sha256()
            while left:
                data = stream.read(min(left, 1024 * 1024))
                if not data:
                    return None
                digest.update(data)
                left -= len(data)
            if not stream.read(1):
                return digest.digest()
    finally:
        if archive:
            archive.close()


def match_variants(variants, titles, imported, archives):
    matched = {}
    by_archive = {}
    for variant in variants:
        if library.norm_title(variant.title) in titles or variant.token in imported:
            by_archive.setdefault(variant.archive, []).append(variant)
    hashes = {}
    for entries in by_archive.values():
        try:
            with ZipFile(archive_path(archives, entries[0])) as archive:
                for variant in entries:
                    try:
                        info = member_info(archive, variant)
                        candidates = list(dict.fromkeys(
                            titles.get(library.norm_title(variant.title), []) + imported.get(variant.token, [])))
                        for path in candidates:
                            key = path, info.file_size
                            if key not in hashes:
                                try:
                                    hashes[key] = fingerprint(*key)
                                except (OSError, ValueError, RuntimeError, BadZipFile):
                                    hashes[key] = None
                        possible = [path for path in candidates if hashes[path, info.file_size] is not None]
                        if possible:
                            with archive.open(info) as stream:
                                digest = hashlib.file_digest(stream, "sha256").digest()
                            for path in possible:
                                if hashes[path, info.file_size] == digest:
                                    matched[variant.key] = path
                                    break
                    except (OSError, ValueError, RuntimeError, BadZipFile):
                        continue
        except (OSError, ValueError, BadZipFile):
            continue
    return matched


def place_variant(temporary, folder, variant, size, occupied_titles):
    base = core.safe_name(variant.title, max_len=130)
    base_key = base.casefold()
    occupied = set()
    same_work = []
    for path in folder.iterdir():
        if not path.name.casefold().endswith(core.BOOK_SUFFIXES):
            continue
        stem = core.stem_of(path)
        normal = core.ARCHIVE_VARIANT_SUFFIX.sub("", stem).casefold()
        if normal == base_key or stem.casefold().endswith(f" [inpx-{variant.token}]"):
            same_work.append(path)
            occupied.add(stem.casefold())
    if same_work:
        expected = fingerprint(temporary, size)
        for existing in same_work:
            try:
                duplicate = fingerprint(existing, size) == expected
            except (OSError, ValueError, RuntimeError, BadZipFile):
                continue
            if duplicate:
                raise FileExistsError("Этот вариант уже сохранён.")
    number = 2 if library.norm_title(variant.title) in occupied_titles else 1
    while True:
        stem = base if number == 1 else f"{base} (вариант {number})"
        path = folder / (stem + ".fb2")
        if stem.casefold() not in occupied:
            try:
                if core.IS_WINDOWS:
                    temporary.rename(path)  # Windows не заменяет существующий файл.
                else:
                    os.link(temporary, path)
                return path
            except FileExistsError:
                pass
        number += 1


def import_variants(variants, root, folder, archives, occupied_titles=()):
    if not folder.resolve().is_relative_to(root.resolve()):
        raise ValueError("Папка автора находится за пределами библиотеки.")
    folder.mkdir(exist_ok=True)
    paths = {}
    errors = []
    by_archive = {}
    for variant in variants:
        by_archive.setdefault(variant.archive, []).append(variant)
    for entries in by_archive.values():
        try:
            with ZipFile(archive_path(archives, entries[0])) as archive:
                for variant in entries:
                    try:
                        info = member_info(archive, variant)
                        with tempfile.NamedTemporaryFile(dir=folder, prefix=".fb2-import-", delete=False) as output:
                            temporary = Path(output.name)
                            try:
                                with archive.open(info) as source:
                                    shutil.copyfileobj(source, output)
                                output.close()
                                path = place_variant(temporary, folder, variant, info.file_size, occupied_titles)
                            finally:
                                output.close()
                                temporary.unlink(missing_ok=True)
                        paths[variant.key] = path
                    except (OSError, ValueError, RuntimeError, BadZipFile) as err:
                        errors.append(f"{variant.title}: {err}")
        except (OSError, ValueError, BadZipFile) as err:
            errors.append(f"{entries[0].archive}: {err}")
    return paths, errors
