"""Запуск программы из исходников: двойной щелчок (нужны Python и PySide6) или `python run.pyw`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from fb2kindle.ui.main_window import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
