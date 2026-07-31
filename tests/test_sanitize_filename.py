from __future__ import annotations

from unittest.mock import patch

import pytest
from ryusync.main import sanitize_filename, GameOrganizer


@pytest.mark.parametrize(
    "filename, folder_path, expected",
    [
        # Happy path
        ("Super Mario Odyssey.nsp", None, "Super Mario Odyssey.nsp"),
        ("The Legend of Zelda", None, "The Legend of Zelda"),
        # Invalid characters removal
        ('Game<>:"|?*Name.nsp', None, "Game_______name.nsp"),
        ("Game/Name.nsp", None, "Name.nsp"),
        ("Game\\Name.nsp", None, "Game_name.nsp"),
        # Hex IDs removal
        ("Zelda [0100000000000000].nsp", None, "Zelda.nsp"),
        ("Pokemon [0100E20014028000].nsp", None, "Pokemon.nsp"),
        ("Pokemon [0100e20014028000].nsp", None, "Pokemon.nsp"),  # lowercase hex
        # Tag removal
        ("Zelda [DLC].nsp", None, "Zelda.nsp"),
        ("Zelda [UPD].nsp", None, "Zelda.nsp"),
        ("Zelda [GME].nsp", None, "Zelda.nsp"),
        ("Zelda [Base+DLC].nsp", None, "Zelda.nsp"),
        # Version formats
        ("Zelda [v1.6.3s].nsp", None, "Zelda.nsp"),
        ("Zelda (v1.6.3s).nsp", None, "Zelda.nsp"),
        ("Zelda v1.6.3s.nsp", None, "Zelda.nsp"),
        ("Zelda V1.nsp", None, "Zelda.nsp"),
        ("ZeldaV1.nsp", None, "Zelda.nsp"),
        ("Zelda V1.0.3.nsp", None, "Zelda.nsp"),
        ("Zelda v2.0.nsp", None, "Zelda.nsp"),
        # Other brackets and parentheses
        ("Zelda (US, EU).nsp", None, "Zelda.nsp"),
        ("Zelda [NSP].nsp", None, "Zelda.nsp"),
        # Specific keywords and symbols
        ("Nintendo Switch Game ROM.nsp", None, "nsp"),
        ("Super Mario®™", None, "Super Mario"),
        ("Zelda Game", None, "Zelda"),
        ("Zelda Game", "/home/user/Zelda Folder", "Zelda"),
        # Fallback to folder name
        ("A", "/home/user/My Awesome Game", "My Awesome"),
        ("B.nsp", "/home/user/My Awesome Game [0100000000000000]", "My Awesome.nsp"),
        ("C", "/home/user/Nintendo ROM", "Unknown Name"),
        # Edge case: No fallback provided
        ("X", None, "X"),
        ("A.nsp", None, "A.nsp"),
        # Extensions preservation
        ("Pokemon.XCI", None, "Pokemon.xci"),
        ("Pokemon", None, "Pokemon"),
    ],
)
def test_sanitize_filename(
    filename: str, folder_path: str | None, expected: str
) -> None:
    assert sanitize_filename(filename, folder_path) == expected


def test_sanitize_filename_exception_handling():
    # We can mock smart_title_case instead because it's called during the normal execution flow
    # and isn't used in sanitize_path_component (which is called in the except block)
    with patch("ryusync.main.smart_title_case", side_effect=Exception("Mocked error")):
        result = sanitize_filename("Game.nsp")
        # Should fallback to the except block returning "unknown.nsp" or similar
        assert result == "unknown.nsp"


def test_game_organizer_sanitize_filename():
    organizer = GameOrganizer()

    # Test valid and invalid chars
    assert organizer.sanitize_filename("The Legend of Zelda") == "The Legend Of Zelda"
    assert organizer.sanitize_filename('Game<:>Name"/\\|?*') == "Game___Name______"

    # Version tags
    assert organizer.sanitize_filename("Game [v1.0.0]") == "Game"
    assert organizer.sanitize_filename("Game (v2.5)") == "Game"
    assert organizer.sanitize_filename("Game V1.6.3s") == "Game"
    assert organizer.sanitize_filename("Game V1") == "Game"
    assert organizer.sanitize_filename("GameV1.0") == "Game"

    # Extensions and dots
    assert organizer.sanitize_filename("Game.") == "Game"

    # Test possessive (due to .title(), 's becomes 'S in GameOrganizer.sanitize_filename)
    assert organizer.sanitize_filename("Link's Awakening") == "Link'S Awakening"

    # Test empty or all invalid returning default
    assert organizer.sanitize_filename("") == "Unknown Game"
