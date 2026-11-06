"""Объяснимый подбор. Жанры задаются явно, оценки книг их не изменяют."""
from __future__ import annotations

from . import core, library
from .reading import Entry, identity

GENRE_TERMS = {"Фантастика": ("фантаст", "антиутоп", "киберпанк"), "Фэнтези": ("фэнтези", "фентези"),
               "Детективы": ("детектив",), "Триллеры": ("триллер", "хоррор", "ужас", "готическ"),
               "Проза": ("проза", "роман", "литератур"), "Нон-фикшен": ("нонфик", "нон-фик", "эрудиц", "наук")}


def has_genre(candidate: dict, genre: str) -> bool:
    text = " ".join(candidate.get("genres", []) + [s["title"] for s in candidate.get("sources", [])]).casefold()
    return any(t in text for t in GENRE_TERMS.get(genre, (genre.casefold(),)))


def local_candidates(groups: list[library.AuthorGroup]) -> list[dict]:
    result = {}
    for g in groups:
        for book in g.books:
            if book.error:
                continue
            e = Entry.from_book(book, g.name)
            result.setdefault(e.key, {"key": e.key, "title": e.title, "author": e.author, "genres": e.genres,
                "series": e.series, "series_num": e.series_num, "path": str(book.path), "refs": [], "sources": [], "url": ""})
    return list(result.values())


def rank(candidates: list[dict], entries: list[Entry], prefs: dict, limit: int = 12) -> list[dict]:
    by_key = {e.key: e for e in entries}
    excluded_refs = {r for e in entries if e.status in ("read", "reading") for r in e.refs}
    favorite_authors = {library.norm_words(a) for a in prefs.get("authors", [])}
    excluded_authors = {library.norm_words(a) for a in prefs.get("exclude_authors", [])}
    # Оценка — сигнал о произведении. Предпочтение жанра из неё не выводится.
    liked_authors = {library.norm_words(e.author) for e in entries if e.rating >= 4}
    liked_series = {(library.norm_words(e.author), e.series) for e in entries if e.rating >= 4 and e.series}
    rated = {e.key: e for e in entries if e.status == "read" and e.rating}
    source_signals = {}
    for c in candidates:
        e = rated.get(c["key"])
        if e:
            signal = {1: -2, 2: -1, 3: 0, 4: 1, 5: 2}[e.rating]
            for s in c.get("sources", []):
                source_signals[s["id"]] = source_signals.get(s["id"], 0) + signal
    scored = []
    for c in candidates:
        key = c.get("key") or identity(c["title"], c["author"])
        entry = by_key.get(key)
        author = library.norm_words(c["author"])
        if (entry and entry.status in ("read", "reading", "paused")) or excluded_refs.intersection(c.get("refs", [])):
            continue
        if author in excluded_authors or any(has_genre(c, g) for g in prefs.get("exclude_genres", [])):
            continue
        score, reasons = 0, []
        if author in favorite_authors:
            score += 6
            reasons.append("Вы выбрали этого автора в любимые.")
        elif author in liked_authors:
            score += 2
            reasons.append("Вы высоко оценили другую книгу этого автора.")
        genres = [g for g in prefs.get("genres", []) if has_genre(c, g)]
        if genres:
            score += 3
            reasons.append("В ваших предпочтениях: " + ", ".join(genres) + ".")
        signal = sum(source_signals.get(s["id"], 0) for s in c.get("sources", []))
        score += max(-3, min(3, signal))
        if signal > 0:
            reasons.append("В этой подборке есть книги, которые вам понравились.")
        if (author, c.get("series")) in liked_series:
            read_numbers = [e.series_num for e in entries if e.status == "read" and
                            library.norm_words(e.author) == author and e.series == c.get("series")]
            if read_numbers and c.get("series_num", 0) == max(read_numbers) + 1:
                score += 5
                reasons.append("Следующая книга серии, которую вы читали.")
        if entry and entry.status == "wish":
            score += 1
        if not reasons:
            reasons.append("Книга из редакционной подборки." if c.get("sources") else "Непрочитанная книга вашей библиотеки.")
        scored.append({**c, "key": key, "score": score, "reason": " ".join(reasons[:2]), "planned": bool(entry and entry.status == "wish")})
    # Разнообразие: каждая уже показанная книга автора уменьшает приоритет следующей.
    result, authors = [], {}
    while scored and len(result) < limit:
        best = max(range(len(scored)), key=lambda i: scored[i]["score"] - 4 * authors.get(library.norm_words(scored[i]["author"]), 0))
        c = scored.pop(best)
        result.append(c)
        author = library.norm_words(c["author"])
        authors[author] = authors.get(author, 0) + 1
    return result
