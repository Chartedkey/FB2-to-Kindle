"""Публичные редакционные подборки LiveLib: возобновляемая загрузка, без входа.

Каталог не содержит историю чтения. Не обходит ограничения сайта и не считает
страницу CAPTCHA пустой подборкой. Один шаг — один запрос; состояние сохраняется.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import core
from .reading import identity
from .reading_covers import cover_url

BASE = "https://www.livelib.ru"
COLLECTION_INDEX = BASE + "/news/selections-novosti"
ARTICLE_INDEX = BASE + "/news/topic/280-novosti"
INTERVAL = 20  # секунды между запросами, в том числе между запусками
WEEK = 7 * 86400


class SourceError(RuntimeError):
    pass


class SitePaused(SourceError):
    pass


class PageUnavailable(SourceError):
    def __init__(self, url, status):
        self.url, self.status = url, status
        super().__init__(f"LiveLib ответил HTTP {status}. Страница отложена; остальные материалы продолжают загружаться.")


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)
    parent: "Node | None" = None

    def all(self, tag: str = "", cls: str = ""):
        for c in self.children:
            if isinstance(c, Node):
                if (not tag or c.tag == tag) and (not cls or cls in c.attrs.get("class", "").split()):
                    yield c
                yield from c.all(tag, cls)

    def text(self):
        return " ".join(c.text() if isinstance(c, Node) else c for c in self.children if not isinstance(c, Node) or c.tag not in ("script", "style"))


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.current = self.root
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs), parent=self.current)
        self.current.children.append(node)
        if tag not in self.VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, dict(attrs), parent=self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node.parent:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def site_url(href: str) -> str:
    u = urllib.parse.urlsplit(urllib.parse.urljoin(BASE, href))
    if u.scheme != "https" or u.hostname not in ("www.livelib.ru", "livelib.ru") or u.port or u.username:
        raise SourceError("Неожиданная ссылка LiveLib.")
    return urllib.parse.urlunsplit(("https", "www.livelib.ru", u.path, "", ""))


def page_url(url: str, page: int) -> str:
    return url + (f"/~{page}" if page > 1 else "")


def check_page(html: str):
    low = html.casefold()
    if any(s in low for s in ("слишком много запросов", "введите текст с картинки", "ratelimitcaptcha", "verify you are human")):
        raise SitePaused("LiveLib просит проверку или ограничил частоту запросов. Загрузка приостановлена; сохранённые подборки доступны.")
    # Старые страницы LiveLib — HTML 4 с опущенным тегом <html>.
    if len(html) < 50 or not any(s in low for s in ("<html", "<!doctype html", "<head>")):
        raise SourceError("Вместо страницы LiveLib получен неожиданный ответ.")


def fetch(url: str) -> str:
    req = urllib.request.Request(site_url(url), headers={"User-Agent": core.USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            site_url(r.url)
            data = r.read(6_000_001)
            if len(data) > 6_000_000:
                raise SourceError("Страница LiveLib превышает допустимый размер.")
            html = data.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise SitePaused("LiveLib временно ограничил доступ. Каталог сохранён; повторите позже.") from e
        if e.code in (404, 410):
            raise PageUnavailable(site_url(url), e.code) from e
        raise SourceError(f"LiveLib ответил HTTP {e.code}. Страница не пропущена.") from e
    check_page(html)
    return html


def editorial_owner(node: Node) -> bool:
    headers = list(node.all(cls="header-card"))
    if not headers:
        headers = list(node.all(cls="event-header"))  # старый шаблон подборок
    return any(urllib.parse.urlsplit(a.attrs.get("href", "")).path.rstrip("/") in
               ("/reader/Redakciya_LiveLib", "/users/12975196")
               for h in headers[:1] for a in h.all("a"))


def last_page(node: Node, path: str) -> int:
    return max([1] + [int(m.group(1)) for a in node.all("a")
                      if (m := re.fullmatch(re.escape(path) + r"/~(\d+)", urllib.parse.urlsplit(a.attrs.get("href", "")).path))])


def parse_index(html: str, kind: str) -> tuple[list[dict], int]:
    check_page(html)
    root = Document(html).root
    cls = "selection-card" if kind == "collection" else "discussiongroup-card"
    cards = list(root.all("article", cls))
    if not cards:
        raise SourceError("Структура списка LiveLib изменилась: карточки не найдены. Прогресс сохранён.")
    prefix = "/selection/" if kind == "collection" else "/news/post/"
    found = {}
    for card in cards:
        if not editorial_owner(card):
            continue
        for a in card.all("a"):
            path = urllib.parse.urlsplit(a.attrs.get("href", "")).path
            match = re.fullmatch(re.escape(prefix) + r"(\d+)(?:-[^/]+)?", path)
            title = " ".join(a.text().split())
            if match and title and title not in ("Вся подборка", "Развернуть"):
                # Статьи о событиях и новостях не становятся рекомендациями только из-за авторства.
                if kind == "article" and not re.search(r"книг|роман|почитать|чита[ею]|детектив|триллер|фэнтези|фантаст|подборк", title, re.I):
                    continue
                id = kind + ":" + match.group(1)
                found.setdefault(id, {"id": id, "kind": kind, "url": site_url(path), "title": title})
    path = "/news/selections-novosti" if kind == "collection" else "/news/topic/280-novosti"
    return list(found.values()), last_page(root, path)


def parse_collection(html: str) -> tuple[int, list[dict]]:
    check_page(html)
    root = Document(html).root
    if not editorial_owner(root):
        raise SourceError("Автор подборки не подтверждён как Redakciya_LiveLib. Данные не заменены.")
    titles = list(root.all("title"))
    match = re.search(r"—\s*(\d+)\s*кни", titles[0].text() if titles else "")
    if not match:
        raise SourceError("Не найден счётчик книг подборки; полнота не подтверждена.")
    books = []
    covers = page_covers(html)
    for a in root.all("a", "brow-book-name"):
        m = re.fullmatch(r"/(book|work)/(\d+)(?:-[^/]+)?", urllib.parse.urlsplit(a.attrs.get("href", "")).path)
        if not m:
            continue
        block = a.parent
        while block.parent and "brow" not in block.attrs.get("class", "").split():
            block = block.parent
        authors = list(block.all("a", "brow-book-author"))
        author = ", ".join(dict.fromkeys(" ".join(n.text().split()) for n in authors))
        if not author:
            # У антологий/сборников нет brow-book-author, но подпись обложки
            # содержит «Антология - Название». Такие книги тоже входят в счётчик.
            byline, separator, _title = a.attrs.get("title", "").partition(" - ")
            author = byline.strip() if separator else "Автор не указан"
            author = author or "Автор не указан"
        genres = [re.sub(r"^№\d+\s*в\s*", "", " ".join(g.text().split())) for g in block.all("a", "label-genre")]
        books.append({"ref": m.group(1) + ":" + m.group(2), "title": " ".join(a.text().split()),
                      "author": author, "genres": genres, "url": site_url(a.attrs["href"]),
                      "cover_url": covers.get(m.group(1) + ":" + m.group(2), "")})
    return int(match.group(1)), books


def parse_article(html: str) -> list[dict]:
    check_page(html)
    root = Document(html).root
    if not editorial_owner(root):
        raise SourceError("Автор статьи не подтверждён как Redakciya_LiveLib.")
    # Только ссылки в основном тексте публикации, не рекомендации в боковой колонке/комментариях.
    body = next((n for n in root.all() if n.attrs.get("itemprop") == "articleBody"), None)
    if body is None:
        body = next((n for n in root.all() if any(c in n.attrs.get("class", "").split()
                    for c in ("lenta-card__text", "group-topic__text", "topic-text", "lenta-card__margins"))), None)
    if body is None:
        raise SourceError("Не найден основной текст статьи. Каталог не изменён.")
    books = {}
    covers = page_covers(html)
    for a in body.all("a"):
        m = re.fullmatch(r"/(book|work)/(\d+)(?:-[^/]+)?", urllib.parse.urlsplit(a.attrs.get("href", "")).path)
        label = " ".join(a.text().split()) or a.attrs.get("title", "")
        if m and " - " in label:
            author, title = label.split(" - ", 1)
            books[m.group(1) + ":" + m.group(2)] = {"ref": m.group(1) + ":" + m.group(2),
                "title": title, "author": author, "genres": [], "url": site_url(a.attrs["href"]),
                "cover_url": covers.get(m.group(1) + ":" + m.group(2), "")}
    if len(books) < 2:
        return []
    return list(books.values())


def page_covers(html: str) -> dict[str, str]:
    """В статьях адреса обложек бывают в данных страницы, а не в тегах img."""
    import html as html_module
    found = {}
    for match in re.finditer(r'https://(?:s\d*|i)\.livelib\.ru/(boocover|workpic)/(\d+)/[^\s<>"\x27\\]+', html.replace('\\/', '/')):
        url = cover_url(html_module.unescape(match.group()))
        if url:
            key = ("book:" if match.group(1) == "boocover" else "work:") + match.group(2)
            found.setdefault(key, url)
    return found


class Catalog:
    def __init__(self, path: Path | None = None):
        self.path = path or core.data_dir() / "editorial.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))

    def documents(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT payload FROM documents ORDER BY rowid")]

    def put(self, doc):
        with self.connect() as db:
            # REPLACE удаляет строку и меняет rowid: незавершённая подборка уходила
            # в конец очереди после каждой страницы. UPDATE сохраняет её место.
            db.execute("INSERT INTO documents (id,payload) VALUES (?,?) "
                       "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                       (doc["id"], json.dumps(doc, ensure_ascii=False)))

    def discover(self, docs):
        known = {d["id"] for d in self.documents()}
        for d in docs:
            if d["id"] not in known:
                self.put({**d, "page": 1, "books": [], "staged": {}, "complete": False})

    def candidates(self):
        found = {}
        by_ref = {}
        for doc in self.documents():
            # При первом заполнении отдельные проверенные страницы уже полезны.
            # При обновлении готовой подборки используем прежний полный снимок.
            books = doc.get("books", [])
            partial = not books and not doc.get("complete", False)
            if partial:
                books = list(doc.get("staged", {}).values())
            for b in books:
                key = by_ref.get(b["ref"], identity(b["title"], b["author"]))
                by_ref[b["ref"]] = key
                source = {"title": doc["title"], "url": doc["url"], "id": doc["id"], "partial": partial}
                if key not in found:
                    found[key] = {**b, "key": key, "sources": [], "refs": []}
                out = found[key]
                if not out.get("cover_url") and b.get("cover_url"):
                    out["cover_url"] = b["cover_url"]
                if b["ref"] not in out["refs"]:
                    out["refs"].append(b["ref"])
                if source not in out["sources"]:
                    out["sources"].append(source)
                out["genres"] = list(dict.fromkeys(out["genres"] + b.get("genres", [])))
        return list(found.values())

    def reset_cycle(self):
        # Старый успешный состав доступен, пока новый не будет сверён целиком.
        for doc in self.documents():
            doc.update(page=1, staged={}, complete=False)
            self.put(doc)
        self.set_state("collection_index", 1)
        self.set_state("article_index", 1)
        self.set_state("collection_last_task", "index")
        self.set_state("article_last_task", "index")
        self.set_state("active_task", {})
        self.set_state("last_cycle", time.time())

    def has_pending_work(self):
        return (any(not d.get("complete") for d in self.documents()) or
                any(self.state(k + "_index", 1) for k in ("collection", "article")))

    def request_refresh(self):
        """Проверить сейчас = продолжить незавершённую загрузку, не сбросить её."""
        if not self.has_pending_work():
            self.reset_cycle()
        elif not self.state("last_cycle", 0):
            self.set_state("last_cycle", time.time())
        self.set_state("enabled", True)

    def progress(self):
        docs = self.documents()
        task = self.state("active_task", {})
        doc = next((d for d in docs if d["id"] == task.get("id")), None)
        detail = ""
        if task.get("type") == "index":
            kind = task["kind"]
            last = self.state(kind + "_index_last", 0)
            detail = f"Список {'подборок' if kind == 'collection' else 'статей'}: страница {task['page']}"
            if last:
                detail += f" из {last}"
            detail += "."
        elif doc:
            if doc.get("complete"):
                detail = f"Сверено: {doc['title']} · книг: {len(doc.get('books', []))}."
            else:
                count = len(doc.get("staged", {}))
                total = doc.get("expected")
                detail = f"Подборка: {doc['title']}\nПолучено книг: {count}"
                if total is not None:
                    detail += f" из {total}"
                detail += f" · страниц загружено: {max(0, doc.get('page', 1) - 1)}."
                if task.get("loading"):
                    detail += f" Загружается страница {task['page']}."
        return {"found": len(docs), "complete": sum(bool(d.get("complete")) for d in docs),
                "partial": sum(bool(d.get("staged")) and not d.get("complete") for d in docs),
                "detail": detail}

    def step(self, getter=fetch, progress=lambda: None) -> bool:
        deferred = self.state("deferred_pages", {})
        def ready(url):
            return deferred.get(url, {}).get("retry_after", 0) <= time.time()
        for kind, base in (("collection", COLLECTION_INDEX), ("article", ARTICLE_INDEX)):
            # Подборки загружаются раньше большого архива статей.
            pending = next((d for d in self.documents() if d["kind"] == kind and not d.get("complete")
                            and ready(page_url(d["url"], d.get("page", 1)))), None)
            page = self.state(kind + "_index", 1)
            index_url = page_url(base, page)
            index_ready = page and ready(index_url)
            index_started = self.state(kind + "_index") is not None
            # Чередуем обнаружение следующих материалов и загрузку книг.
            # Сами документы остаются в стабильной очереди до завершения.
            if pending and (not index_ready or not index_started or self.state(kind + "_last_task", "index") == "index"):
                task = {"type": "document", "id": pending["id"], "page": pending.get("page", 1), "loading": True,
                        "url": page_url(pending["url"], pending.get("page", 1))}
                self.set_state("active_task", task)
                progress()
                self._document_step(pending, getter)
                self.set_state("active_task", {**task, "loading": False})
                self.set_state(kind + "_last_task", "document")
                return True
            if index_ready:
                url = index_url
                task = {"type": "index", "kind": kind, "page": page, "loading": True, "url": url}
                self.set_state("active_task", task)
                progress()
                docs, last = parse_index(getter(url), kind)
                self.discover(docs)
                self.set_state(kind + "_index", page + 1 if page < last else 0)
                self.set_state(kind + "_index_last", last)
                self.set_state(kind + "_last_task", "index")
                self.set_state("active_task", {**task, "loading": False})
                return True
        return False

    def _document_step(self, doc, getter):
        page = doc.get("page", 1)
        html = getter(page_url(doc["url"], page))
        if doc["kind"] == "article":
            books = parse_article(html)
            doc.update(books=books, complete=True, checked=time.time())
        else:
            expected, books = parse_collection(html)
            if not books and expected:
                raise SourceError("Страница подборки не содержит книг. Загрузка остановлена без потери данных.")
            if page > 1 and doc.get("expected") != expected:
                doc.update(page=1, staged={})
                self.put(doc)
                raise SourceError("Подборка изменилась во время загрузки. После паузы её сверка начнётся сначала.")
            staged = dict(doc.get("staged", {}))
            before = len(staged)
            staged.update({b["ref"]: b for b in books})
            if len(staged) > expected or (page > 1 and len(staged) == before):
                raise SourceError("Состав страниц не совпадает со счётчиком LiveLib. Сверка остановлена.")
            doc.update(expected=expected, staged=staged, page=page + 1)
            if len(staged) == expected:
                doc.update(books=list(staged.values()), staged={}, complete=True, checked=time.time())
        self.put(doc)

    def run_batch(self, cancel: threading.Event, getter=fetch, budget=4, interval=INTERVAL, progress=lambda: None):
        if time.time() < self.state("retry_after", 0):
            return
        for _ in range(budget):
            delay = max(0, self.state("last_request", 0) + interval - time.time())
            if cancel.wait(delay):
                return
            self.set_state("last_request", time.time())
            try:
                if not self.step(getter, progress):
                    deferred = self.state("deferred_pages", {})
                    retries = [p["retry_after"] for p in deferred.values() if p["retry_after"] > time.time()]
                    if retries:
                        self.set_state("retry_after", min(retries))
                        self.set_state("message", "Доступные материалы загружены. Недоступные страницы будут проверены повторно; прогресс сохранён.")
                        progress()
                        return
                    self.set_state("message", "Все обнаруженные материалы сверены. Полнота всего архива сайта не гарантируется.")
                    self.set_state("last_complete", time.time())
                    self.set_state("enabled", False)
                    progress()
                    return
                deferred = self.state("deferred_pages", {})
                url = self.state("active_task", {}).get("url")
                if url in deferred:
                    del deferred[url]
                    self.set_state("deferred_pages", deferred)
                self.set_state("message", "Прогресс сохранён. Загрузка продолжается небольшими порциями.")
                self.set_state("retry_after", 0)
            except PageUnavailable as e:
                task = self.state("active_task", {})
                self.set_state("active_task", {**task, "loading": False})
                deferred = self.state("deferred_pages", {})
                deferred[task.get("url") or e.url] = {"status": e.status, "retry_after": time.time() + 15 * 60}
                self.set_state("deferred_pages", deferred)
                self.set_state("message", str(e))
                self.set_state("retry_after", 0)
            except Exception as e:
                task = self.state("active_task", {})
                if task:
                    self.set_state("active_task", {**task, "loading": False})
                self.set_state("message", str(e)[:500])
                self.set_state("retry_after", time.time() + (6 * 3600 if isinstance(e, SitePaused) else 15 * 60))
                progress()
                return
            progress()
            if cancel.is_set():
                return
