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
    assert catalog.state("active_task")["loading"] is False
    assert catalog.candidates() == []


def test_resume_and_preserve_old_catalog_until_complete(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    catalog.discover([{"id": "collection:1", "kind": "collection", "url": editorial.BASE + "/selection/1-demo", "title": "Подборка"}])
    catalog.step(lambda u: collection([("book", 1)], expected=2))
    assert catalog.documents()[0]["page"] == 2
    assert len(catalog.candidates()) == 1
    assert catalog.candidates()[0]["sources"][0]["partial"] is True
    reopened = editorial.Catalog(catalog.path)
    reopened.step(lambda u: collection([("work", 2)], expected=2))
    assert len(reopened.candidates()) == 2
    assert all(not c["sources"][0]["partial"] for c in reopened.candidates())
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


def test_document_updates_keep_queue_position(tmp_path):
    catalog = editorial.Catalog(tmp_path / "catalog.db")
    docs = [{"id": f"collection:{i}", "kind": "collection", "url": editorial.BASE + f"/selection/{i}-demo", "title": str(i)} for i in range(1, 4)]
    catalog.discover(docs)
    calls = []
    def getter(url):
        calls.append(url)
        return collection([("book", 2 if url.endswith('/~2') else 1)], expected=2)
    catalog.step(getter)
    catalog.step(getter)
    assert calls == [docs[0]["url"], docs[0]["url"] + '/~2']
    assert [d['id'] for d in catalog.documents()] == [d['id'] for d in docs]
    assert catalog.documents()[0]['complete']


def test_discover_next_index_page_without_hiding_first_books(tmp_path):
    catalog = editorial.Catalog(tmp_path / 'catalog.db')
    calls = []
    def getter(url):
        calls.append(url)
        if url == editorial.COLLECTION_INDEX:
            return index(next_page=True)
        if url == editorial.COLLECTION_INDEX + '/~2':
            return index().replace('/selection/1-demo', '/selection/2-demo')
        return collection([('book', 1)], expected=3)
    catalog.step(getter)
    catalog.step(getter)
    assert len(catalog.candidates()) == 1
    assert catalog.progress()['partial'] == 1
    assert '1 из 3' in catalog.progress()['detail']
    assert 'страниц загружено: 1' in catalog.progress()['detail']
    catalog.step(getter)
    assert len(catalog.documents()) == 2
    assert calls == [editorial.COLLECTION_INDEX, editorial.BASE + '/selection/1-demo', editorial.COLLECTION_INDEX + '/~2']


def test_check_now_preserves_partial_pages_across_restart(tmp_path):
    catalog = editorial.Catalog(tmp_path / 'catalog.db')
    catalog.discover([{'id': 'collection:1', 'kind': 'collection', 'title': 'Начатая подборка', 'url': editorial.BASE + '/selection/1-demo'}])
    catalog.step(lambda u: collection([('book', 1)], expected=2))
    before = catalog.documents()
    for _ in range(3):
        catalog.request_refresh()
    assert catalog.documents() == before
    reopened = editorial.Catalog(catalog.path)
    assert reopened.documents() == before
    assert len(reopened.candidates()) == 1
    reopened.step(lambda u: collection([('book', 2)], expected=2))
    assert len(reopened.candidates()) == 2
    assert reopened.documents()[0]['complete']


def test_refresh_only_restarts_completed_cycle(tmp_path):
    catalog = editorial.Catalog(tmp_path / 'catalog.db')
    catalog.put({'id': 'collection:1', 'kind': 'collection', 'title': 'Готовая', 'url': editorial.BASE + '/selection/1',
                 'complete': True, 'page': 2, 'staged': {}, 'books': [{'ref': 'book:1', 'title': 'Книга', 'author': 'Автор', 'genres': []}]})
    catalog.set_state('collection_index', 0)
    catalog.set_state('article_index', 0)
    assert not catalog.has_pending_work()
    catalog.request_refresh()
    assert catalog.has_pending_work()
    assert catalog.documents()[0]['page'] == 1
    assert len(catalog.candidates()) == 1
    assert not catalog.candidates()[0]['sources'][0]['partial']


def test_partial_220_catalog_is_visible_without_reset(tmp_path):
    import json
    catalog = editorial.Catalog(tmp_path / 'catalog.db')
    # Формат уже записанной 2.2.0 базы — без каких-либо новых полей.
    legacy = {'id': 'collection:1', 'kind': 'collection', 'url': editorial.BASE + '/selection/1',
              'title': 'Старая незавершённая', 'page': 2, 'expected': 100, 'books': [], 'complete': False,
              'staged': {'book:1': {'ref': 'book:1', 'title': 'Уже получена', 'author': 'Автор', 'genres': [], 'url': editorial.BASE + '/book/1'}}}
    with catalog.connect() as db:
        db.execute('INSERT OR REPLACE INTO documents VALUES (?,?)', (legacy['id'], json.dumps(legacy)))
    reopened = editorial.Catalog(catalog.path)
    assert reopened.documents() == [legacy]
    assert reopened.candidates()[0]['title'] == 'Уже получена'
    assert reopened.candidates()[0]['sources'][0]['partial']
    reopened.request_refresh()
    assert reopened.documents() == [legacy]


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


def test_refresh_keeps_full_snapshot_while_new_pages_are_partial(tmp_path):
    catalog = editorial.Catalog(tmp_path / 'catalog.db')
    old = {'ref': 'book:10', 'title': 'Прежняя книга', 'author': 'Автор', 'genres': []}
    catalog.put({'id': 'collection:1', 'kind': 'collection', 'title': 'Обновляемая',
                 'url': editorial.BASE + '/selection/1', 'complete': True, 'page': 2, 'staged': {}, 'books': [old]})
    catalog.reset_cycle()
    catalog.set_state('collection_index', 0)
    catalog.set_state('article_index', 0)
    catalog.step(lambda u: collection([('book', 1)], expected=2))
    assert [c['ref'] for c in catalog.candidates()] == ['book:10']
    assert not catalog.candidates()[0]['sources'][0]['partial']
    catalog.step(lambda u: collection([('book', 2)], expected=2))
    assert {c['ref'] for c in catalog.candidates()} == {'book:1', 'book:2'}


def test_partial_books_remain_available_during_site_pause(tmp_path):
    catalog = editorial.Catalog(tmp_path / 'catalog.db')
    catalog.discover([{'id': 'collection:1', 'kind': 'collection', 'title': 'Начатая', 'url': editorial.BASE + '/selection/1'}])
    catalog.step(lambda u: collection([('book', 1)], expected=2))
    before = catalog.documents()
    def captcha(_url):
        return '<html><body>С вашего адреса идёт слишком много запросов. Введите текст с картинки</body></html>'
    catalog.run_batch(threading.Event(), captcha, interval=0)
    assert catalog.documents() == before
    assert len(catalog.candidates()) == 1
    assert catalog.candidates()[0]['sources'][0]['partial']
    assert catalog.state('retry_after') > 0
