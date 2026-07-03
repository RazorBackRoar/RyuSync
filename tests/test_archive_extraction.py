"""Tests for auto-extraction of dropped archives.

Dropping a .rar/.zip/.7z (or a folder containing them) should extract the
archive IN THE SAME FOLDER, organize the resulting .nsp/.xci, and leave the
original archive and all unrelated files untouched. Dry Mode must report the
archive but never extract it.

Real .zip archives are used (built with Python's zipfile and extracted with the
same `unar` the app uses) so the full path runs end-to-end.
"""

from __future__ import annotations

import os
import queue
import zipfile
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from ryusync import (
    DragDropWindow,
    FolderProcessingWorker,
    find_unar,
    is_archive_file,
)


pytestmark = pytest.mark.skipif(
    find_unar() is None, reason="unar extractor not installed"
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


@pytest.fixture
def window(qapp: QApplication) -> DragDropWindow:
    win = DragDropWindow()
    yield win
    win.close()


NSP_NAME = "My Game [0100A77018EA0000].nsp"


def _zip_of_nsp(zip_path: Path, nsp_name: str = NSP_NAME) -> Path:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(nsp_name, b"FAKE-NSP-DATA")
    return zip_path


# --------------------------------------------------------------------------- #
# Unit-level
# --------------------------------------------------------------------------- #
def test_find_unar_available() -> None:
    assert find_unar() is not None


def test_is_archive_file(tmp_path: Path) -> None:
    for name in ["a.rar", "a.zip", "a.7z", "Game [0100].nsp.rar", "B.ZIP"]:
        p = tmp_path / name
        p.write_text("x")
        assert is_archive_file(p) is True, name
    for name in ["a.nsp", "a.xci", "a.txt"]:
        p = tmp_path / name
        p.write_text("x")
        assert is_archive_file(p) is False, name


# --------------------------------------------------------------------------- #
# Regular Mode: single archive dropped
# --------------------------------------------------------------------------- #
def test_single_archive_extracts_in_same_folder(
    window: DragDropWindow, tmp_path: Path
) -> None:
    home = tmp_path
    desktop = home / "Desktop"
    desktop.mkdir()
    zip_path = _zip_of_nsp(desktop / "My Game [0100A77018EA0000].zip")
    notes = desktop / "notes.txt"
    notes.write_text("secret")

    window.dry_run_enabled = False
    items = window._prepare_drop([zip_path])

    assert len(items) == 1
    temp_dir, base, archives = items[0]
    # Extraction is staged INSIDE the same folder, never the parent/Home.
    assert base == desktop
    assert temp_dir.parent == desktop
    assert temp_dir.name.startswith("ryusync_temp_")
    assert archives == [zip_path]

    worker = FolderProcessingWorker(queue.Queue())
    worker._extract_archives_into(archives, temp_dir)
    worker.process_folder_logic(temp_dir, base)

    # Original archive preserved; organized game folder lands in the same folder.
    assert zip_path.exists()
    assert not temp_dir.exists()
    game_folders = [p for p in desktop.iterdir() if p.is_dir()]
    assert len(game_folders) == 1
    assert list(game_folders[0].rglob("*.nsp"))
    # Unrelated file untouched; Home not polluted.
    assert notes.read_text() == "secret"
    assert {p.name for p in home.iterdir()} == {"Desktop"}


# --------------------------------------------------------------------------- #
# Regular Mode: folder containing archives dropped (the user's workflow)
# --------------------------------------------------------------------------- #
def test_folder_with_archive_extracts_in_place(
    window: DragDropWindow, tmp_path: Path
) -> None:
    folder = tmp_path / "New Folder With Items"
    folder.mkdir()
    zip_path = _zip_of_nsp(folder / "My Game [0100A77018EA0000].zip")
    readme = folder / "readme.txt"
    readme.write_text("keep me")

    window.dry_run_enabled = False
    items = window._prepare_drop([folder])

    assert len(items) == 1
    processing_path, original_parent, archives = items[0]
    assert processing_path == folder
    assert original_parent is None
    assert archives == [zip_path]

    worker = FolderProcessingWorker(queue.Queue())
    worker._extract_archives_into(archives, folder)
    worker.process_folder_logic(folder, None)

    # Archive + unrelated root file preserved; game organized in place.
    assert zip_path.exists()
    assert readme.read_text() == "keep me"
    game_folders = [p for p in folder.iterdir() if p.is_dir()]
    assert len(game_folders) == 1
    assert list(game_folders[0].rglob("*.nsp"))


# --------------------------------------------------------------------------- #
# Dry Mode: report archives, never extract
# --------------------------------------------------------------------------- #
def test_dry_run_reports_folder_archive_without_extracting(
    window: DragDropWindow, tmp_path: Path
) -> None:
    folder = tmp_path / "Games"
    folder.mkdir()
    zip_path = _zip_of_nsp(folder / "My Game [0100A77018EA0000].zip")

    window.dry_run_enabled = True
    preview = window._generate_dry_run_preview([folder])

    assert "Archives to extract: 1" in preview
    assert "My Game [0100A77018EA0000].zip" in preview
    assert "extract in place" in preview
    # Nothing was extracted — folder still contains only the archive.
    assert [p.name for p in folder.iterdir()] == [zip_path.name]


def test_dry_run_single_archive_file_not_extracted(
    window: DragDropWindow, tmp_path: Path
) -> None:
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    zip_path = _zip_of_nsp(desktop / "My Game [0100A77018EA0000].zip")

    window.dry_run_enabled = True
    preview = window._generate_dry_run_preview([zip_path])

    assert "Archives to extract: 1" in preview
    assert not list(desktop.glob("*.nsp"))
    assert [p.name for p in desktop.iterdir()] == [zip_path.name]
