"""Обложки трекера: ограниченные загрузки, проверка изображения, отдельный кэш."""
from __future__ import annotations

import hashlib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QImage, QImageReader

from . import core

MAX_BYTES = 2 * 1024 * 1024


def cover_url(url: str) -> str:
    """Только известные CDN LiveLib и каталоги обложек, без произвольных URL."""
    try:
        u = urllib.parse.urlsplit(url)
        allowed = {"s.livelib.ru", "s1.livelib.ru", "s2.livelib.ru", "i.livelib.ru"}
        if u.scheme != "https" or u.hostname not in allowed or u.port or u.username or u.password:
            return ""
        if not u.path.startswith(("/boocover/", "/workpic/")):
            return ""
        return urllib.parse.urlunsplit(("https", u.hostname, u.path, "", ""))
    except ValueError:
        return ""


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not cover_url(newurl):
            raise ValueError("Неожиданный адрес обложки")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str) -> bytes:
    url = cover_url(url)
    if not url:
        raise ValueError("Неизвестный источник обложки")
    # Системные прокси и TLS-проверка сохраняются.
    opener = urllib.request.build_opener(CheckedRedirect())
    with opener.open(urllib.request.Request(url, headers={"User-Agent": core.USER_AGENT}), timeout=12) as response:
        if not cover_url(response.url):
            raise ValueError("Неожиданный адрес обложки")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if not content_type.startswith("image/") or content_type == "image/svg+xml":
            raise ValueError("Ответ не является растровой обложкой")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("Изображение слишком велико")
        return data


def decode(data: bytes) -> QImage:
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    size = reader.size()
    if not size.isValid() or size.width() * size.height() > 16_000_000:
        return QImage()
    image = reader.read()
    if image.isNull():
        return image
    return image.scaled(120, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class CoverLoader(QObject):
    ready = Signal(str, QImage)

    def __init__(self, parent=None, directory: Path | None = None):
        super().__init__(parent)
        self.directory = directory or core.data_dir() / "reading-covers"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="reading-cover")
        self.pending = set()
        self.images = {}
        self.failed = {}
        self.closed = threading.Event()
        self.next_remote = 0.0
        self.remote_paused_until = 0.0
        self.ready.connect(self._received)

    def request(self, paths: list[str], remote: str = ""):
        remote = cover_url(remote)
        local, signature = None, ""
        for raw in paths:
            p = Path(raw)
            try:
                if p.is_file():
                    st = p.stat()
                    local, signature = p, f"{p}|{st.st_size}|{st.st_mtime_ns}"
                    break
            except OSError:
                continue
        if not signature and not remote:
            return "", None
        key = hashlib.sha256((signature + "|" + remote).encode()).hexdigest()
        image = self.images.get(key)
        if image is not None or self.closed.is_set():
            return key, image
        if key not in self.pending and self.failed.get(key, 0) <= time.time():
            self.pending.add(key)
            self.pool.submit(self._work, key, local, remote)
        return key, None

    def _work(self, key, local, remote):
        if self.closed.is_set():
            return
        image = QImage()
        cache = self.directory / (key + ".png")
        try:
            if cache.is_file() and cache.stat().st_size <= MAX_BYTES:
                image = decode(cache.read_bytes())
            if image.isNull() and local:
                data = core.read_cover(local)
                if data:
                    image = decode(data)
            if image.isNull() and remote and time.time() >= self.remote_paused_until:
                if self.closed.wait(max(0, self.next_remote - time.monotonic())):
                    return
                self.next_remote = time.monotonic() + 3
                try:
                    image = decode(download(remote))
                except urllib.error.HTTPError as e:
                    if e.code in (403, 429):
                        self.remote_paused_until = time.time() + 6 * 3600
                    raise
            if not image.isNull():
                temp = cache.with_suffix(".tmp")
                if image.save(str(temp), "PNG"):
                    temp.replace(cache)
        except Exception:
            # Отсутствие сети/обложки не влияет на статус чтения или конвертацию.
            pass
        if not self.closed.is_set():
            try:
                self.ready.emit(key, image)
            except RuntimeError:
                pass  # родительское окно уже уничтожено

    def _received(self, key, image):
        self.pending.discard(key)
        if image.isNull():
            self.failed[key] = max(time.time() + 15 * 60, self.remote_paused_until)
        else:
            if len(self.images) >= 256:
                self.images.pop(next(iter(self.images)))
            self.images[key] = image

    def shutdown(self):
        self.closed.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
