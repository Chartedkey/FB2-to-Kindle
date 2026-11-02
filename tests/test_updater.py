import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fb2kindle import updater  # noqa: E402


def rel(path: Path, sha: str, version="9.9.9"):
    return updater.Release(version=version, notes="", url="", asset_url=path.as_uri(), asset_name="FB2-Kindle-9.9.9-windows.zip",
                           size=path.stat().st_size, sha256=sha)


@pytest.fixture()
def tmp_update(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "work_dir", lambda: tmp_path / "work")
    (tmp_path / "work").mkdir()
    z = tmp_path / "src.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("FB2 Kindle/FB2 Kindle.exe", b"exe")
        f.writestr("FB2 Kindle/_internal/x.dll", b"dll")
        f.writestr("FB2 Kindle/fbc/fbc.exe", b"fbc")
    return z


def test_is_newer():
    r = updater.Release("2.1.0", "", "", "", "", 0, "")
    assert updater.is_newer(r, "2.0.1") and not updater.is_newer(r, "2.1.0") and not updater.is_newer(r, "2.10.0")


def test_download_and_unpack(tmp_update):
    sha = hashlib.sha256(tmp_update.read_bytes()).hexdigest()
    seen = []
    z = updater.download(rel(tmp_update, sha), progress=seen.append)
    new = updater.unpack(z)
    assert (new / "FB2 Kindle.exe").read_bytes() == b"exe"
    assert (new / "fbc" / "fbc.exe").is_file()


def test_bad_checksum(tmp_update):
    with pytest.raises(RuntimeError, match="контрольная сумма"):
        updater.download(rel(tmp_update, "0" * 64))


def test_unsafe_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "work_dir", lambda: tmp_path / "work")
    (tmp_path / "work").mkdir()
    z = tmp_path / "evil.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("../evil.txt", b"x")
        f.writestr("FB2 Kindle/FB2 Kindle.exe", b"exe")
    with pytest.raises(RuntimeError):
        updater.unpack(z)
    assert not (tmp_path / "evil.txt").exists()


def test_script_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "work_dir", lambda: tmp_path)
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: None)
    s = updater.launch_replacer(Path("C:/t/new"), "2.1.0", app_dir=Path("C:/Users/Д'Артаньян/FB2 Kindle"), pid=123)
    text = s.read_text(encoding="utf-8-sig")
    assert "Д''Артаньян" in text and "-Id 123" in text and "'2.1.0'" in text
