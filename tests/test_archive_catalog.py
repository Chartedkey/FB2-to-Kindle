import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fb2kindle import archive_catalog as catalog, core, library
from fixtures import fb2


def archive_fixture(root):
    archive = root / "d.fb2-001-003.zip"
    index = root / "flibusta_fb2_local.inpx"
    fields = "TITLE;AUTHOR;FILE;EXT;LANG;LIBID;DEL"
    records = [
        ("Два перевода", "Кинг,Стивен,:", "1", "fb2", "ru", "1", "0"),
        ("Два перевода", "Кинг,Стивен,:", "2", "fb2", "ru", "2", "0"),
        ("Другой рассказ", "Другой,Автор,:", "3", "fb2", "ru", "3", "0"),
        ("Удалено", "Кинг,Стивен,:", "4", "fb2", "ru", "4", "1"),
    ]
    variants = {}
    for number, last in ((1, "Иванов"), (2, "Петров")):
        text = fb2("Два перевода", "Стивен", "Кинг", cover=False, encoding="utf-8").decode()
        text = text.replace("</title-info>", f"<translator><first-name>Иван</first-name>"
                            f"<last-name>{last}</last-name></translator></title-info>"
                            f"<publish-info><publisher>Издательство {number}</publisher>"
                            f"<year>200{number}</year></publish-info>")
        variants[number] = text.encode()
    with ZipFile(archive, "w") as bundle:
        for number, data in variants.items():
            bundle.writestr(f"{number}.fb2", data)
    with ZipFile(index, "w") as bundle:
        bundle.writestr("structure.info", fields)
        bundle.writestr("d.fb2-001-003.inp", "\n".join("\x04".join(record) + "\x04" for record in records))
    return index, archive, variants


def test_index_import_and_matching_keep_editions_separate(tmp_path):
    source, archive, original = archive_fixture(tmp_path)
    index = catalog.Catalog(tmp_path / "index.sqlite")
    index.build(source)
    index.build(source)
    author, name = index.authors("Два перевода")[0]
    assert name == "Кинг Стивен"
    books = index.books(author)
    assert [b.member for b in books] == ["1.fb2", "2.fb2"]
    assert index.authors("Стивен Кинг") == [(author, name)]
    archives = catalog.archive_paths(tmp_path)
    with ZipFile(archive) as z:
        details = catalog.metadata(z, catalog.member_info(z, books[1]))
    assert details["Переводчик"] == "Иван Петров"
    assert details["Издательство"] == "Издательство 2"

    root = tmp_path / "Книги"
    folder = root / "Кинг Стивен"
    folder.mkdir(parents=True)
    old_file = folder / "Мой перевод.fb2"
    old_file.write_bytes(original[1])
    local = library.group_by_author([core.read_metadata(old_file)], root)
    titles, folders, imported = catalog.local_candidates(local, author, root)
    assert folders == {folder}
    assert catalog.match_variants(books, titles, imported, archives) == {books[0].key: old_file}

    paths, errors = catalog.import_variants(books[1:], root, folder, archives)
    assert not errors
    new_path = paths[books[1].key]
    assert new_path.read_bytes() == original[2]
    assert old_file.read_bytes() == original[1]
    paths, errors = catalog.import_variants(books[1:], root, folder, archives)
    assert not paths and errors
    assert list(folder.glob(".fb2-import-*")) == []
    assert new_path.read_bytes() == original[2]
    local = library.group_by_author([core.read_metadata(old_file), core.read_metadata(new_path)], root)
    titles, _, imported = catalog.local_candidates(local, author, root)
    assert catalog.match_variants(books, titles, imported, archives) == {books[0].key: old_file, books[1].key: new_path}
    settings = core.Settings(kindle_root=str(tmp_path / "Kindle"))
    targets = [library.target_path(book, local[0], settings, None).name for book in local[0].books]
    assert len(set(targets)) == 2


def test_bad_index_keeps_previous_catalog(tmp_path):
    source, _, _ = archive_fixture(tmp_path)
    index = catalog.Catalog(tmp_path / "index.sqlite")
    index.build(source)
    with ZipFile(source, "w") as bundle:
        bundle.writestr("structure.info", "TITLE;AUTHOR;FILE;EXT")
        bundle.writestr("d.fb2-001-003.inp", "неполная запись")
    with pytest.raises(ValueError, match="Неполная запись"):
        index.build(source, refresh=True)
    assert len(index.authors()) == 2
