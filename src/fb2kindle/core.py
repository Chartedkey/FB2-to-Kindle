"""Ядро конвертера: чтение FB2, настройки fb2cng, запуск конвертации.

Окно программы (пакет ui) только показывает интерфейс и вызывает функции отсюда.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__

APP_NAME = "FB2 → Kindle"
IS_WINDOWS = os.name == "nt"
FROZEN = getattr(sys, "frozen", False)
# Ресурсы (assets): во временной распаковке exe или в корне репозитория
RES_DIR = Path(getattr(sys, "_MEIPASS", "")) if FROZEN else Path(__file__).resolve().parents[2]
# Папка рядом с программой: рядом с .exe или корень репозитория
HOME_DIR = Path(sys.executable).resolve().parent if FROZEN else RES_DIR
FBC_NAME = "fbc.exe" if IS_WINDOWS else "fbc"
FBC_RELEASE_API = "https://api.github.com/repos/rupor-github/fb2cng/releases/latest"
USER_AGENT = f"FB2toKindle/{__version__} (https://github.com/Chartedkey/FB2-to-Kindle)"

BOOK_SUFFIXES = (".fb2", ".fb2.zip")


def data_dir() -> Path:
    """Папка для настроек и кэша (в Windows — %APPDATA%\\FB2Kindle)."""
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    p = Path(base) / "FB2Kindle"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_json_atomic(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

@dataclass
class Settings:
    # Куда и как сохранять (вкладка «Конвертация»)
    output_dir: str = ""
    layout: str = "one_folder"          # one_folder | per_author | flat
    folder_name: str = ""               # имя папки для режима one_folder
    if_exists: str = "skip"             # skip | replace
    # Библиотека
    library_root: str = ""
    kindle_root: str = ""
    wiki_enabled: bool = True
    author_from_folder: bool = True     # автор библиотеки — из названия папки, а не из FB2
    # Основные
    footnotes: str = "float"            # float | floatRenumbered | default
    hyphenation: bool = True
    chapter_new_page: bool = True
    toc_page: str = "before"            # before | after | none
    generate_cover: bool = True
    title_style: str = "plain"          # plain | series
    author_style: str = "first_last"    # first_last | last_first
    # Дополнительно
    annotation: bool = False
    dropcaps: bool = False
    fix_dashes: bool = False
    paragraph_style: str = "indent"     # indent | spacing
    text_align: str = "justify"         # justify | left
    jpeg_quality: int = 90
    vignettes: bool = False
    as_ebook: bool = False              # False: «Документы» — Kindle сам показывает обложку
    output_format: str = "kfx"          # kfx | azw8
    workers: int = 0                    # 0 = автоматически
    # Окно
    window_geometry: str = ""
    last_tab: int = 0

    @staticmethod
    def path() -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(s, k) and type(v) is type(getattr(s, k)):
                    setattr(s, k, v)
        except Exception:
            pass
        if not s.output_dir:
            s.output_dir = str(default_output_dir())
        if not s.kindle_root:
            s.kindle_root = s.output_dir
        return s

    def save(self) -> None:
        try:
            write_json_atomic(self.path(), asdict(self))
        except Exception:
            pass

    def copy(self, **changes) -> "Settings":
        data = asdict(self)
        data.update(changes)
        return Settings(**data)


def default_output_dir() -> Path:
    home = Path.home()
    for name in ("Desktop", "Рабочий стол", "OneDrive/Desktop", "OneDrive/Рабочий стол"):
        if (home / name).is_dir():
            return home / name / "Для Kindle"
    return home / "Для Kindle"


# --------------------------------------------------------------------------- #
#  Метаданные книги
# --------------------------------------------------------------------------- #

GENRES = {
    "sf": "Научная фантастика", "sf_fantasy": "Фэнтези", "sf_social": "Социальная фантастика",
    "sf_history": "Альтернативная история", "sf_action": "Боевая фантастика", "sf_epic": "Эпическая фантастика",
    "sf_heroic": "Героическая фантастика", "sf_detective": "Детективная фантастика", "sf_cyberpunk": "Киберпанк",
    "sf_space": "Космическая фантастика", "sf_humor": "Юмористическая фантастика", "sf_horror": "Ужасы",
    "sf_postapocalyptic": "Постапокалипсис", "sf_fantasy_city": "Городское фэнтези", "sf_etc": "Фантастика",
    "det_classic": "Классический детектив", "det_police": "Полицейский детектив", "det_action": "Боевик",
    "det_irony": "Иронический детектив", "det_history": "Исторический детектив", "det_espionage": "Шпионский детектив",
    "det_crime": "Криминальный детектив", "det_political": "Политический детектив", "det_maniac": "Триллер",
    "det_hard": "Крутой детектив", "thriller": "Триллер", "detective": "Детектив",
    "prose_classic": "Классическая проза", "prose_history": "Историческая проза", "prose_contemporary": "Современная проза",
    "prose_counter": "Контркультура", "prose_rus_classic": "Русская классика", "prose_su_classics": "Советская классика",
    "prose_military": "Военная проза", "prose": "Проза",
    "love_contemporary": "Любовный роман", "love_history": "Исторический любовный роман", "love": "Любовный роман",
    "adv_western": "Вестерн", "adv_history": "Исторические приключения", "adv_maritime": "Морские приключения",
    "adv_animal": "Приключения о животных", "adventure": "Приключения", "adv_geo": "Путешествия",
    "child_tale": "Сказка", "child_prose": "Детская проза", "child_sf": "Детская фантастика",
    "child_adv": "Детские приключения", "children": "Детская литература",
    "poetry": "Поэзия", "dramaturgy": "Драматургия", "humor": "Юмор", "humor_prose": "Юмористическая проза",
    "sci_history": "История", "sci_philosophy": "Философия", "sci_psychology": "Психология", "science": "Научная литература",
    "nonf_biography": "Биография", "nonf_publicism": "Публицистика", "nonf_criticism": "Критика", "nonfiction": "Документальная литература",
    "antique": "Старинная литература", "religion": "Религия", "reference": "Справочник", "home": "Дом и семья",
    "comp_programming": "Программирование", "computers": "Компьютеры",
}


@dataclass
class Author:
    first: str = ""
    middle: str = ""
    last: str = ""
    nick: str = ""

    def display(self) -> str:
        """«Имя Фамилия» — как на обложке."""
        name = " ".join(p for p in (self.first, self.last) if p)
        return name or self.nick

    def sort_name(self) -> str:
        """«Фамилия Имя» — для списка авторов."""
        name = " ".join(p for p in (self.last, self.first) if p)
        return name or self.nick


@dataclass
class Book:
    path: Path
    title: str = ""
    authors: list[Author] = field(default_factory=list)
    series: str = ""
    series_num: int = 0
    lang: str = ""
    annotation: str = ""
    genres: list[str] = field(default_factory=list)
    year: str = ""
    has_cover: bool = False
    size: int = 0
    mtime: float = 0.0
    error: str = ""

    @property
    def author_display(self) -> str:
        names = [a.display() for a in self.authors if a.display()]
        if not names:
            return ""
        return names[0] if len(names) == 1 else f"{names[0]} и др."

    @property
    def first_author(self) -> str:
        for a in self.authors:
            if a.display():
                return a.display()
        return ""

    @property
    def first_author_obj(self) -> Author | None:
        for a in self.authors:
            if a.display():
                return a
        return None

    @property
    def display_title(self) -> str:
        return self.title or stem_of(self.path)

    @property
    def genre_text(self) -> str:
        names = []
        for g in self.genres:
            n = GENRES.get(g, "")
            if n and n not in names:
                names.append(n)
        return ", ".join(names[:2])

    @property
    def series_text(self) -> str:
        if not self.series:
            return ""
        return f"{self.series}, кн. {self.series_num}" if self.series_num else self.series

    def to_cache(self) -> dict:
        d = asdict(self)
        d["path"] = str(self.path)
        return d

    @classmethod
    def from_cache(cls, d: dict) -> "Book":
        d = dict(d)
        d["path"] = Path(d["path"])
        d["authors"] = [Author(**a) for a in d.get("authors", [])]
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})


def stem_of(path: Path) -> str:
    name = path.name
    for suf in BOOK_SUFFIXES[::-1]:
        if name.lower().endswith(suf):
            return name[: -len(suf)]
    return path.stem


_ENC_RE = re.compile(rb'<\?xml[^>]*encoding\s*=\s*["\']([A-Za-z0-9_\-]+)["\']', re.I)


def _open_fb2(path: Path):
    """Открывает FB2 как поток байтов (из .fb2 или из .fb2.zip)."""
    if path.name.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        names = [n for n in z.namelist() if n.lower().endswith(".fb2")]
        if not names:
            z.close()
            raise ValueError("в архиве нет файла .fb2")
        return z, z.open(names[0])
    return None, open(path, "rb")


def read_fb2_bytes(path: Path, limit: int | None = 256 * 1024) -> bytes:
    z, f = _open_fb2(path)
    try:
        return f.read(limit) if limit else f.read()
    finally:
        f.close()
        if z:
            z.close()


def read_fb2_head(path: Path, limit: int = 256 * 1024) -> bytes:
    return read_fb2_bytes(path, limit)


def decode_fb2(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    m = _ENC_RE.search(raw[:200])
    enc = m.group(1).decode("ascii").lower() if m else "utf-8"
    try:
        return raw.decode(enc, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _strip_ns(xml: str) -> str:
    xml = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", xml)
    xml = re.sub(r"<(/?)\w+:", r"<\1", xml)
    xml = re.sub(r'\s\w+:(\w+=)', r" \1", xml)
    return xml


def _text(el) -> str:
    if el is None:
        return ""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _paragraphs(el) -> str:
    if el is None:
        return ""
    parts = []
    for p in el.iter():
        if p.tag in ("p", "v", "subtitle"):
            t = _text(p)
            if t:
                parts.append(t)
    return "\n".join(parts) if parts else _text(el)


def _year(s: str) -> str:
    m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", s or "")
    return m.group(1) if m else ""


def read_metadata(path: Path) -> Book:
    book = Book(path=path)
    try:
        st = path.stat()
        book.size, book.mtime = st.st_size, st.st_mtime
    except OSError:
        pass
    text = ""
    try:
        text = decode_fb2(read_fb2_head(path))
        m = re.search(r"<(\w+:)?title-info\b.*?</(\w+:)?title-info>", text, re.S)
        if not m:
            raise ValueError("нет описания книги (title-info)")
        ti = ET.fromstring(_strip_ns(m.group(0)))
        book.title = _text(ti.find("book-title"))
        for a in ti.findall("author"):
            book.authors.append(Author(
                first=_text(a.find("first-name")),
                middle=_text(a.find("middle-name")),
                last=_text(a.find("last-name")),
                nick=_text(a.find("nickname")),
            ))
        book.lang = _text(ti.find("lang"))
        book.genres = [_text(g) for g in ti.findall("genre") if _text(g)]
        book.annotation = _paragraphs(ti.find("annotation"))[:4000]
        date = ti.find("date")
        book.year = _year((date.get("value") or "") + " " + _text(date)) if date is not None else ""
        if not book.year:
            pm = re.search(r"<(\w+:)?publish-info\b.*?<(\w+:)?year>(.*?)</", text, re.S)
            if pm:
                book.year = _year(pm.group(3))
        book.has_cover = ti.find("coverpage") is not None
        seq = ti.find("sequence")
        if seq is not None:
            book.series = (seq.get("name") or "").strip()
            num = re.match(r"\d+", (seq.get("number") or "").strip())
            book.series_num = int(num.group(0)) if num else 0
    except zipfile.BadZipFile:
        book.error = "повреждённый архив"
    except ET.ParseError:
        # Описание с ошибками разметки — берём хотя бы название и автора регулярными выражениями
        t = re.search(r"<book-title>(.*?)</book-title>", text, re.S)
        if t:
            book.title = re.sub(r"<[^>]+>|\s+", " ", t.group(1)).strip()
        fn = re.search(r"<first-name>(.*?)</first-name>", text, re.S)
        ln = re.search(r"<last-name>(.*?)</last-name>", text, re.S)
        if fn or ln:
            book.authors.append(Author(first=(fn.group(1).strip() if fn else ""), last=(ln.group(1).strip() if ln else "")))
    except Exception as e:  # noqa: BLE001
        book.error = str(e)
    return book


def read_cover(path: Path) -> bytes | None:
    """Картинка обложки из FB2 (байты JPEG/PNG) или None."""
    try:
        raw = read_fb2_bytes(path, limit=None)
    except Exception:
        return None
    text = decode_fb2(raw)
    m = re.search(r"<(\w+:)?coverpage>(.*?)</(\w+:)?coverpage>", text, re.S)
    if not m:
        return None
    href = re.search(r'href\s*=\s*"#?([^"]+)"', m.group(2))
    if not href:
        return None
    cid = re.escape(href.group(1))
    b = re.search(r'<(\w+:)?binary\b[^>]*\bid\s*=\s*"' + cid + r'"[^>]*>(.*?)</', text, re.S)
    if not b:
        return None
    try:
        return base64.b64decode(re.sub(r"\s+", "", b.group(2)), validate=False)
    except (binascii.Error, ValueError):
        return None


def find_books(paths: list[str]) -> list[Path]:
    """Файлы и папки → список книг (папки обходятся с подпапками)."""
    found: list[Path] = []
    seen: set[str] = set()

    def add(p: Path):
        key = os.path.normcase(str(p.resolve()))
        if key not in seen:
            seen.add(key)
            found.append(p)

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*"), key=lambda x: str(x).lower()):
                if f.is_file() and f.name.lower().endswith(BOOK_SUFFIXES):
                    add(f)
        elif p.is_file() and p.name.lower().endswith(BOOK_SUFFIXES):
            add(p)
    return found


def folder_author(folder: str) -> tuple[str, str, str]:
    """Название папки «Фамилия Имя» → («Имя Фамилия», «Фамилия Имя», «Фамилия»).

    Первое слово считается фамилией; фамилию из нескольких слов отделяют запятой: «Ле Гуин, Урсула».
    """
    folder = re.sub(r"\s+", " ", folder).strip()
    if "," in folder:
        last, rest = (x.strip() for x in folder.split(",", 1))
    else:
        parts = folder.split(" ", 1)
        last, rest = parts[0], (parts[1] if len(parts) > 1 else "")
    if not rest:
        return last, last, last
    return f"{rest} {last}", f"{last} {rest}", last


def most_common_author(books: list[Book]) -> str:
    c = Counter(b.first_author for b in books if b.first_author)
    return c.most_common(1)[0][0] if c else ""


# --------------------------------------------------------------------------- #
#  Имена файлов и папок
# --------------------------------------------------------------------------- #

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str, fallback: str = "Без названия", max_len: int = 150) -> str:
    """Имя, допустимое в Windows: без : ? * " < > | / \\ и точек в конце."""
    name = name.replace("­", "")
    name = re.sub(r"\s*:\s*", " - ", name)
    name = re.sub(r"\s*[\\/]\s*", " - ", name)
    name = re.sub(r'"([^"]*)"', r"«\1»", name).replace('"', "").replace("?", "").replace("*", "")
    name = re.sub(r"[<>|\x00-\x1f]", "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ").strip()
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    if not name:
        name = fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    return name


def plan_outputs(books: list[Book], s: Settings) -> list[Path]:
    """Путь итогового файла для каждой книги; одинаковые названия получают (2), (3)…"""
    ext = "." + s.output_format
    root = Path(s.output_dir)
    used: set[str] = set()
    result = []
    for b in books:
        if s.layout == "one_folder":
            folder = root / safe_name(s.folder_name or b.first_author, "Без автора")
        elif s.layout == "per_author":
            folder = root / safe_name(b.first_author, "Без автора")
        else:
            folder = root
        base = safe_name(b.display_title)
        cand = folder / (base + ext)
        n = 2
        while os.path.normcase(str(cand)) in used:
            cand = folder / f"{base} ({n}){ext}"
            n += 1
        used.add(os.path.normcase(str(cand)))
        result.append(cand)
    return result


# --------------------------------------------------------------------------- #
#  Конфигурация fb2cng
# --------------------------------------------------------------------------- #

TITLE_PLAIN = "{{ .Title -}}"
TITLE_SERIES = (
    '{{- $p := "" -}}'
    '{{- if gt (len .Series) 0 -}}{{- with first .Series -}}'
    '{{- if gt .Number 0 -}}{{- $p = printf "(%s %02d) " .Name .Number -}}'
    '{{- else -}}{{- $p = printf "(%s) " .Name -}}{{- end -}}'
    '{{- end -}}{{- end -}}'
    '{{ $p }}{{ .Title -}}'
)
CREATOR_FIRST_LAST = (
    '{{- $n := list -}}'
    '{{- if .FirstName }}{{ $n = append $n .FirstName }}{{ end -}}'
    '{{- if .LastName }}{{ $n = append $n .LastName }}{{ end -}}'
    '{{- if gt (len $n) 0 }}{{ join " " $n }}{{ else }}{{ .Nickname }}{{ end -}}'
)
CREATOR_LAST_FIRST = (
    '{{- $n := list -}}'
    '{{- if .LastName }}{{ $n = append $n .LastName }}{{ end -}}'
    '{{- if .FirstName }}{{ $n = append $n .FirstName }}{{ end -}}'
    '{{- if gt (len $n) 0 }}{{ join " " $n }}{{ else }}{{ .Nickname }}{{ end -}}'
)


def _q(s: str) -> str:
    """Строка YAML в одинарных кавычках."""
    return "'" + s.replace("'", "''") + "'"


def build_stylesheet(s: Settings, work: Path) -> Path | None:
    """Свой стиль нужен только если что-то отличается от стандартного."""
    extra = []
    if not s.chapter_new_page:
        extra.append(".section-title-h2 { page-break-before: auto; }")
    if s.paragraph_style == "spacing":
        extra.append("p { text-indent: 0; margin: 0 0 0.6em 0; }")
    if s.text_align == "left":
        extra.append("p { text-align: left; }")
    if not extra:
        return None
    base = (RES_DIR / "assets" / "fbc-default.css").read_text(encoding="utf-8")
    css = work / "style.css"
    css.write_text(base + "\n\n/* Настройки FB2 → Kindle */\n" + "\n".join(extra) + "\n", encoding="utf-8")
    return css


def build_config(s: Settings, work: Path) -> Path:
    lines = [
        "version: 1",
        "document:",
        f"  insert_soft_hyphen: {str(s.hyphenation).lower()}",
        "  metainformation:",
        f"    title_template: {_q(TITLE_SERIES if s.title_style == 'series' else TITLE_PLAIN)}",
        f"    creator_name_template: {_q(CREATOR_LAST_FIRST if s.author_style == 'last_first' else CREATOR_FIRST_LAST)}",
        "  images:",
        f"    jpeg_quality_level: {max(40, min(100, int(s.jpeg_quality)))}",
        "    cover:",
        f"      generate: {str(s.generate_cover).lower()}",
        "      resize: keepAR",
        "  footnotes:",
        f"    mode: {s.footnotes}",
        "  annotation:",
        f"    enable: {str(s.annotation).lower()}",
        "    title: Аннотация",
        "  toc_page:",
        f"    placement: {s.toc_page}",
        "  dropcaps:",
        f"    enable: {str(s.dropcaps).lower()}",
        "  text_transformations:",
        "    speech:",
        f"      enable: {str(s.fix_dashes).lower()}",
        "    dashes:",
        f"      enable: {str(s.fix_dashes).lower()}",
        "    dialogue:",
        f"      enable: {str(s.fix_dashes).lower()}",
    ]
    if s.vignettes:
        lines += [
            "  vignettes:",
            "    book:",
            "      title_top: builtin",
            "      title_bottom: builtin",
            "    chapter:",
            "      title_bottom: builtin",
            "      end: builtin",
        ]
    css = build_stylesheet(s, work)
    if css:
        lines.insert(3, f"  stylesheet_path: {_q(str(css))}")
    cfg = work / "config.yaml"
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return cfg


# --------------------------------------------------------------------------- #
#  fb2cng (fbc)
# --------------------------------------------------------------------------- #

def fbc_path() -> Path | None:
    for d in (HOME_DIR / "fbc", RES_DIR / "fbc", HOME_DIR):
        p = d / FBC_NAME
        if p.is_file():
            return p
    found = shutil.which(FBC_NAME)
    return Path(found) if found else None


def popen_kwargs() -> dict:
    if IS_WINDOWS:
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW — без чёрных окон
    return {}


def fbc_version(fbc: Path) -> str:
    try:
        r = subprocess.run([str(fbc), "--version"], capture_output=True, timeout=20, **popen_kwargs())
        out = (r.stdout or r.stderr).decode("utf-8", "replace").strip()
        m = re.search(r"version\s+(.+?)\s*\(go[\d.]+\)", out) or re.search(r"\d+\.\d+[\w.\-+]*", out)
        return (m.group(1) if m.lastindex else m.group(0)) if m else out[:60]
    except Exception:
        return ""


def http_get(url: str, timeout: int = 30, accept: str = "") -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
        return r.read()


def download_fbc(progress=None) -> Path:
    """Скачивает последнюю версию fb2cng для Windows с GitHub в папку fbc рядом с программой."""
    arch = "arm64" if "ARM" in os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() else "amd64"
    info = json.loads(http_get(FBC_RELEASE_API, accept="application/vnd.github+json"))
    asset = next((a for a in info.get("assets", []) if a["name"] == f"fbc-windows-{arch}.zip"), None)
    if not asset:
        raise RuntimeError("в последнем выпуске fb2cng нет сборки для Windows")
    req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": USER_AGENT})
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
        while chunk := r.read(64 * 1024):
            buf.write(chunk)
            if progress and total:
                progress(buf.tell() / total)
    target = HOME_DIR / "fbc"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(buf) as z:
        member = next((n for n in z.namelist() if n.lower().endswith("fbc.exe")), None)
        if not member:
            raise RuntimeError("в архиве нет fbc.exe")
        tmp = target / "fbc.exe.new"
        tmp.write_bytes(z.read(member))
        os.replace(tmp, target / "fbc.exe")
    return target / "fbc.exe"


def latest_app_release() -> tuple[str, str]:
    """(версия, ссылка) последнего выпуска программы на GitHub."""
    from . import REPO
    info = json.loads(http_get(f"https://api.github.com/repos/{REPO}/releases/latest", accept="application/vnd.github+json"))
    return info.get("tag_name", "").lstrip("v"), info.get("html_url", f"https://github.com/{REPO}/releases")


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


# --------------------------------------------------------------------------- #
#  Журнал конвертаций: из какого FB2 сделан какой файл
# --------------------------------------------------------------------------- #

class ConversionIndex:
    """Хранит записи «FB2 → готовый файл», чтобы библиотека находила книги и после переименования."""

    _lock = threading.Lock()

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "conversions.json")
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            self.data = {}

    @staticmethod
    def key(src: Path) -> str:
        return os.path.normcase(str(Path(src).resolve()))

    def get(self, src: Path) -> dict | None:
        return self.data.get(self.key(src))

    def record(self, src: Path, output: Path) -> None:
        try:
            st = Path(src).stat()
            entry = {"output": str(output), "src_mtime": st.st_mtime, "src_size": st.st_size}
        except OSError:
            return
        with self._lock:
            self.data[self.key(src)] = entry
            try:
                write_json_atomic(self.path, self.data)
            except OSError:
                pass


# --------------------------------------------------------------------------- #
#  Конвертация
# --------------------------------------------------------------------------- #

@dataclass
class Job:
    book: Book
    output: Path
    status: str = "wait"        # wait | run | done | skip | error | cancel
    message: str = ""
    details: str = ""
    tag: object = None          # для интерфейса (строка таблицы, книга библиотеки…)


_KNOWN_ERRORS = [
    (r"invalid XML|XML syntax|etree", "файл повреждён (ошибка в разметке FB2)"),
    (r"no such file|cannot find the file|not exist", "файл не найден"),
    (r"permission denied|access is denied", "нет доступа к файлу"),
    (r"zip: not a valid zip|zip: |archive", "повреждённый архив"),
    (r"no fb2 files|nothing to (process|convert)", "в архиве нет книги FB2"),
    (r"not enough space|no space left|disk full", "не хватает места на диске"),
]


def short_error(out: str) -> str:
    """Понятная причина ошибки из журнала fbc."""
    errors = re.findall(r'"error":\s*"((?:[^"\\]|\\.)*)"', out)
    text = errors[-1] if errors else ""
    if not text:
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        text = lines[-1] if lines else ""
    for pattern, msg in _KNOWN_ERRORS:
        if re.search(pattern, text, re.I):
            return msg
    reason = text.replace('\\"', '"').split(": ")[-1].strip()
    return (reason or "fb2cng завершился с ошибкой")[:160] + " (дважды щёлкните — подробности)"


def _only_cleanup_failed(log: str) -> bool:
    """fbc сделал книгу, но споткнулся при уборке своих служебных файлов."""
    return "Conversion completed" in log and bool(re.search(r"unable to remove .*log", log))


class Converter:
    """Конвертирует список книг; сообщает о каждом шаге через on_update(job), в конце — on_finish()."""

    def __init__(self, jobs: list[Job], settings: Settings, fbc: Path, on_update, on_finish,
                 index: ConversionIndex | None = None):
        self.jobs = jobs
        self.s = settings
        self.fbc = fbc
        self.on_update = on_update
        self.on_finish = on_finish
        self.index = index
        self._cancel = threading.Event()
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()

    def start(self) -> None:
        threading.Thread(target=self.run, daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            for p in list(self._procs):
                try:
                    p.kill()
                except Exception:
                    pass

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> None:
        work = Path(tempfile.mkdtemp(prefix="fb2kindle-"))
        try:
            cfg = build_config(self.s, work)
            workers = self.s.workers or max(1, min(4, (os.cpu_count() or 2) // 2))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(lambda ij: self._one(ij[1], cfg, work / f"job{ij[0]}"), enumerate(self.jobs)))
        finally:
            shutil.rmtree(work, ignore_errors=True)
            self.on_finish()

    def _set(self, job: Job, status: str, message: str = "", details: str = "") -> None:
        job.status, job.message, job.details = status, message, details
        self.on_update(job)

    def _one(self, job: Job, cfg: Path, jobdir: Path) -> None:
        if self._cancel.is_set():
            self._set(job, "cancel", "Отменено")
            return
        out = job.output
        if out.exists() and self.s.if_exists == "skip":
            self._set(job, "skip", "Уже есть, пропущено")
            if self.index:
                self.index.record(job.book.path, out)
            return
        self._set(job, "run", "Конвертируется…")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            jobdir.mkdir(parents=True, exist_ok=True)   # у каждой книги своя рабочая папка
        except OSError as e:
            self._set(job, "error", f"Не удалось создать папку: {e.strerror}")
            return
        # fbc пишет во временный файл, потом переносим — чтобы на диске не оставались недоделанные книги
        tmp_out = jobdir / f"book.{self.s.output_format}"
        cmd = [str(self.fbc), "-c", str(cfg), "convert", "--to", self.s.output_format, "--overwrite"]
        if self.s.as_ebook:
            cmd.append("--ebook")
        cmd += ["--output-file", str(tmp_out), str(job.book.path)]
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(jobdir), **popen_kwargs())
        except OSError as e:
            self._set(job, "error", f"Не удалось запустить fb2cng: {e.strerror}")
            return
        with self._lock:
            self._procs.add(p)
        try:
            raw, _ = p.communicate()
        finally:
            with self._lock:
                self._procs.discard(p)
        log = raw.decode("utf-8", "replace")
        if self._cancel.is_set():
            self._set(job, "cancel", "Отменено")
            return
        ok = tmp_out.is_file() and (p.returncode == 0 or _only_cleanup_failed(log))
        if not ok:
            self._set(job, "error", "Ошибка: " + short_error(log), log)
            return
        try:
            shutil.move(str(tmp_out), str(out))
        except OSError as e:
            self._set(job, "error", f"Не удалось сохранить файл: {e.strerror}", log)
            return
        if self.index:
            self.index.record(job.book.path, out)
        size = out.stat().st_size / 1024 / 1024
        self._set(job, "done", f"Готово, {size:.1f} МБ", log)
