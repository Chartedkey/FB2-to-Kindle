# -*- mode: python ; coding: utf-8 -*-
# Сборка: python -m PyInstaller --noconfirm FB2Kindle.spec  →  dist/FB2 Kindle/FB2 Kindle.exe

EXCLUDES = [
    "tkinter", "unittest", "pydoc", "PySide6.QtNetwork", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtXml", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtDBus", "PySide6.QtConcurrent", "PySide6.QtHelp", "PySide6.QtDesigner",
    "PySide6.QtMultimedia", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtCharts",
    "PySide6.Qt3DCore", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSerialPort",
]

a = Analysis(
    ["run.pyw"],
    pathex=["src"],
    datas=[("assets", "assets")],
    excludes=EXCLUDES,
    noarchive=False,
)
# Лишние переводы Qt (оставляем русский и английский) и программные OpenGL/d3d — экономят ~30 МБ
def keep(name):
    n = name.replace("\\", "/").lower()
    if "/translations/" in n and n.endswith(".qm"):
        return any(x in n for x in ("_ru.qm", "_en.qm"))
    if n.endswith(("opengl32sw.dll", "d3dcompiler_47.dll")):
        return False
    return True

a.datas = [d for d in a.datas if keep(d[0])]
a.binaries = [b for b in a.binaries if keep(b[0])]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FB2 Kindle",
    icon="assets/icon.ico",
    console=False,
    upx=False,
    version="tools/version_info.txt",
)
coll = COLLECT(exe, a.binaries, a.datas, name="FB2 Kindle", upx=False)
