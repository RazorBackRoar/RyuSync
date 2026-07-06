"""Comprehensive safety, dry run, and cleanup tests for DragDropWindow and FolderProcessingWorker."""

from __future__ import annotations

import os
import queue
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ryusync import (
    DragDropWindow,
    FolderProcessingWorker,
    is_protected_directory,
    sanitize_path_component,
    should_clean_file,
    unique_destination_path,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def test_is_protected_directory_blocks_high_level_paths() -> None:
    """Ensure standard system directories are recognized as protected."""
    assert is_protected_directory(Path("/")) is True
    assert is_protected_directory(Path.home()) is True
    assert is_protected_directory(Path.home() / "Desktop") is True
    assert is_protected_directory(Path.home() / "Downloads") is True
    assert is_protected_directory(Path.home() / "Workspace") is True
    assert is_protected_directory(Path.home() / "Workspace" / "Apps") is True
    
    # Non-protected subpath should be False
    assert is_protected_directory(Path.home() / "Documents" / "MyGames") is False


def test_sanitize_path_component_prevents_hidden_traversal_and_control_chars() -> None:
    """Generated filenames must remain single safe macOS path components."""
    sanitized = sanitize_path_component(
        "../.bad:name?\nwith\tspaces.nsp",
        default="Game",
        preserve_extension=True,
    )

    assert sanitized.endswith(".nsp")
    assert not sanitized.startswith(".")
    assert "/" not in sanitized
    assert ":" not in sanitized
    assert "?" not in sanitized
    assert "\n" not in sanitized
    assert "\t" not in sanitized

    long_name = sanitize_path_component("A" * 260 + ".xci")
    assert long_name.endswith(".xci")
    assert len(long_name) <= 180


def test_unique_destination_path_adds_predictable_suffix(tmp_path: Path) -> None:
    """Duplicate destination files get _1/_2 suffixes rather than overwriting."""
    destination = tmp_path / "Game.nsp"
    destination.write_text("original")
    source = tmp_path / "Incoming.nsp"
    source.write_text("incoming")

    assert unique_destination_path(destination, source).name == "Game_1.nsp"
    (tmp_path / "Game_1.nsp").write_text("other")
    assert unique_destination_path(destination, source).name == "Game_2.nsp"


def test_should_clean_file_identifies_url_and_system_metadata() -> None:
    """Ensure only .url/.URL files and metadata files are slated for cleanup."""
    assert should_clean_file(Path("test.url")) is True
    assert should_clean_file(Path("test.URL")) is True
    assert should_clean_file(Path("desktop.ini")) is True
    assert should_clean_file(Path(".ds_store")) is True
    
    # Other file types should NOT be cleaned
    assert should_clean_file(Path("readme.txt")) is False
    assert should_clean_file(Path("game.nsp")) is False
    assert should_clean_file(Path("cover.jpg")) is False
    assert should_clean_file(Path("video.mp4")) is False


def test_multi_drop_rejects_unsupported_files_without_moving_them(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Unsupported multi-drop files stay untouched and out of the staging folder."""
    game_file = tmp_path / "Everhood [0100E20014028000][GME].nsp"
    game_file.write_text("fake_game")
    notes_file = tmp_path / "notes.txt"
    notes_file.write_text("do not move")

    window = DragDropWindow()
    try:
        queue_items = window._prepare_drop([game_file, notes_file])
    finally:
        window.close()

    assert len(queue_items) == 1
    temp_dir = queue_items[0][0]
    assert temp_dir.name.startswith("ryusync_temp_")
    assert (temp_dir / game_file.name).exists()
    assert notes_file.exists()
    assert notes_file.read_text() == "do not move"
    assert not (temp_dir / notes_file.name).exists()


def test_dry_mode_performs_zero_filesystem_mutations(tmp_path: Path, qapp: QApplication) -> None:
    """Verify that Dry Run mode preview performs zero file modifications or deletions."""
    # Setup files
    game_file = tmp_path / "Everhood [0100E20014028000][GME].nsp"
    game_file.write_text("fake_game")
    url_file = tmp_path / "link.url"
    url_file.write_text("[InternetShortcut]\nURL=https://google.com")
    txt_file = tmp_path / "readme.txt"
    txt_file.write_text("info")

    window = DragDropWindow()
    window.dry_run_enabled = True
    try:
        # Simulate dropping the path
        window.process_dropped_directory(tmp_path)
        
        # Verify filesystem is unchanged
        assert game_file.exists()
        assert url_file.exists()
        assert txt_file.exists()
        assert not (tmp_path / "Everhood").exists()  # Game folder shouldn't be created
    finally:
        window.close()


def test_regular_mode_deletes_urls_but_preserves_other_unrelated_files(tmp_path: Path, qapp: QApplication) -> None:
    """Verify regular mode processes game files, deletes .url, but leaves other non-game files untouched."""
    # Setup test structure
    game_folder = tmp_path / "MyGame"
    game_folder.mkdir()
    
    game_file = game_folder / "Everhood [0100E20014028000][GME].nsp"
    game_file.write_text("fake_game")
    
    url_file = game_folder / "shortcut.url"
    url_file.write_text("url")
    
    url_upper_file = game_folder / "shortcut.URL"
    url_upper_file.write_text("url")
    
    txt_file = game_folder / "readme.txt"
    txt_file.write_text("don't delete me")
    
    # A file outside the dropped scope
    outside_file = tmp_path / "outside.url"
    outside_file.write_text("outside")

    window = DragDropWindow()
    window.dry_run_enabled = False
    try:
        window.process_dropped_directory(game_folder)
        
        # Game should be organized in the canonical folder
        canonical_folder = game_folder / "Everhood"
        assert canonical_folder.exists()
        assert (canonical_folder / "Everhood [0100E20014028000] [GME].nsp").exists()
        
        # URL files inside the dropped scope should be deleted
        assert not url_file.exists()
        assert not url_upper_file.exists()
        
        # Non-game files (like .txt) must be preserved
        assert txt_file.exists()
        
        # URL files outside the scope must NOT be deleted
        assert outside_file.exists()
    finally:
        window.close()


def test_worker_safety_prevents_outside_mutations(tmp_path: Path, qapp: QApplication) -> None:
    """Verify worker thread processes single files securely within temp directories without parent traversal."""
    source_folder = tmp_path / "source"
    source_folder.mkdir()
    
    game_file = source_folder / "Everhood [0100E20014028000][GME].nsp"
    game_file.write_text("game")
    
    sibling_file = source_folder / "sibling.txt"
    sibling_file.write_text("sibling")

    q: queue.Queue = queue.Queue()
    worker = FolderProcessingWorker(q)
    
    # Simulate single file wrapping flow
    window = DragDropWindow()
    try:
        temp_dir, base = window._wrap_single_file(game_file)
        
        # Run worker processing on the temp dir
        worker.process_folder_logic(temp_dir, original_parent=base)
        
        # Verify original game file is organized and moved back to base
        canonical_folder = source_folder / "Everhood"
        assert canonical_folder.exists()
        assert (canonical_folder / "Everhood [0100E20014028000] [GME].nsp").exists()
        
        # Sibling file outside temp_dir must remain untouched
        assert sibling_file.exists()
        assert sibling_file.read_text() == "sibling"
    finally:
        window.close()
