"""Tests for auto-extraction of dropped archives.

Dropping a .rar/.zip/.7z (or a folder containing them) should extract the
archive IN THE SAME FOLDER, organize the resulting .nsp/.xci, remove the
original archive once organization succeeds, and delete any .url/.URL shortcut
files (e.g. "Vendor - Site shortcut.URL") that land in the
processing folder — whether they came from inside the archive or were sitting
beside it. Failed extractions (corrupt / password-protected archives) preserve
the original so the user can retry. Dry Mode must report the archive but never
extract or delete anything.

Real .zip archives are used (built with Python's zipfile and extracted with the
same `unar` the app uses) so the full path runs end-to-end. A .zip named with a
.nsp.rar extension is also covered, since `unar` detects the format by content.
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
    should_clean_file,
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
URL_NAME = "Vendor - Site shortcut.URL"


def _zip_of_nsp(zip_path: Path, nsp_name: str = NSP_NAME) -> Path:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(nsp_name, b"FAKE-NSP-DATA")
    return zip_path


def _zip_with_nsp_and_url(
    zip_path: Path, nsp_name: str = NSP_NAME, url_name: str = URL_NAME
) -> Path:
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(nsp_name, b"FAKE-NSP-DATA")
        zf.writestr(
            url_name,
            b"[InternetShortcut]\nURL=https://example.com\n",
        )
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


def test_should_clean_file_url_shortcuts() -> None:
    # The exact shortcut file the user reported must be flagged for cleanup.
    assert should_clean_file(Path("Vendor - Site shortcut.URL")) is True
    assert should_clean_file(Path("anything.URL")) is True
    assert should_clean_file(Path("anything.url")) is True
    # Game files and unrelated files are NOT cleanup targets.
    assert should_clean_file(Path(NSP_NAME)) is False
    assert should_clean_file(Path("readme.txt")) is False


# --------------------------------------------------------------------------- #
# Regular Mode: single archive dropped
# --------------------------------------------------------------------------- #
def test_single_archive_extracts_then_archive_removed(
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
    # Mirror the worker's run() ordering: remove the archive after success.
    worker._delete_extracted_archives(archives, temp_dir, base)

    # Original archive removed after successful organization.
    assert not zip_path.exists()
    assert not temp_dir.exists()
    game_folders = [p for p in desktop.iterdir() if p.is_dir()]
    assert len(game_folders) == 1
    assert list(game_folders[0].rglob("*.nsp"))
    # Unrelated file untouched; Home not polluted.
    assert notes.read_text() == "secret"
    assert {p.name for p in home.iterdir()} == {"Desktop"}


def test_single_archive_removes_url_shortcut_inside(
    window: DragDropWindow, tmp_path: Path
) -> None:
    """A .URL shortcut shipped inside the dropped archive must not survive."""
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    # Name it like the user's real drop: a .nsp.rar (unar detects Zip by content).
    archive = _zip_with_nsp_and_url(
        desktop / "deltarune [0100A0D022A68000][v0][US].nsp.rar"
    )

    window.dry_run_enabled = False
    items = window._prepare_drop([archive])
    assert len(items) == 1
    temp_dir, base, archives = items[0]
    assert archives == [archive]

    worker = FolderProcessingWorker(queue.Queue())
    worker._extract_archives_into(archives, temp_dir)
    worker.process_folder_logic(temp_dir, base)
    worker._delete_extracted_archives(archives, temp_dir, base)

    # Archive removed, no .URL shortcut left anywhere, game organized in base.
    assert not archive.exists()
    assert not any(p.suffix.lower() == ".url" for p in base.rglob("*"))
    game_folders = [p for p in desktop.iterdir() if p.is_dir()]
    assert len(game_folders) == 1
    assert list(game_folders[0].rglob("*.nsp"))


# --------------------------------------------------------------------------- #
# Regular Mode: folder containing archives dropped (the user's workflow)
# --------------------------------------------------------------------------- #
def test_folder_with_archive_extracts_then_archive_removed(
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
    worker._delete_extracted_archives(archives, folder, None)

    # Archive removed; unrelated root file preserved; game organized in place.
    assert not zip_path.exists()
    assert readme.read_text() == "keep me"
    game_folders = [p for p in folder.iterdir() if p.is_dir()]
    assert len(game_folders) == 1
    assert list(game_folders[0].rglob("*.nsp"))


def test_folder_drop_removes_url_shortcut_at_root(
    window: DragDropWindow, tmp_path: Path
) -> None:
    """A .URL sitting at the root of the dropped folder (beside the archive)
    must be cleaned up — this is the root-walk path that previously leaked."""
    folder = tmp_path / "New Folder With Items"
    folder.mkdir()
    zip_path = _zip_of_nsp(folder / "My Game [0100A77018EA0000].zip")
    url_file = folder / URL_NAME
    url_file.write_text("[InternetShortcut]\nURL=https://example.com\n")
    readme = folder / "readme.txt"
    readme.write_text("keep me")

    window.dry_run_enabled = False
    items = window._prepare_drop([folder])
    processing_path, _original_parent, archives = items[0]
    assert archives == [zip_path]

    worker = FolderProcessingWorker(queue.Queue())
    worker._extract_archives_into(archives, processing_path)
    worker.process_folder_logic(processing_path, None)
    worker._delete_extracted_archives(archives, processing_path, None)

    # Archive and the root .URL shortcut are gone; unrelated file kept; game
    # organized in place.
    assert not zip_path.exists()
    assert not url_file.exists()
    assert not any(p.suffix.lower() == ".url" for p in folder.rglob("*"))
    assert readme.read_text() == "keep me"
    game_folders = [p for p in folder.iterdir() if p.is_dir()]
    assert len(game_folders) == 1
    assert list(game_folders[0].rglob("*.nsp"))


# --------------------------------------------------------------------------- #
# Regular Mode: failed extraction preserves the archive
# --------------------------------------------------------------------------- #
def test_failed_extraction_preserves_archive(
    window: DragDropWindow, tmp_path: Path
) -> None:
    """A corrupt archive cannot be extracted, so it must NOT be deleted."""
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    corrupt = desktop / "Broken [0100A77018EA0000].zip"
    corrupt.write_bytes(b"this is not a real zip archive")

    window.dry_run_enabled = False
    items = window._prepare_drop([corrupt])
    temp_dir, base, archives = items[0]

    worker = FolderProcessingWorker(queue.Queue())
    worker._extraction_errors = []
    worker._extract_archives_into(archives, temp_dir)
    # Extraction failed -> recorded, not deleted.
    assert corrupt.name in worker._extraction_errors
    # The worker's run() would `continue` here (no game files). Mirror the
    # post-organization cleanup call anyway: a failed archive must survive it.
    worker._delete_extracted_archives(archives, temp_dir, base)
    assert corrupt.exists()


# --------------------------------------------------------------------------- #
# Dry Mode: report archives, never extract or delete
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
