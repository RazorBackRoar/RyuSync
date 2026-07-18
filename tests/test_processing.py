from __future__ import annotations

import os
import queue
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from ryusync import (
    DragDropWindow,
    FolderProcessingWorker,
    extract_game_id,
    get_base_id,
    merge_folders_by_base_id,
    standardize_filenames_to_folder,
)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if isinstance(app, QApplication):
        return app
    return QApplication([])


def test_extract_game_id_supports_standard_switch_title_ids() -> None:
    filename = "My Game [0100A77018EA0000].nsp"

    full_id = extract_game_id(filename)

    assert full_id == "0100A77018EA0000"
    assert get_base_id(full_id) == "0100A77018EA"


def test_merge_folders_by_base_id_merges_duplicate_game_folders(tmp_path: Path) -> None:
    primary = tmp_path / "Primary"
    duplicate = tmp_path / "Duplicate"
    primary.mkdir()
    duplicate.mkdir()

    (primary / "My Game [0100A77018EA0000] [GME].nsp").write_text("")
    (duplicate / "My Game [0100A77018EA0800] [UPD].nsp").write_text("")

    merge_folders_by_base_id(tmp_path)

    remaining_folders = [path for path in tmp_path.iterdir() if path.is_dir()]
    assert len(remaining_folders) == 1
    merged_folder = remaining_folders[0]
    assert (merged_folder / "My Game [0100A77018EA0000] [GME].nsp").exists()
    assert (merged_folder / "My Game [0100A77018EA0800] [UPD].nsp").exists()


def test_standardize_filenames_to_folder_completes_without_duplicate_tail_code(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "my weird game"
    folder.mkdir()
    source_file = folder / "MY WEIRD GAME [0100A77018EA0000][GME].nsp"
    source_file.write_text("")

    standardize_filenames_to_folder(tmp_path)

    renamed_folder = tmp_path / "My Weird Game"
    renamed_file = renamed_folder / "My Weird Game [0100A77018EA0000] [GME].nsp"
    assert renamed_folder.exists()
    assert renamed_file.exists()


def test_process_dropped_directory_handles_three_files_without_progress_signal_errors(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    directory = tmp_path / "drop"
    directory.mkdir()

    (directory / "My Game [0100A77018EA0000].nsp").write_text("")
    (directory / "My Game Update [0100A77018EA0800].nsp").write_text("")
    (directory / "My Game DLC Pack [0100A77018EA1000].nsp").write_text("")

    window = DragDropWindow()
    try:
        summary = window.process_dropped_directory(directory)
    finally:
        window.close()

    game_folder = directory / "My Game"
    game_folders = [
        path
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    ]

    assert "Successfully processed 3 files." in summary
    assert len(game_folders) == 1

    game_folder = game_folders[0]
    dlc_folder = game_folder / "DLC"
    assert dlc_folder.exists()
    assert len(list(game_folder.glob("*.nsp"))) == 2
    assert len(list(dlc_folder.glob("*.nsp"))) == 1


def test_worker_process_folder_logic_groups_snake_case_dlc_together(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Two snake-case DLC files for the same game should land in one DLC folder."""
    directory = tmp_path / "drop"
    directory.mkdir()

    (directory / "V-Example_game_title_deluxe_edition_bonuses_dlc.nsp").write_text("")
    (directory / "V-Example_game_title_pre_order_bonuses_dlc.nsp").write_text("")

    q: queue.Queue = queue.Queue()
    worker = FolderProcessingWorker(q)
    summary = worker.process_folder_logic(directory)

    assert "Successfully processed 2 files." in summary

    game_folders = [
        path
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    ]
    assert len(game_folders) == 1

    dlc_folder = game_folders[0] / "DLC"
    assert dlc_folder.exists()
    assert len(list(dlc_folder.glob("*.nsp"))) == 2


def test_worker_does_not_fuzzy_merge_different_games(
    tmp_path: Path,
    qapp: QApplication,
) -> None:
    """Similar game names must not share a folder when Title IDs differ."""
    directory = tmp_path / "drop"
    directory.mkdir()

    (directory / "Super Mario Odyssey [01008B000936C000].nsp").write_text("odyssey")
    (directory / "Super Mario 3D World [010028600EBDA800].nsp").write_text("3dworld")

    q: queue.Queue = queue.Queue()
    worker = FolderProcessingWorker(q)
    summary = worker.process_folder_logic(directory)

    assert "Successfully processed 2 files." in summary

    game_folders = [
        path
        for path in directory.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    ]
    assert len(game_folders) == 2
    folder_names = {path.name for path in game_folders}
    assert "Super Mario Odyssey" in folder_names
    assert "Super Mario 3D World" in folder_names
