"""Скачивает официальную сборку fb2cng (fbc.exe) с GitHub для сборки программы.

    python tools/get_fbc.py fbc            # последняя версия для Windows amd64
    python tools/get_fbc.py fbc linux      # для тестов на Linux
"""

import io
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

API = "https://api.github.com/repos/rupor-github/fb2cng/releases/latest"


def get(url: str, accept: str = "") -> bytes:
    headers = {"User-Agent": "FB2toKindle-build"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as r:
        return r.read()


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "fbc")
    platform = sys.argv[2] if len(sys.argv) > 2 else "windows"
    info = json.loads(get(API, "application/vnd.github+json"))
    name = f"fbc-{platform}-amd64.zip"
    asset = next((a for a in info["assets"] if a["name"] == name), None)
    if not asset:
        print(f"В выпуске {info.get('tag_name')} нет {name}", file=sys.stderr)
        return 1
    data = get(asset["browser_download_url"])
    exe = "fbc.exe" if platform == "windows" else "fbc"
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        member = next(n for n in z.namelist() if n.replace("\\", "/").split("/")[-1] == exe)
        (dest / exe).write_bytes(z.read(member))
    if platform != "windows":
        (dest / exe).chmod(0o755)
    (dest / "VERSION.txt").write_text(f"fb2cng {info.get('tag_name')}\nhttps://github.com/rupor-github/fb2cng\n", encoding="utf-8")
    print(f"fb2cng {info.get('tag_name')} → {dest / exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
