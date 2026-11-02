"""Обновление программы изнутри: поиск выпуска на GitHub, скачивание, проверка, замена файлов.

Работающий exe нельзя перезаписать, поэтому замену делает небольшой сценарий PowerShell:
он ждёт, пока программа закроется, сохраняет копию старой версии, копирует новую и запускает её.
Если копирование не удалось — возвращает старую версию.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import REPO, __version__
from .core import FROZEN, HOME_DIR, IS_WINDOWS, USER_AGENT, http_get, version_tuple

EXE_NAME = "FB2 Kindle.exe"


@dataclass
class Release:
    version: str
    notes: str
    url: str            # страница выпуска
    asset_url: str      # архив для Windows
    asset_name: str
    size: int
    sha256: str         # пусто, если GitHub не сообщил


def latest_release() -> Release:
    info = json.loads(http_get(f"https://api.github.com/repos/{REPO}/releases/latest",
                               accept="application/vnd.github+json"))
    asset = next((a for a in info.get("assets", []) if a["name"].lower().endswith("-windows.zip")), None)
    digest = (asset or {}).get("digest") or ""
    return Release(
        version=info.get("tag_name", "").lstrip("v"),
        notes=info.get("body") or "",
        url=info.get("html_url", f"https://github.com/{REPO}/releases"),
        asset_url=(asset or {}).get("browser_download_url", ""),
        asset_name=(asset or {}).get("name", ""),
        size=int((asset or {}).get("size") or 0),
        sha256=digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "",
    )


def is_newer(rel: Release, current: str = __version__) -> bool:
    return version_tuple(rel.version) > version_tuple(current)


def can_self_update() -> tuple[bool, str]:
    """(можно ли обновиться изнутри, причина если нельзя)."""
    if not (IS_WINDOWS and FROZEN):
        return False, "обновление изнутри работает только в собранной программе для Windows"
    try:
        probe = HOME_DIR / ".write-test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False, f"нет прав на запись в папку программы:\n{HOME_DIR}"
    return True, ""


def work_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "FB2Kindle-update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(rel: Release, progress=None, cancelled=lambda: False) -> Path:
    """Скачивает архив выпуска и проверяет SHA-256. Возвращает путь к архиву."""
    if not rel.asset_url:
        raise RuntimeError("в выпуске нет архива для Windows")
    target = work_dir() / rel.asset_name
    tmp = target.with_suffix(".part")
    h = hashlib.sha256()
    req = urllib.request.Request(rel.asset_url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or rel.size or 0)
        done = 0
        while chunk := r.read(256 * 1024):
            if cancelled():
                raise RuntimeError("загрузка отменена")
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    if rel.size and tmp.stat().st_size != rel.size:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("архив скачался не полностью — попробуйте ещё раз")
    if rel.sha256 and h.hexdigest() != rel.sha256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("контрольная сумма архива не совпала — файл повреждён, попробуйте ещё раз")
    os.replace(tmp, target)
    return target


def unpack(zip_path: Path) -> Path:
    """Распаковывает архив; возвращает папку с новой версией (где лежит FB2 Kindle.exe)."""
    stage = work_dir() / "new"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        for m in z.namelist():
            p = (stage / m).resolve()
            if not str(p).startswith(str(stage.resolve())):
                raise RuntimeError("в архиве недопустимые пути")
        z.extractall(stage)
    exe = next(stage.rglob(EXE_NAME), None)
    if exe is None:
        raise RuntimeError(f"в архиве нет {EXE_NAME}")
    return exe.parent


SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$app    = '{app}'
$new    = '{new}'
$backup = '{backup}'
$log    = '{log}'
$exe    = Join-Path $app '{exe}'
try {{ Wait-Process -Id {pid} -Timeout 120 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Milliseconds 700
function Copy-Tree($from, $to) {{
    robocopy $from $to /E /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) {{ throw ("robocopy " + $from + " -> " + $to + " - код " + $LASTEXITCODE) }}
}}
try {{
    if (Test-Path $backup) {{ Remove-Item $backup -Recurse -Force }}
    Copy-Tree $app $backup
    Copy-Tree $new $app
    Start-Process -FilePath $exe -ArgumentList @('--updated', '{version}') -WorkingDirectory $app
    Remove-Item $backup -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $new -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    "$(Get-Date -Format s) $_" | Out-File -FilePath $log -Encoding utf8 -Append
    try {{ Copy-Tree $backup $app }} catch {{ "$(Get-Date -Format s) откат: $_" | Out-File -FilePath $log -Encoding utf8 -Append }}
    Start-Process -FilePath $exe -ArgumentList @('--update-failed', ('"' + $log + '"')) -WorkingDirectory $app
}}
"""


def _ps(s: str) -> str:
    return str(s).replace("'", "''")


def launch_replacer(new_dir: Path, version: str, app_dir: Path | None = None, pid: int | None = None) -> Path:
    """Запускает сценарий замены файлов. Программа после этого должна сразу закрыться."""
    app_dir = app_dir or HOME_DIR
    wd = work_dir()
    log = wd / "update-error.log"
    log.unlink(missing_ok=True)
    script = wd / "replace.ps1"
    script.write_text(SCRIPT.format(app=_ps(app_dir), new=_ps(new_dir), backup=_ps(wd / "backup"), log=_ps(log),
                                    exe=_ps(EXE_NAME), pid=pid or os.getpid(), version=_ps(version)),
                      encoding="utf-8-sig")   # BOM — чтобы PowerShell 5 правильно прочитал русские пути
    flags = 0x08000000 | 0x00000200            # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(script)], creationflags=flags, close_fds=True)
    return script
