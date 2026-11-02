"""Тестовые книги FB2: разные кодировки, архив, битый файл, обложка, сноски."""

from __future__ import annotations

import base64
import io
import zipfile
from pathlib import Path


def _cover_b64() -> str:
    try:
        from PIL import Image
        im = Image.new("RGB", (300, 450), (40, 60, 90))
        b = io.BytesIO()
        im.save(b, "JPEG")
        return base64.b64encode(b.getvalue()).decode()
    except ImportError:  # крошечный JPEG 1×1
        return ("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////"
                "////////////////////////////wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAA"
                "AAAAAAD/2gAIAQEAAD8AKp//2Q==")


def fb2(title, first, last, series=None, num=None, notes=True, cover=True, chapters=2, year="2004",
        annotation=True, genre="sf_fantasy", encoding="windows-1251") -> bytes:
    seq = f'<sequence name="{series}" number="{num}"/>' if series else ""
    cp = '<coverpage><image l:href="#cover.jpg"/></coverpage>' if cover else ""
    ann = f"<annotation><p>Аннотация книги «{title}».</p><p>Второй абзац.</p></annotation>" if annotation else ""
    secs = ""
    for c in range(1, chapters + 1):
        note = f'<a l:href="#n{c}" type="note">[{c}]</a>' if notes else ""
        paras = "".join(f"<p>— Абзац {i} главы {c}, — сказал он. Электрификация и достопримечательность.{note if i == 1 else ''}</p>"
                        for i in range(1, 8))
        secs += f"<section><title><p>Глава {c}</p></title>{paras}</section>"
    nb = "".join(f'<section id="n{c}"><title><p>{c}</p></title><p>Примечание {c}.</p></section>'
                 for c in range(1, chapters + 1)) if notes else ""
    notes_body = f'<body name="notes"><title><p>Примечания</p></title>{nb}</body>' if notes else ""
    binary = f'<binary id="cover.jpg" content-type="image/jpeg">{_cover_b64()}</binary>' if cover else ""
    xml = f'''<?xml version="1.0" encoding="{encoding}"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info><genre>{genre}</genre><author><first-name>{first}</first-name><last-name>{last}</last-name></author><book-title>{title}</book-title>{ann}<date>{year}</date><lang>ru</lang>{seq}{cp}</title-info>
<document-info><author><nickname>test</nickname></author><date>2026</date><id>id-{abs(hash(title))}</id><version>1.0</version></document-info></description>
<body><title><p>{first} {last}</p><p>{title}</p></title>{secs}</body>{notes_body}{binary}</FictionBook>'''
    return xml.encode(encoding)


def make_library(root: Path) -> Path:
    """Папка «Книги» с несколькими авторами, как у пользователя."""
    m = root / "Мьевилль Чайна"
    m.mkdir(parents=True)
    (m / "Кракен.fb2").write_bytes(fb2("Кракен", "Чайна", "Мьевилль", year="2010", genre="sf_fantasy_city"))
    (m / "Шрам.fb2").write_bytes(fb2("Шрам", "Чайна", "Мьевилль", series="Бас-Лаг", num=2, year="2002"))
    (m / "Нью-Кробюзон..fb2").write_bytes(fb2("Нью-Кробюзон.", "Чайна", "Мьевилль", notes=False, cover=False,
                                             annotation=False, chapters=1))
    with zipfile.ZipFile(m / "Город и город.fb2.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("Город и город.fb2", fb2("Город и город: роман", "Чайна", "Мьевилль", encoding="utf-8"))
    (m / "Битая книга.fb2").write_bytes(b'<?xml version="1.0"?><FictionBook><description><title-info><book-title>Bad')
    a = root / "Азимов Айзек"
    a.mkdir()
    (a / "Я, робот.fb2").write_bytes(fb2("Я, робот", "Айзек", "Азимов", year="1950", genre="sf"))
    (a / "Конец Вечности.fb2").write_bytes(fb2("Конец Вечности", "Айзек", "Азимов", year="1955", genre="sf"))
    return root
