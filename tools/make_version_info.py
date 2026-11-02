"""Создаёт tools/version_info.txt (свойства exe в Windows) из версии программы."""

import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ver = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "src/fb2kindle/__init__.py").read_text(encoding="utf-8")).group(1)
nums = (list(map(int, re.findall(r"\d+", ver))) + [0, 0, 0, 0])[:4]
t = tuple(nums)
(root / "tools/version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('CompanyName', 'Chartedkey'),
      StringStruct('FileDescription', 'FB2 to Kindle'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'FB2 Kindle'),
      StringStruct('OriginalFilename', 'FB2 Kindle.exe'),
      StringStruct('ProductName', 'FB2 to Kindle'),
      StringStruct('ProductVersion', '{ver}'),
      StringStruct('LegalCopyright', 'GPL-3.0')])]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])
  ]
)
""", encoding="utf-8")
print(ver)
