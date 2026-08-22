from __future__ import annotations

import pytest

from ryusync.main import _has_trailing_dlc_descriptors, split_dlc_name

@pytest.mark.parametrize(
    "name,expected_base,expected_descriptor",
    [
        # Simple space-separated DLC name
        ("Game DLC Pack", ["Game"], ["DLC", "Pack"]),

        # Snake_case with standard descriptor words and qualifiers
        (
            "Example_game_title_deluxe_edition_bonuses_dlc",
            ["Example", "game", "title"],
            ["deluxe", "edition", "bonuses", "dlc"]
        ),

        # Kebab-case with qualifiers
        (
            "Example-game-title-pre-order-bonuses-dlc",
            ["Example", "game", "title"],
            ["pre", "order", "bonuses", "dlc"]
        ),

        # Game name only (no trailing descriptor tokens)
        ("Base Game Only", ["Base", "Game", "Only"], []),

        # No tokens
        ("", [], []),

        # Only one token
        ("Zelda", ["Zelda"], []),

        # Descriptor words in the middle of the game name, but no trailing descriptor
        ("Super Smash Bros. Ultimate Fighter Pass Vol 1", ["Super", "Smash", "Bros.", "Ultimate", "Fighter", "Pass", "Vol", "1"], []),

        # Descriptor words in the middle, but ends with a descriptor
        # 'Pack' and 'Expansion' are both in DLC_DESCRIPTOR_WORDS
        ("Game Pack Expansion", ["Game"], ["Pack", "Expansion"]),

        # Starts with a qualifier, ends with a descriptor
        # 'pre', 'order' are qualifiers, 'bonus', 'DLC' are descriptors
        ("Pre order bonus DLC", [], ["Pre", "order", "bonus", "DLC"]),

        # All descriptor words
        ("DLC Pack", [], ["DLC", "Pack"]),

        # Just qualifiers at the end (but no descriptor word to trigger the pop)
        ("Game of the edition", ["Game", "of", "the", "edition"], []),

        # 'Extra' is in DLC_DESCRIPTOR_WORDS, 'DLC' is in DLC_DESCRIPTOR_WORDS
        ("Game DLC Extra", ["Game"], ["DLC", "Extra"]),
    ],
)
def test_split_dlc_name(name: str, expected_base: list[str], expected_descriptor: list[str]) -> None:
    """Test splitting of DLC names into base and descriptor tokens."""
    base_tokens, descriptor_tokens = split_dlc_name(name)
    assert base_tokens == expected_base
    assert descriptor_tokens == expected_descriptor

def test_split_dlc_name_single_token() -> None:
    """Test that a single token DLC name returns just the base, even if it's a descriptor word."""
    base_tokens, descriptor_tokens = split_dlc_name("DLC")
    assert base_tokens == ["DLC"]
    assert descriptor_tokens == []


@pytest.mark.parametrize(
    "filename,expected",
    [
        (
            "BUBBLE BOBBLE Sugar Dungeons Deluxe Contents [01001C30251DC000][v0][Base].nsp",
            True,
        ),
        (
            "Some Game Season Pass [0100ABCDEF120000][v0][Base].nsp",
            True,
        ),
        ("Mario Kart 8 Deluxe [0100ABCDEF120000][v0].nsp", False),
        ("GRIME Definitive Edition [0100F300169B6000][v65536][US].nsp", False),
    ],
)
def test_has_trailing_dlc_descriptors(filename: str, expected: bool) -> None:
    assert _has_trailing_dlc_descriptors(filename) is expected
