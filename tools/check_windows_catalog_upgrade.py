"""Регрессия 2.2.0: обновление с уже частично загруженным каталогом."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

from check_windows_upgrade import extract, digest, running, stop, screenshot, read_entry
from fb2kindle import core, editorial, reading, __version__


def main():
    parser = argparse.ArgumentParser()
    for name in ('old-zip', 'new-zip', 'work-dir', 'report'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('Требуется Windows')
    work = args.work_dir.resolve()
    assert not work.exists()
    work.mkdir(parents=True)
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {'source_version': '2.2.0', 'target_version': __version__, 'success': False}
    installed_exe = None
    try:
        old_app = extract(args.old_zip.resolve(), work / '2.2.0')
        new_app = extract(args.new_zip.resolve(), work / __version__)
        installed = work / 'Программа с каталогом'
        shutil.copytree(old_app, installed)
        installed_exe = installed / 'FB2 Kindle.exe'
        appdata, temp = work / 'Данные', work / 'Временные файлы'
        appdata.mkdir(); temp.mkdir()
        env = {**os.environ, 'APPDATA': str(appdata), 'TEMP': str(temp), 'TMP': str(temp), 'QT_QPA_PLATFORM': 'offscreen'}
        os.environ['APPDATA'] = str(appdata)
        core.Settings(wiki_enabled=False, last_tab=2).save()
        data = core.data_dir()
        history = reading.ReadingStore()
        entry = reading.Entry('Сохранённая запись', 'Автор', status='read', rating=5)
        history.save(entry)
        legacy = {'id': 'collection:123', 'kind': 'collection', 'title': 'Уже начатая подборка',
                  'url': 'https://www.livelib.ru/selection/123', 'complete': False, 'page': 2,
                  'expected': 100, 'books': [], 'staged': {}}
        for i in range(20):
            legacy['staged'][f'book:{i}'] = {'ref': f'book:{i}', 'title': f'Полученная книга {i}',
                'author': 'Тестовый автор', 'genres': [], 'url': f'https://www.livelib.ru/book/{i}'}
        db = sqlite3.connect(data / 'editorial.sqlite3')
        with db:
            db.execute('CREATE TABLE documents (id TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE state (key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
            db.execute('INSERT INTO documents VALUES (?,?)', (legacy['id'], json.dumps(legacy, ensure_ascii=False)))
            for key, value in [('collection_index', 2), ('article_index', 1), ('enabled', False), ('weekly', False)]:
                db.execute('INSERT INTO state VALUES (?,?)', (key, json.dumps(value)))
        db.close()
        screenshot(installed_exe, report_path.parent / '18-old-partial-catalog.png', env)
        before_history = read_entry(history.path, entry.key)
        result = subprocess.run([str(installed_exe), '--apply-update', str(args.new_zip.resolve())], env=env,
                                cwd=installed, timeout=90)
        assert result.returncode == 0
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                replaced = digest(installed_exe) == digest(new_app / 'FB2 Kindle.exe')
            except OSError:
                replaced = False
            if replaced and running(installed_exe):
                break
            time.sleep(1)
        else:
            raise AssertionError('Обновление с 2.2.0 не перезапустилось')
        stop(installed_exe)
        screenshot(installed_exe, report_path.parent / '19-upgraded-partial-catalog.png', env)
        catalog = editorial.Catalog(data / 'editorial.sqlite3')
        assert catalog.documents() == [legacy], 'Промежуточные страницы были изменены при запуске'
        candidates = catalog.candidates()
        assert len(candidates) == 20
        assert all(c['sources'][0]['partial'] for c in candidates)
        assert read_entry(history.path, entry.key) == before_history, 'Обновление изменило историю'
        catalog.request_refresh()
        assert catalog.documents() == [legacy], 'Проверить сейчас сбросило прогресс'
        report.update(success=True, restored_candidates=len(candidates), preserved_next_page=2,
                      history_preserved=True, check_now_preserves_progress=True)
        print('PASS: 2.2.0 →', __version__, 'сохраняет 20 книг, страницу продолжения и историю; кандидаты сразу доступны', flush=True)
    except Exception as e:
        report['error'] = str(e)
        raise
    finally:
        if installed_exe:
            try:
                stop(installed_exe)
            except Exception as cleanup_error:
                report['cleanup_error'] = str(cleanup_error)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
