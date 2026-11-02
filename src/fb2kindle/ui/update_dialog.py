"""Окно «Есть новая версия»: что нового, скачивание с прогрессом, установка."""

from __future__ import annotations

import threading
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QTextBrowser, QVBoxLayout

from .. import __version__, updater
from .common import bold, button, muted, ui_call


class UpdateDialog(QDialog):
    def __init__(self, rel: updater.Release, main):
        super().__init__(main)
        self.rel, self.main = rel, main
        self._cancel = False
        self.downloading = False
        self.setWindowTitle("Обновление программы")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 16)
        lay.setSpacing(10)
        lay.addWidget(bold(f"Доступна версия {rel.version}", 13))
        lay.addWidget(muted(f"У вас установлена версия {__version__}."))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(rel.notes or "Описание изменений не указано.")
        notes.setMinimumHeight(200)
        notes.setStyleSheet("QTextBrowser { background:#FFFFFF; border:1px solid #E5E5E5; border-radius:8px; padding:8px; }")
        lay.addWidget(notes, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        ok, reason = updater.can_self_update()
        self.can_install = ok and bool(rel.asset_url)
        btns = QHBoxLayout()
        btns.addWidget(button("Страница выпуска", slot=lambda: webbrowser.open(rel.url)))
        btns.addStretch(1)
        self.btn_later = button("Позже", slot=self.reject)
        btns.addWidget(self.btn_later)
        self.btn_install = button("Обновить", primary=True, slot=self.install)
        self.btn_install.setEnabled(self.can_install)
        btns.addWidget(self.btn_install)
        lay.addLayout(btns)
        if not self.can_install:
            self.status.setText(f"Обновиться изнутри нельзя: {reason or 'в выпуске нет архива'}. "
                                "Скачайте новую версию со страницы выпуска.")
        else:
            size = f"{rel.size / 1024 / 1024:.0f} МБ" if rel.size else ""
            self.status.setText(f"Программа скачает новую версию{' (' + size + ')' if size else ''}, "
                                "закроется, обновится и запустится снова. Настройки, библиотека и книги сохранятся.")
            self.status.setProperty("muted", True)

    def install(self):
        if self.main.convert_tab.busy or self.main.library_tab.busy:
            self.status.setText("Сначала дождитесь окончания конвертации.")
            return
        self.downloading = True
        self.btn_install.setEnabled(False)
        self.btn_later.setText("Отмена")
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status.setText("Скачиваю…")

        def work():
            try:
                zip_path = updater.download(
                    self.rel,
                    progress=lambda f: ui_call(lambda: self._progress(f)),
                    cancelled=lambda: self._cancel)
                ui_call(lambda: self.status.setText("Проверяю и распаковываю…"))
                new_dir = updater.unpack(zip_path)
                ui_call(lambda: self._ready(new_dir))
            except Exception as e:  # noqa: BLE001
                err = str(e)
                ui_call(lambda: self._failed(err))

        threading.Thread(target=work, daemon=True).start()

    def _progress(self, f: float):
        self.progress.setValue(int(f * 100))
        done = f * self.rel.size / 1024 / 1024 if self.rel.size else 0
        self.status.setText(f"Скачиваю… {done:.0f} из {self.rel.size / 1024 / 1024:.0f} МБ" if self.rel.size
                            else f"Скачиваю… {int(f * 100)}%")

    def _failed(self, err: str):
        self.downloading = False
        self.progress.setVisible(False)
        self.btn_later.setText("Закрыть")
        self.btn_install.setEnabled(not self._cancel)
        self.status.setText("" if self._cancel else f"Не удалось обновить: {err}")
        if self._cancel:
            self.reject()

    def _ready(self, new_dir):
        self.status.setText("Устанавливаю — программа перезапустится…")
        try:
            updater.launch_replacer(new_dir, self.rel.version)
        except Exception as e:  # noqa: BLE001
            self._failed(str(e))
            return
        self.accept()
        self.main.quit_for_update()

    def reject(self):
        if self.downloading:
            self._cancel = True
            self.status.setText("Отменяю…")
            return
        super().reject()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.downloading:
            self.reject()
            return
        super().keyPressEvent(e)
