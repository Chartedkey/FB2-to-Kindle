import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fb2kindle import editorial


def collection(books, expected=2, owner="Redakciya_LiveLib"):
    return f'''<!DOCTYPE HTML><head><title>Подборка — {expected} книг</title></head><body>
    <div class="event-header"><a href="/reader/{owner}">Автор</a></div>
    <div id="booklist">''' + "".join(f'''<div class="brow"><a class="brow-book-name with-cycle" href="/{kind}/{i}-title">Книга {i}</a>
    <a class="brow-book-author" href="/author/7">Автор</a><a class="label-genre">№1 в Фантастика</a></div>'''
    for kind, i in books) + '</div></body>'


def index(owner="Redakciya_LiveLib", next_page=False):
    return f'''<!DOCTYPE html><html><head><title>Подборки</title></head><body><article class="selection-card">
    <div class="header-card"><a href="/reader/{owner}">Автор</a></div>
    <a href="/selection/1-demo">Подборка</a></article>{'<a href="/news/selections-novosti/~2">2</a>' if next_page else ''}
    </body></html>'''


def test_only_editorial_owner_and_old_html():
    docs, last = editorial.parse_index(index(next_page=True), "collection")
    assert len(docs) == 1 and last == 2
    assert editorial.parse_index(index("Other"), "collection")[0] == []
    assert editorial.parse_collection(collection([("work", 1)]))[1][0]["ref"] == "work:1"
    with pytest.raises(editorial.SourceError):
        editorial.parse_collection(collection([("book", 1)], owner="Other"))


def test_http_200_captcha_is_not_empty_catalog(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    catalog.discover([{"id": "collection:1", "kind": "collection", "url": editorial.BASE + "/selection/1-demo", "title": "Подборка"}])
    before = catalog.documents()
    def getter(_url):
        return '<html><body>С вашего адреса идёт слишком много запросов. Введите текст с картинки</body></html>'
    catalog.run_batch(threading.Event(), getter, interval=0)
    assert catalog.documents() == before
    assert catalog.state("retry_after") > 0
    assert "приостановлена" in catalog.state("message")
    assert catalog.candidates() == []


def test_resume_and_preserve_old_catalog_until_complete(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    catalog.discover([{"id": "collection:1", "kind": "collection", "url": editorial.BASE + "/selection/1-demo", "title": "Подборка"}])
    catalog.step(lambda u: collection([("book", 1)], expected=2))
    assert catalog.documents()[0]["page"] == 2
    assert catalog.candidates() == []  # Частичный список не выдаём за полный.
    reopened = editorial.Catalog(catalog.path)
    reopened.step(lambda u: collection([("work", 2)], expected=2))
    assert len(reopened.candidates()) == 2
    reopened.reset_cycle()
    assert len(reopened.candidates()) == 2
    reopened.step(lambda u: collection([("book", 3)], expected=1))
    assert [c["ref"] for c in reopened.candidates()] == ["book:3"]


def test_duplicate_page_is_detected(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    catalog.discover([{"id": "collection:1", "kind": "collection", "url": editorial.BASE + "/selection/1-demo", "title": "Подборка"}])
    catalog.step(lambda u: collection([("book", 1)]))
    with pytest.raises(editorial.SourceError, match="Состав страниц"):
        catalog.step(lambda u: collection([("book", 1)]))
    assert catalog.documents()[0]["page"] == 2


def test_changed_collection_restarts_without_erasing_old_data(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    catalog.discover([{"id": "collection:1", "kind": "collection", "url": editorial.BASE + "/selection/1-demo", "title": "Подборка"}])
    catalog.step(lambda u: collection([("book", 1)]))
    with pytest.raises(editorial.SourceError, match="изменилась"):
        catalog.step(lambda u: collection([("book", 2)], expected=3))
    assert catalog.documents()[0]["page"] == 1
    assert catalog.documents()[0]["staged"] == {}


def test_catalog_dedup_keeps_provenance(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    book = {"ref": "book:1", "title": "Книга", "author": "Автор", "genres": [], "url": editorial.BASE + "/book/1"}
    for i in range(2):
        catalog.put({"id": f"collection:{i}", "kind": "collection", "title": str(i), "url": editorial.BASE + f"/selection/{i}", "books": [book], "complete": True})
    result = catalog.candidates()
    assert len(result) == 1 and len(result[0]["sources"]) == 2


def test_cancelled_batch_makes_no_request(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    cancel = threading.Event()
    cancel.set()
    calls = []
    catalog.run_batch(cancel, getter=lambda u: calls.append(u), interval=0)
    assert calls == []


def test_article_only_main_body_and_verified_author():
    html = '''<!doctype html><html><body><div class="header-card"><a href="/reader/Redakciya_LiveLib">Редакция</a></div>
    <div class="lenta-card__text"><a href="/book/1-one">Автор - Первая</a><a href="/work/2-two">Автор - Вторая</a></div>
    <aside><a href="/book/3-ad">Автор - Реклама</a></aside></body></html>'''
    result = editorial.parse_article(html)
    assert [b["ref"] for b in result] == ["book:1", "work:2"]
    with pytest.raises(editorial.SourceError):
        editorial.parse_article(html.replace('Redakciya_LiveLib', 'SomebodyElse'))


def test_invalid_source_url():
    for u in ['https://evil.example/selection/1', 'http://www.livelib.ru/book/1', 'https://www.livelib.ru:999/book/1']:
        with pytest.raises(editorial.SourceError):
            editorial.site_url(u)


def test_same_external_id_with_renamed_title_is_one_candidate(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    for i, title in enumerate(["Первое название", "Другое название"]):
        catalog.put({"id": f"article:{i}", "kind": "article", "title": str(i), "url": editorial.BASE + f"/news/post/{i}",
            "books": [{"ref": "work:10", "title": title, "author": "Автор", "genres": [], "url": editorial.BASE + "/work/10"}], "complete": True})
    candidates = catalog.candidates()
    assert len(candidates) == 1 and len(candidates[0]["sources"]) == 2


def test_network_failure_preserves_cursor_and_cooldown(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    def fail(_url):
        raise OSError("Network unavailable")
    catalog.run_batch(threading.Event(), fail, interval=0)
    assert catalog.state("collection_index", 1) == 1
    assert catalog.state("retry_after") > 0
    assert catalog.documents() == []
    calls = []
    catalog.run_batch(threading.Event(), lambda u: calls.append(u), interval=0)
    assert calls == []
