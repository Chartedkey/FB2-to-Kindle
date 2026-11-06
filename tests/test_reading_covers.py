import json
import sys
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from fb2kindle import core, editorial, reading, reading_covers
from fb2kindle.ui.main_window import create_app
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

URL = 'https://s1.livelib.ru/boocover/100/200/abcd/cover.jpg'


def png():
    image = QImage(40, 60, QImage.Format.Format_RGB32)
    image.fill('#885522')
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(buffer.data())


def wait(app, cond):
    end = time.time() + 4
    while time.time() < end:
        app.processEvents()
        if cond():
            return
        time.sleep(.005)
    assert cond()


@pytest.mark.parametrize('url', ['http://s1.livelib.ru/boocover/1/x', 'https://evil.example/boocover/1/x',
    'https://s1.livelib.ru/userpic/1/x', 'https://s1.livelib.ru:99/boocover/1/x',
    'https://name@i.livelib.ru/workpic/1/x', 'https://s1.livelib.ru:invalid/boocover/1/x'])
def test_reject_non_cover_urls(url):
    assert reading_covers.cover_url(url) == ''


def test_ids_bind_cover_to_correct_book():
    html = '''<html><img src="https://s1.livelib.ru/boocover/100/140/abcd/cover.jpg">
    <img src="https://i.livelib.ru/workpic/200/200x305/abcd/cover.jpg">
    <img src="https://evil.example/boocover/300/pic.jpg"></html>'''
    result = editorial.page_covers(html)
    assert set(result) == {'book:100', 'work:200'}
    assert '/100/' in result['book:100']
    assert '/200/' in result['work:200']


def test_invalid_image_is_not_decoded():
    assert reading_covers.decode(b'<html>captcha</html>').isNull()
    assert not reading_covers.decode(png()).isNull()


def test_local_cover_priority_and_disk_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = create_app([])
    path = tmp_path / 'book.fb2'
    path.write_bytes(b'book')
    calls = []
    monkeypatch.setattr(core, 'read_cover', lambda p: calls.append(p) or png())
    monkeypatch.setattr(reading_covers, 'download', lambda u: pytest.fail('Local cover must take priority'))
    loader = reading_covers.CoverLoader(directory=tmp_path / 'cache')
    key, image = loader.request([str(path)], URL)
    assert image is None
    wait(app, lambda: key in loader.images)
    assert len(calls) == 1
    assert loader.images[key].pixelColor(0, 0).name() == '#885522'
    assert (tmp_path / 'cache' / (key + '.png')).is_file()
    loader.shutdown()
    reopened = reading_covers.CoverLoader(directory=tmp_path / 'cache')
    monkeypatch.setattr(core, 'read_cover', lambda p: pytest.fail('Cover must load from disk cache'))
    same_key, _ = reopened.request([str(path)], URL)
    wait(app, lambda: same_key in reopened.images)
    assert same_key == key
    reopened.shutdown()


def test_remote_image_is_cached_and_failure_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    app = create_app([])
    calls = []
    monkeypatch.setattr(reading_covers, 'download', lambda u: calls.append(u) or png())
    loader = reading_covers.CoverLoader(directory=tmp_path)
    key, _ = loader.request([], URL)
    wait(app, lambda: key in loader.images)
    assert calls == [URL]
    loader.request([], URL)
    assert calls == [URL]
    loader.shutdown()
    second = reading_covers.CoverLoader(directory=tmp_path / 'fail')
    calls.clear()
    def denied(url):
        calls.append(url)
        raise urllib.error.HTTPError(url, 429, 'Rate limit', {}, None)
    monkeypatch.setattr(reading_covers, 'download', denied)
    key, _ = second.request([], URL)
    wait(app, lambda: key in second.failed)
    second.request([], URL)
    assert calls == [URL]
    assert second.remote_paused_until > time.time()
    second.shutdown()


def test_previous_history_format_remains_readable(tmp_path):
    store = reading.ReadingStore(tmp_path / 'reading.sqlite3')
    e = reading.Entry('Старая запись', 'Автор', 'read')
    legacy = {'title': e.title, 'author': e.author, 'status': e.status, 'key': e.key}
    with store.connect() as db:
        db.execute('INSERT INTO entries VALUES (?,?)', (e.key, json.dumps(legacy)))
    assert store.get(e.key).cover_url == ''
    assert store.get(e.key).status == 'read'
