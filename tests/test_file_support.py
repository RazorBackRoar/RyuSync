from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ryusync import is_supported_game_or_archive_file


@pytest.mark.parametrize(
    "filename, expected",
    [
        # Supported game files
        ("game.nsp", True),
        ("game.xci", True),
        # Supported archive files
        ("archive.rar", True),
        ("archive.zip", True),
        ("archive.7z", True),
        # Case insensitivity checks
        ("game.NSP", True),
        ("archive.ZIP", True),
        ("game.Xci", True),
        # Unsupported files
        ("readme.txt", False),
        ("game.exe", False),
        ("archive.tar.gz", False),
        ("game.nro", False),
        ("image.png", False),
        # No extension
        ("game", False),
        ("archive.", False),
        # Path with directories
        ("/path/to/game.nsp", True),
        ("/path/to/readme.txt", False),
    ],
)
def test_is_supported_game_or_archive_file(filename: str, expected: bool) -> None:
    assert is_supported_game_or_archive_file(Path(filename)) is expected
