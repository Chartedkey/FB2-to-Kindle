"""Главное окно: вкладки «Конвертация» и «Библиотека», строка состояния."""

from __future__ import annotations

import sys
import threading
import webbrowser

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox, QStyleFactory, QTabWidget

from .. import __version__, core
from .common import button, init_bridge, stylesheet, ui_call
from .convert_tab import ConvertTab
from .library_tab import LibraryTab


class MainWindow(QMainWindow):
    def __init__(self, settings: core.Settings | None = None):
        super().__init__()
        self.s = settings or core.Settings.load()
        self.conv_index = core.ConversionIndex()
        self.setWindowTitle(f"{core.APP_NAME} {__version__}")
        icon = core.RES_DIR / "assets" / "icon.ico"
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.setMinimumSize(1000, 680)
        self.resize(1320, 840)
        if self.s.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.s.window_geometry.encode()))

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.convert_tab = ConvertTab(self)
        self.library_tab = LibraryTab(self)
        self.tabs.addTab(self.convert_tab, "Конвертация")
        self.tabs.addTab(self.library_tab, "Библиотека")
        self.tabs.currentChanged.connect(self._on_tab)
        self.setCentralWidget(self.tabs)

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.lbl_msg = QLabel("")
        sb.addWidget(self.lbl_msg, 1)
        self.lbl_fbc = QLabel("")
        sb.addPermanentWidget(self.lbl_fbc)
        self.btn_fbc = button("Обновить fb2cng", slot=self.download_fbc)
        sb.addPermanentWidget(self.btn_fbc)
        self.btn_update = button("Проверить обновления", slot=self.check_updates)
        sb.addPermanentWidget(self.btn_update)
        self._msg_timer = QTimer(self)
        self._msg_timer.setSingleShot(True)
        self._msg_timer.timeout.connect(lambda: self.lbl_msg.setText(""))
        self.refresh_fbc()

        self.tabs.setCurrentIndex(self.s.last_tab if self.s.last_tab in (0, 1) else 0)
        self._on_tab(self.tabs.currentIndex())

    # ------------------------------------------------------------------------

    def _on_tab(self, idx: int):
        self.s.last_tab = idx
        if idx == 1:
            self.library_tab.ensure_loaded()

    def notify(self, text: str, ms: int = 8000):
        self.lbl_msg.setText(text)
        self._msg_timer.start(ms)

    def settings_changed(self):
        self.convert_tab.settings_changed()
        self.library_tab.settings_changed()

    # ------------------------------------------------------------------ fbc --

    def refresh_fbc(self):
        fbc = core.fbc_path()
        if fbc:
            ver = core.fbc_version(fbc)
            self.lbl_fbc.setText(f"fb2cng {ver}".strip())
            self.btn_fbc.setText("Обновить fb2cng")
        else:
            self.lbl_fbc.setText("Конвертер fb2cng не найден")
            self.btn_fbc.setText("Скачать fb2cng")

    def require_fbc(self):
        fbc = core.fbc_path()
        if fbc:
            return fbc
        if QMessageBox.question(self, core.APP_NAME, "Не найден конвертер fb2cng (fbc.exe).\n\nСкачать его сейчас с GitHub?") \
                == QMessageBox.StandardButton.Yes:
            self.download_fbc()
        return None

    def download_fbc(self):
        if not core.IS_WINDOWS:
            QMessageBox.information(self, core.APP_NAME, "Автоматическая загрузка работает только в Windows.")
            return
        if self.convert_tab.busy or self.library_tab.busy:
            QMessageBox.information(self, core.APP_NAME, "Дождитесь окончания конвертации.")
            return
        self.btn_fbc.setEnabled(False)
        self.lbl_fbc.setText("Скачиваю fb2cng…")

        def work():
            try:
                core.download_fbc(progress=lambda f: ui_call(lambda: self.lbl_fbc.setText(f"Скачиваю fb2cng… {int(f * 100)}%")))
                err = ""
            except Exception as e:  # noqa: BLE001
                err = str(e)
            ui_call(lambda: self._fbc_done(err))

        threading.Thread(target=work, daemon=True).start()

    def _fbc_done(self, err: str):
        self.btn_fbc.setEnabled(True)
        self.refresh_fbc()
        if err:
            QMessageBox.warning(self, core.APP_NAME, f"Не удалось скачать fb2cng:\n{err}\n\nМожно скачать вручную: "
                                "github.com/rupor-github/fb2cng/releases — файл fbc-windows-amd64.zip; "
                                "fbc.exe из него положите в папку «fbc» рядом с программой.")
        else:
            self.notify("fb2cng обновлён")

    # ----------------------------------------------------------- Обновления --

    def check_updates(self):
        self.btn_update.setEnabled(False)

        def work():
            try:
                ver, url = core.latest_app_release()
                err = ""
            except Exception as e:  # noqa: BLE001
                ver, url, err = "", "", str(e)
            ui_call(lambda: self._updates_done(ver, url, err))

        threading.Thread(target=work, daemon=True).start()

    def _updates_done(self, ver: str, url: str, err: str):
        self.btn_update.setEnabled(True)
        if err or not ver:
            QMessageBox.warning(self, core.APP_NAME, f"Не удалось проверить обновления.\n{err}")
            return
        if core.version_tuple(ver) > core.version_tuple(__version__):
            if QMessageBox.question(self, core.APP_NAME, f"Есть новая версия {ver} (у вас {__version__}).\n\n"
                                    "Открыть страницу загрузки?") == QMessageBox.StandardButton.Yes:
                webbrowser.open(url)
        else:
            QMessageBox.information(self, core.APP_NAME, f"У вас последняя версия ({__version__}).")

    # ----------------------------------------------------------------------- #

    def closeEvent(self, e):
        if self.convert_tab.busy or self.library_tab.busy:
            if QMessageBox.question(self, core.APP_NAME, "Конвертация ещё идёт. Остановить и выйти?") \
                    != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            if self.convert_tab.converter:
                self.convert_tab.converter.cancel()
        self.library_tab.shutdown()
        self.convert_tab.collect()
        self.s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.s.save()
        e.accept()


def create_app(argv=None) -> QApplication:
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setApplicationName("FB2 Kindle")
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    init_bridge()
    return app


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if core.IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Chartedkey.FB2Kindle")
        except Exception:
            pass
    app = create_app(argv)
    win = MainWindow()
    win.show()
    if "--screenshot" in argv:
        # Проверка сборки: открыть окно, сохранить снимок и выйти
        out = argv[argv.index("--screenshot") + 1]

        def snap():
            win.grab().save(out)
            app.quit()

        QTimer.singleShot(2500, snap)
    return app.exec()
