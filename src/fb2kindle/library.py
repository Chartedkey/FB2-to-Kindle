"""Библиотека: сканирование папки с FB2, группировка по авторам, статус конвертации."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .core import (BOOK_SUFFIXES, Book, ConversionIndex, Settings, data_dir, folder_author, read_metadata,
                   safe_name, write_json_atomic)

KINDLE_SUFFIXES = (".kfx", ".azw8", ".azw3", ".mobi")

# Статусы книги в библиотеке
NONE, DONE, CHANGED = "none", "done", "changed"


# --------------------------------------------------------------------------- #
#  Кэш метаданных
# --------------------------------------------------------------------------- #

class MetaCache:
    """Метаданные книг на диске: при повторном открытии библиотека не перечитывает файлы."""

    VERSION = 2

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "library-cache.json")
        self._lock = threading.Lock()
        self.books: dict[str, dict] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("v") == self.VERSION:
                self.books = data.get("books", {})
        except Exception:
            pass

    def get(self, path: Path, size: int, mtime: float) -> Book | None:
        d = self.books.get(str(path))
        if d and d.get("size") == size and abs(d.get("mtime", 0) - mtime) < 1:
            try:
                return Book.from_cache(d)
            except Exception:
                return None
        return None

    def put(self, book: Book) -> None:
        with self._lock:
            self.books[str(book.path)] = book.to_cache()

    def prune(self, keep: set[str]) -> None:
        with self._lock:
            for k in [k for k in self.books if k not in keep]:
                del self.books[k]

    def save(self) -> None:
        with self._lock:
            try:
                write_json_atomic(self.path, {"v": self.VERSION, "books": self.books})
            except OSError:
                pass


def scan_library(root: Path, cache: MetaCache, progress=None, cancelled=lambda: False) -> list[Book]:
    """Все книги FB2 в папке (с подпапками). progress(done, total) вызывается по ходу."""
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if n.lower().endswith(BOOK_SUFFIXES):
                files.append(Path(dirpath) / n)
    files.sort(key=lambda p: str(p).lower())
    books: list[Book] = []
    total = len(files)
    for i, f in enumerate(files):
        if cancelled():
            break
        try:
            st = f.stat()
        except OSError:
            continue
        b = cache.get(f, st.st_size, st.st_mtime)
        if b is None:
            b = read_metadata(f)
            cache.put(b)
        books.append(b)
        if progress and (i % 25 == 0 or i == total - 1):
            progress(i + 1, total)
    cache.prune({str(b.path) for b in books})
    cache.save()
    return books


# --------------------------------------------------------------------------- #
#  Авторы
# --------------------------------------------------------------------------- #

@dataclass
class AuthorGroup:
    key: str            # нормализованное имя
    name: str           # «Имя Фамилия»
    sort_name: str      # «Фамилия Имя»
    books: list[Book] = field(default_factory=list)
    last: str = ""      # фамилия — для поиска в Википедии

    @property
    def initials(self) -> str:
        parts = [p for p in re.split(r"[\s\-]+", self.name) if p and p[0].isalpha()]
        if not parts:
            return "?"
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


def norm_words(s: str) -> str:
    """Имя без учёта порядка слов, регистра, ё/е и знаков: «Мьевилль Чайна» = «Чайна Мьевилль»."""
    s = s.lower().replace("ё", "е")
    words = re.findall(r"\w+", s)
    return " ".join(sorted(words))


def norm_title(s: str) -> str:
    s = s.lower().replace("ё", "е")
    return "".join(re.findall(r"\w+", s))


def _top_folder(book: Book, root: Path) -> str:
    try:
        rel = book.path.parent.relative_to(root)
    except ValueError:
        return ""
    return rel.parts[0] if rel.parts else ""


def group_by_author(books: list[Book], root: Path, by_folder: bool = True) -> list[AuthorGroup]:
    """Книги по авторам.

    by_folder=True: автор — верхняя папка внутри библиотеки («Мьевилль Чайна» → «Чайна Мьевилль»),
    книги прямо в корне — по автору из FB2. by_folder=False: всегда по автору из FB2.
    """
    groups: dict[str, AuthorGroup] = {}
    from_meta: set[str] = set()
    for b in books:
        folder = _top_folder(b, root)
        a = b.first_author_obj
        meta = False
        if by_folder and folder:
            name, sort_name, last = folder_author(folder)
        elif a:
            name, sort_name, last = a.display(), a.sort_name(), a.last
            meta = True
        elif folder:
            name, sort_name, last = folder_author(folder)
        else:
            name = sort_name = "Без автора"
            last = ""
        key = norm_words(name) or "без автора"
        g = groups.get(key)
        if g is None:
            g = groups[key] = AuthorGroup(key=key, name=name, sort_name=sort_name, last=last)
        if meta and not by_folder and key not in from_meta:
            # имя из самой книги точнее имени папки
            g.name, g.sort_name, g.last = name, sort_name, last
            from_meta.add(key)
        g.books.append(b)
    for g in groups.values():
        g.books.sort(key=lambda b: (b.series.lower() if b.series else "\uffff", b.series_num,
                                    b.display_title.lower()))
    return sorted(groups.values(), key=lambda g: g.sort_name.lower().replace("ё", "е"))


# --------------------------------------------------------------------------- #
#  Готовые книги для Kindle
# --------------------------------------------------------------------------- #

class KindleIndex:
    """Все готовые файлы в папке для Kindle: поиск по названию и автору."""

    def __init__(self, root: Path | None):
        self.root = root
        self.by_title: dict[str, list[Path]] = {}
        if root and root.is_dir():
            for dirpath, _dirs, names in os.walk(root):
                for n in names:
                    low = n.lower()
                    ext = next((e for e in KINDLE_SUFFIXES if low.endswith(e)), None)
                    if not ext:
                        continue
                    title = re.sub(r"\s\(\d+\)$", "", n[: -len(ext)])  # «Кракен (2)» → «Кракен»
                    self.by_title.setdefault(norm_title(title), []).append(Path(dirpath) / n)

    def find(self, book: Book, author_key: str) -> Path | None:
        cands = self.by_title.get(norm_title(safe_name(book.display_title)), [])
        if not cands:
            return None
        for c in cands:
            if norm_words(c.parent.name) == author_key:
                return c
        return cands[0] if len(cands) == 1 else None


def book_status(book: Book, author_key: str, kindle: KindleIndex, conv: ConversionIndex) -> tuple[str, Path | None]:
    """(статус, путь к готовому файлу)."""
    rec = conv.get(book.path)
    if rec:
        out = Path(rec["output"])
        if out.is_file():
            changed = book.mtime > rec.get("src_mtime", 0) + 2 or book.size != rec.get("src_size", book.size)
            return (CHANGED if changed else DONE), out
    found = kindle.find(book, author_key)
    if found:
        try:
            if book.mtime > found.stat().st_mtime + 2:
                return CHANGED, found
        except OSError:
            pass
        return DONE, found
    return NONE, None


def target_path(book: Book, group: AuthorGroup, s: Settings, existing: Path | None) -> Path:
    """Куда сохранить книгу из библиотеки: папка Kindle / Имя Фамилия / Название.kfx."""
    if existing is not None:
        return existing.with_suffix("." + s.output_format)
    return Path(s.kindle_root) / safe_name(group.name, "Без автора") / (safe_name(book.display_title) + "." + s.output_format)


# --------------------------------------------------------------------------- #
#  Обложки (миниатюры на диске)
# --------------------------------------------------------------------------- #

def reset_caches() -> None:
    """Забыть сохранённые данные библиотеки: описания книг, обложки и сведения из Википедии.

    Книги, готовые файлы для Kindle и журнал конвертаций не трогаются.
    """
    import shutil
    d = data_dir()
    for name in ("library-cache.json", "wiki-cache.json"):
        try:
            (d / name).unlink()
        except FileNotFoundError:
            pass
    for sub in ("covers", "photos"):
        shutil.rmtree(d / sub, ignore_errors=True)


def thumb_path(book: Book) -> Path:
    h = hashlib.sha1(f"{book.path}|{book.size}|{book.mtime}".encode("utf-8")).hexdigest()[:20]
    d = data_dir() / "covers"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{h}.jpg"
