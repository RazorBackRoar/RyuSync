"""Tests for get_clean_base_name."""

from __future__ import annotations

import pytest

from ryusync.main import get_clean_base_name


@pytest.mark.parametrize(
    "filename,is_dlc,expected",
    [
        # Scenarios where leading bracket contains the title
        ("[Super Mario Odyssey] v0.nsp", False, "Super Mario Odyssey"),
        ("[The Legend of Zelda] Some Release Group.nsp", False, "The Legend of Zelda"),

        # Scenario where leading bracket contains a roman numeral that should be restored
        ("[final fantasy vi] update.nsp", False, "Final Fantasy VI"),
        ("[Super Mario Party III] (Junk).nsp", False, "Super Mario Party III"),

        # Scenarios where leading bracket should be ignored (e.g. Hex IDs, region codes)
        ("[0100000000010000] Super Mario.nsp", False, "Super Mario"),
        ("[EU] Super Mario.nsp", False, "Super Mario"),
        ("[USA] Super Mario.nsp", False, "Super Mario"),
        ("[v131072] Zelda.nsp", False, "Zelda"),
        ("[update] Metroid.nsp", False, "Metroid"),

        # Scenario where junk keywords should be removed
        ("Super Mario ROM.nsp", False, "Super Mario"),
        ("Awesome Game BASE.nsp", False, "Awesome"),
        ("My App nintendo.nsp", False, "My App"),

        # Scenario where parentheses are removed
        ("My App (USA) (v1.0).nsp", False, "My App"),
        ("Another Title (World).nsp", False, "Another Title"),

        # Scenario where trademarks are removed
        ("Super Mario™ Odyssey®.nsp", False, "Super Mario Odyssey"),
        ("Super Mario®™", False, "Super Mario"),

        # Scenario where DLC descriptors are stripped
        ("V-Example_game_title_deluxe_edition_bonuses_dlc.nsp", True, "Example game title"),
        ("Some-App-Deluxe-Edition-Bonuses-DLC.nsp", True, "Some App"),

        # Edge cases and fallbacks
        ("[0100000000010000].nsp", False, "Unknown Game"),
        (" (USA) [EU] .nsp", False, "Unknown Game"),
    ],
)
def test_get_clean_base_name(filename: str, is_dlc: bool, expected: str) -> None:
    """Tests that get_clean_base_name correctly processes a variety of naming conventions."""
    assert get_clean_base_name(filename, is_dlc=is_dlc) == expected
