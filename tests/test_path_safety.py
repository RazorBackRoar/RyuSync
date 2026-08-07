"""Tests for path safety guards and DLC folder matching."""

import os
from pathlib import Path

import pytest

from ryusync import (
    find_dlc_parent_folder,
    is_cli_directory_safe,
    is_path_safe,
    safe_move,
)
from ryusync.main import FileOperationError


def test_is_path_safe_accepts_paths_inside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "games"
    root.mkdir()
    inside = root / "Zelda.nsp"
    inside.write_text("data")

    assert is_path_safe(inside, [root]) is True
    assert is_path_safe(root, [root]) is True


def test_is_path_safe_rejects_paths_outside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "games"
    root.mkdir()
    outside = tmp_path / "outside.nsp"
    outside.write_text("data")

    assert is_path_safe(outside, [root]) is False


def test_safe_move_blocks_escape_outside_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "games"
    root.mkdir()
    src = root / "game.nsp"
    src.write_text("data")
    dst = tmp_path / "escaped.nsp"

    with pytest.raises(FileOperationError, match="outside the selected scope"):
        safe_move(src, dst, [root])


def test_is_cli_directory_safe_allows_home_and_volumes() -> None:
    home_games = os.path.join(os.path.expanduser("~"), "Switch")
    assert is_cli_directory_safe(home_games) is True
    assert is_cli_directory_safe("/Volumes/Switch") is True


def test_is_cli_directory_safe_rejects_system_paths() -> None:
    assert is_cli_directory_safe("/etc") is False
    assert is_cli_directory_safe("/tmp/ryusync") is False


def test_find_dlc_parent_folder_matches_base_game_id(tmp_path: Path) -> None:
    base_folder = tmp_path / "Zelda"
    base_folder.mkdir()
    game_folders = {"0100A77018EA": base_folder}
    dlc_name = "DLC Pack [0100A77018EA8000].nsp"

    parent = find_dlc_parent_folder(dlc_name, game_folders)

    assert parent == base_folder


def test_find_dlc_parent_folder_returns_none_without_id_match(tmp_path: Path) -> None:
    other_folder = tmp_path / "Other"
    other_folder.mkdir()
    game_folders = {"0100BBBBBBBB": other_folder}

    assert find_dlc_parent_folder("Random [0100A77018EA8000].nsp", game_folders) is None
