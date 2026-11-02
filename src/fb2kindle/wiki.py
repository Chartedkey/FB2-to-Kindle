"""Справки об авторах и описания книг из русской Википедии (с кэшем на диске)."""

from __future__ import annotations

import json
import re
import threading
import urllib.parse
from pathlib import Path

from .core import data_dir, http_get, write_json_atomic

API = "https://ru.wikipedia.org/w/api.php"
WRITER_WORDS = ("писател", "поэт", "прозаик", "фантаст", "драматург", "романист", "автор", "журналист",
                "историк", "философ", "сценарист", "публицист", "литератор", "учёный", "ученый")


class WikiCache:
    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "wiki-cache.json")
        self._lock = threading.Lock()
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    def get(self, key: str):
        return self.data.get(key)

    def put(self, key: str, value: dict) -> None:
        with self._lock:
            self.data[key] = value
            try:
                write_json_atomic(self.path, self.data)
            except OSError:
                pass


def _search(query: str, limit: int = 5) -> list[dict]:
    params = {
        "action": "query", "format": "json", "formatversion": "2", "utf8": "1",
        "generator": "search", "gsrsearch": query, "gsrlimit": str(limit), "gsrnamespace": "0",
        "prop": "extracts|pageimages|description|info", "exintro": "1", "explaintext": "1",
        "exsentences": "4", "exlimit": "max", "piprop": "thumbnail", "pithumbsize": "240", "inprop": "url",
    }
    data = json.loads(http_get(API + "?" + urllib.parse.urlencode(params), timeout=20))
    pages = data.get("query", {}).get("pages", [])
    return sorted(pages, key=lambda p: p.get("index", 99))


def _clean(extract: str) -> str:
    extract = re.sub(r"\s*\([^()]*\)", "", extract, count=1) if len(extract) > 200 else extract
    return re.sub(r"\s+", " ", extract).strip()


def _stem(word: str) -> str:
    """Начало фамилии: «Мьевилль» и «Мьевиль», «Азимов» и «Азимова» совпадут."""
    w = word.lower().replace("ё", "е")
    return w[: min(len(w), max(4, min(5, len(w) - 2)))]


def _result(page: dict) -> dict:
    return {
        "title": page.get("title", ""),
        "description": page.get("description", ""),
        "extract": _clean(page.get("extract", "")),
        "thumb": (page.get("thumbnail") or {}).get("source", ""),
        "url": page.get("fullurl", ""),
    }


def author_info(name: str, last_name: str = "") -> dict:
    """Справка об авторе: {title, description, extract, thumb, url} или {} если не нашлось."""
    last = _stem(last_name or name.split()[-1])
    for query in (f"{name} писатель", name):
        for page in _search(query):
            title = page.get("title", "").lower().replace("ё", "е")
            text = (page.get("description", "") + " " + page.get("extract", "")[:400]).lower()
            if last in title and any(w in text for w in WRITER_WORDS):
                return _result(page)
    return {}


def book_info(title: str, author_last: str) -> dict:
    """Описание книги или {} если не нашлось."""
    last = _stem(author_last) if author_last else ""
    t = re.sub(r"\W+", " ", title.lower().replace("ё", "е")).strip()
    queries = [f"{title} {author_last} роман", f"{title} роман", f"{title} {author_last}", title] if author_last \
        else [f"{title} роман", title]
    seen = set()
    for query in queries:
        for page in _search(query):
            if page.get("pageid") in seen:
                continue
            seen.add(page.get("pageid"))
            ptitle = re.sub(r"\W+", " ", page.get("title", "").lower().replace("ё", "е"))
            text = (page.get("description", "") + " " + page.get("extract", "")).lower().replace("ё", "е")
            if t and t in ptitle and (not last or last in text):
                return _result(page)
    return {}


def thumb_file(url: str) -> Path:
    import hashlib
    d = data_dir() / "photos"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + ".jpg")


def download_thumb(url: str) -> Path | None:
    if not url:
        return None
    p = thumb_file(url)
    if not p.is_file():
        p.write_bytes(http_get(url, timeout=20))
    return p
