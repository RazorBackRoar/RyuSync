"""Unit tests for the restore_roman_numerals function."""

from __future__ import annotations

import pytest

from ryusync import restore_roman_numerals


@pytest.mark.parametrize(
    "text,expected",
    [
        # Standard capitalization scenarios
        ("Final Fantasy vii", "Final Fantasy VII"),
        ("Grand Theft Auto v", "Grand Theft Auto V"),
        ("Zelda ii The Adventure of Link", "Zelda II The Adventure of Link"),
        ("Super Mario bros. iii", "Super Mario bros. III"),
        # Full range of roman numerals up to XX
        ("i", "I"),
        ("ii", "II"),
        ("iii", "III"),
        ("iv", "IV"),
        ("v", "V"),
        ("vi", "VI"),
        ("vii", "VII"),
        ("viii", "VIII"),
        ("ix", "IX"),
        ("x", "X"),
        ("xi", "XI"),
        ("xii", "XII"),
        ("xiii", "XIII"),
        ("xiv", "XIV"),
        ("xv", "XV"),
        ("xvi", "XVI"),
        ("xvii", "XVII"),
        ("xviii", "XVIII"),
        ("xix", "XIX"),
        ("xx", "XX"),
        # Edge Cases: Should only target whole words
        ("fix", "fix"),
        ("civic", "civic"),
        ("every", "every"),
        ("six", "six"),
        ("toxic", "toxic"),
        ("Ivan", "Ivan"),
        ("Oliver", "Oliver"),
        ("x-ray", "X-ray"),  # The 'x' is a standalone word boundary character in regex
        # Upper case inputs remain unchanged
        ("FINAL FANTASY VII", "FINAL FANTASY VII"),
        # Keep non-roman numeral text exactly as it is
        ("Some Game i", "Some Game I"),
    ],
)
def test_restore_roman_numerals(text: str, expected: str) -> None:
    """Test that restore_roman_numerals correctly restores isolated roman numerals."""
    assert restore_roman_numerals(text) == expected
