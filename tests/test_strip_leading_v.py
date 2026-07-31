from __future__ import annotations

import pytest

from ryusync import strip_leading_v


@pytest.mark.parametrize(
    "name,expected",
    [
        # Happy paths - standard removal
        ("V-Final", "Final"),
        ("v-Final", "Final"),
        ("V Final", "Final"),
        ("v Final", "Final"),

        # Edge cases - varying delimiters
        ("V- Final", "Final"),
        ("V - Final", "Final"),
        ("V--Final", "Final"),

        # Edge cases - trailing spaces left after regex
        ("V ", ""),
        ("v-  ", ""),
        ("V-  Game", "Game"),

        # Just the letter
        ("V", ""),
        ("v", ""),

        # Should not modify (no word boundary matching expected pattern)
        ("V1.0", "V1.0"),
        ("v1.0", "v1.0"),
        ("Version 1.0", "Version 1.0"),
        ("Very Good Game", "Very Good Game"),
        ("V_Final", "V_Final"),
        ("V_ Final", "V_ Final"), # V_ doesn't match \b because _ is a word char

        # Multiple words
        ("V-Example Game Title", "Example Game Title"),

        # Empty string
        ("", ""),

        # Spaced prefix - regex checks start of string (`^`), so leading spaces make it not match,
        # but the function `.strip()`s the result, so `  V-Final` becomes `V-Final`
        ("  V-Final", "V-Final"),
        (" V-Final ", "V-Final"),
    ],
)
def test_strip_leading_v(name: str, expected: str) -> None:
    """Test that a leading 'V' or 'v' release tag is correctly removed."""
    assert strip_leading_v(name) == expected
