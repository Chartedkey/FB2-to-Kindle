"""Проверка опубликованной 2.1.0 → текущая сборка → ручной возврат.

Запускается только в Windows CI. Использует отдельные APPDATA/TEMP и копии
программ/книг, не обращается к данным пользователя. Код приложения не меняет.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from fb2kindle import core, reading, __version__  # noqa: E402
from fixtures import make_library  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extract(archive: Path, target: Path) -> Path:
    target.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if not (target / name).resolve().is_relative_to(target.resolve()):
                raise ValueError("Недопустимый путь в архиве")
        z.extractall(target)
    matches = list(target.rglob("FB2 Kindle.exe"))
    assert len(matches) == 1, "В архиве должна быть одна программа"
    return matches[0].parent


def running(exe: Path) -> list[int]:
    path = str(exe).replace("'", "''")
    script = f"@(Get-Process -Name 'FB2 Kindle' -ErrorAction SilentlyContinue | Where-Object {{$_.Path -eq '{path}'}} | ForEach-Object {{$_.Id}}) | ConvertTo-Json -Compress"
    out = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command", script], text=True).strip()
    if not out:
        return []
    value = json.loads(out)
    return value if isinstance(value, list) else [value]


def stop(exe: Path):
    for pid in running(exe):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
    for _ in range(10):
        if not running(exe):
            return
        time.sleep(.5)
    raise AssertionError("Не удалось завершить тестовый процесс")


def screenshot(exe: Path, destination: Path, env: dict):
    assert not destination.exists(), "Снимок не должен остаться от предыдущего запуска"
    result = subprocess.run([str(exe), "--screenshot", str(destination)], env=env, timeout=90, cwd=exe.parent)
    assert result.returncode == 0 and destination.is_file(), f"Не запустилась программа: {exe}"
    assert destination.stat().st_size > 1000


def settings_unchanged(path: Path, expected: dict):
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in expected.items():
        assert data.get(key) == value, f"Изменился параметр {key}"


def read_entry(path: Path, key: str):
    # Только SELECT: сравнение файла истории не должно меняться из-за инициализации хранилища.
    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT payload FROM entries WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-zip", required=True, type=Path)
    parser.add_argument("--new-zip", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Для этой проверки требуется Windows")
    work, report_path = args.work_dir.resolve(), args.report.resolve()
    assert not work.exists(), "Для проверки нужна новая рабочая папка"
    work.mkdir(parents=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"source_version": "2.1.0", "target_version": __version__, "checks": [], "success": False}
    old_app = new_app = installed = None
    def checked(name, **data):
        report["checks"].append({"name": name, "passed": True, **data})
        print("PASS:", name, flush=True)
    try:
        old_app = extract(args.old_zip.resolve(), work / "Старая версия 2.1.0")
        new_app = extract(args.new_zip.resolve(), work / f"Новая версия {__version__}")
        installed = work / "Мои программы" / "FB2 Kindle"
        shutil.copytree(old_app, installed)
        old_exe, new_exe, installed_exe = old_app / "FB2 Kindle.exe", new_app / "FB2 Kindle.exe", installed / "FB2 Kindle.exe"
        assert digest(old_app / "fbc/fbc.exe") == digest(new_app / "fbc/fbc.exe")
        checked("Конвертер одинаков в обеих версиях", sha256=digest(old_app / "fbc/fbc.exe"))
        appdata = work / "Данные приложения"
        temp = work / "Временные файлы"
        appdata.mkdir(); temp.mkdir()
        env = {**os.environ, "APPDATA": str(appdata), "TEMP": str(temp), "TMP": str(temp), "QT_QPA_PLATFORM": "offscreen"}
        # В текущем процессе тоже выбираем только тестовые данные.
        os.environ["APPDATA"] = str(appdata)
        library_root = make_library(work / "Мои книги")
        output = work / "Для Kindle"
        ready = output / "Айзек Азимов" / "Я, робот.kfx"
        ready.parent.mkdir(parents=True)
        ready.write_bytes(b"existing converted file must remain unchanged")
        book_path = library_root / "Азимов Айзек" / "Я, робот.fb2"
        settings = core.Settings(library_root=str(library_root), kindle_root=str(output), output_dir=str(output),
            wiki_enabled=False, layout="per_author", if_exists="skip", footnotes="float", as_ebook=False,
            workers=2, last_tab=1)
        settings.save()
        index = core.ConversionIndex()
        index.record(book_path, ready)
        data = core.data_dir()
        expected = {k: getattr(settings, k) for k in ("library_root", "kindle_root", "output_dir", "layout", "if_exists", "footnotes", "as_ebook", "workers")}
        conversions_before, output_before = digest(data / "conversions.json"), digest(ready)
        source_before = {str(p): digest(p) for p in library_root.rglob('*') if p.is_file()}
        screenshot(installed_exe, report_path.parent / "12-real-2.1.0.png", env)
        assert not (data / "reading.sqlite3").exists()
        settings_unchanged(data / "settings.json", expected)
        checked("Запуск настоящего выпуска 2.1.0 с тестовой библиотекой")
        result = subprocess.run([str(installed_exe), "--apply-update", str(args.new_zip.resolve())], env=env,
                                timeout=90, cwd=installed)
        assert result.returncode == 0
        deadline = time.monotonic() + 120
        update_log = temp / "FB2Kindle-update" / "update-error.log"
        while time.monotonic() < deadline:
            if update_log.exists() and update_log.stat().st_size:
                raise AssertionError(update_log.read_text(encoding="utf-8-sig")[-3000:])
            try:
                replaced = digest(installed_exe) == digest(new_exe)
            except OSError:
                replaced = False
            if replaced and running(installed_exe) and (data / "reading.sqlite3").exists():
                break
            time.sleep(1)
        else:
            raise AssertionError(f"Обновлённая {__version__} не перезапустилась или не создала базу трекера")
        stop(installed_exe)
        screenshot(installed_exe, report_path.parent / f"13-upgraded-{__version__}.png", env)
        assert digest(installed_exe) == digest(new_exe)
        settings_unchanged(data / "settings.json", expected)
        assert digest(data / "conversions.json") == conversions_before
        assert digest(ready) == output_before
        assert list((data / "backups").glob("before-reading-*/settings.json"))
        assert list((data / "backups").glob("before-reading-*/conversions.json"))
        checked(f"Переход 2.1.0 → {__version__} через настоящий механизм обновления, перезапуск и резервные копии")
        history = reading.ReadingStore()
        entry = reading.Entry.from_book(core.read_metadata(book_path))
        entry.status, entry.rating, entry.precision, entry.finished, entry.note = "read", 5, "year", "2024", "История должна пережить возврат"
        history.save(entry)
        settings.last_tab = 2
        settings.save()
        screenshot(installed_exe, report_path.parent / "14-tracker-with-history.png", env)
        history_before = digest(history.path)
        screenshot(old_exe, report_path.parent / "15-rollback-2.1.0.png", env)
        assert digest(old_exe) != digest(installed_exe)
        assert digest(history.path) == history_before, "Старая версия изменила историю трекера"
        assert read_entry(history.path, entry.key)["rating"] == 5
        settings_unchanged(data / "settings.json", expected)
        assert digest(data / "conversions.json") == conversions_before
        checked("Ручной возврат к отдельной 2.1.0: история, настройки и журнал не потеряны")
        screenshot(installed_exe, report_path.parent / f"16-return-to-{__version__}.png", env)
        assert read_entry(history.path, entry.key)["finished"] == "2024"
        assert read_entry(history.path, entry.key)["note"] == entry.note
        checked(f"Повторный запуск {__version__} после старой версии сохранил отметку и дату")
        backup = work / "reading-backup.sqlite3"
        history.backup(backup)
        modified = reading.Entry.from_book(core.read_metadata(book_path))
        modified.status = "wish"
        history.save(modified)
        shutil.copy2(backup, history.path)
        screenshot(installed_exe, report_path.parent / "17-restored-history.png", env)
        assert read_entry(history.path, entry.key)["rating"] == 5
        assert read_entry(history.path, entry.key)["note"] == entry.note
        checked("Восстановление резервной копии истории и запуск exe")
        assert digest(ready) == output_before
        assert {str(p): digest(p) for p in library_root.rglob('*') if p.is_file()} == source_before
        checked("Исходные книги и существовавший Kindle-файл не изменились")
        report["success"] = True
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        for app in (old_app, new_app, installed):
            if app:
                try:
                    stop(app / "FB2 Kindle.exe")
                except Exception:
                    pass
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
