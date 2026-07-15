"""Regression tests for GAME / UPDATE / DLC categorization.

The Switch title-ID suffix is authoritative: ...000 = base game, ...800 =
update, DLC carries its own tag/hex. A base game with a non-zero version tag
(e.g. [v65536]) must still be classified GAME — the title-ID check is ordered
before the version-number heuristic. Previously the title-ID regex miscounted
hex digits ({12} instead of {11}) so it never matched, and every non-zero
version was mislabeled UPDATE.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ryusync import FileType, categorize_file


@pytest.mark.parametrize(
    "filename,expected",
    [
        # The exact GRIME files that were mislabeled.
        (
            "GRIME Definitive Edition [0100F300169B6000][v65536][US].nsp",
            FileType.GAME,
        ),
        (
            "GRIME Definitive Edition [0100F300169B6800][v458752][US].nsp",
            FileType.UPDATE,
        ),
        # Base game with a non-zero version must still be GAME.
        ("Some Game [0100ABCDEF120000][v131072].nsp", FileType.GAME),
        # Explicit v0 base game.
        ("Some Game [0100ABCDEF120000][v0].nsp", FileType.GAME),
        # Update by title-ID suffix.
        ("Another Game [0100ABCDEF120800][v262144].nsp", FileType.UPDATE),
        ("My Game [0100A77018EA0000].nsp", FileType.GAME),
        ("My Game Update [0100A77018EA0800].nsp", FileType.UPDATE),
        # DLC tag wins even when the hex happens to end in 000.
        ("Zelda [DLC] [01002DA013484000].nsp", FileType.DLC),
        ("My Game DLC Pack [0100A77018EA1000].nsp", FileType.DLC),
        # Snake-case / kebab-case DLC filenames without explicit [DLC] tag.
        (
            "V-Final_fantasy_tactics_the_ivalice_chronicles_deluxe_edition_bonuses_dlc.nsp",
            FileType.DLC,
        ),
        (
            "V-Final_fantasy_tactics_the_ivalice_chronicles_pre_order_bonuses_dlc.nsp",
            FileType.DLC,
        ),
        ("Game-Pre-Order-Bonus.nsp", FileType.DLC),
    ],
)
def test_categorize_file_uses_title_id_suffix(
    filename: str, expected: FileType
) -> None:
    assert categorize_file(filename) is expected
