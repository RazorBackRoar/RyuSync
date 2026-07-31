from __future__ import annotations

import pytest

from ryusync import smart_title_case

@pytest.mark.parametrize(
    "input_text, expected_output",
    [
        # Standard title casing
        ("super mario odyssey", "Super Mario Odyssey"),
        ("THE LEGEND OF ZELDA", "The Legend of Zelda"),

        # Small words handling
        ("legend of zelda", "Legend of Zelda"),
        ("breath of the wild", "Breath of the Wild"),
        ("a tale of two sons", "A Tale of Two Sons"), # First word shouldn't be lowercased
        ("mario and luigi", "Mario and Luigi"),
        ("plants vs zombies", "Plants vs Zombies"),

        # Acronyms preservation
        ("mario kart 8 deluxe dlc", "Mario Kart 8 Deluxe DLC"),
        ("pokemon EX", "Pokemon EX"),
        ("final fantasy vii hd remaster", "Final Fantasy VII HD Remaster"),
        ("super mario 3d all-stars", "Super Mario 3D All-Stars"),
        ("some game USA version", "Some Game USA Version"),

        # Hyphenated words
        ("spider-man", "Spider-Man"),
        ("pac-man", "Pac-Man"),
        ("all-star", "All-Star"),

        # Roman numerals restoration
        ("final fantasy vii", "Final Fantasy VII"),
        ("dragon quest xi", "Dragon Quest XI"),
        ("gta iv", "Gta IV"), # GTA is not in acronyms so it becomes Gta
        ("street fighter iv", "Street Fighter IV"),
        ("civilization vi", "Civilization VI"),
        ("persona 5 royal", "Persona 5 Royal"), # numbers are fine
        ("iii", "III"),

        # Mixed cases
        ("THE LEGEND OF ZELDA: BREATH OF THE WILD", "The Legend of Zelda: Breath of the Wild"),
        ("super smash bros. ultimate", "Super Smash Bros. Ultimate"),
    ]
)
def test_smart_title_case(input_text: str, expected_output: str) -> None:
    assert smart_title_case(input_text) == expected_output
