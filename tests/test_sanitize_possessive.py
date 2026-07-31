from __future__ import annotations

import pytest

from ryusync.main import sanitize_possessive


@pytest.mark.parametrize(
    "text, expected",
    [
        # Normal words
        ("Mario", "Mario"),
        ("Luigi", "Luigi"),
        ("Game", "Game"),

        # Singular possessive ('s)
        ("Mario's", "Mario"),
        ("Luigi's", "Luigi"),
        ("Princess's", "Princess"),
        ("Mario’s", "Mario"),
        ("Luigi’s", "Luigi"),

        # Plural possessive (s')
        ("Marios'", "Marios"),
        ("Luigis'", "Luigis"),
        ("Boys'", "Boys"),
        ("Marios’", "Marios"),
        ("Luigis’", "Luigis"),

        # Edge cases
        ("", ""),
        ("'s", ""),
        ("s'", "s"),
        ("s", "s"),
        ("'", "'"),
        ("a", "a"),
        ("  Mario's  ", "  Mario's  "), # Doesn't strip whitespace unless we expect it to, but let's check code.
        # It just does endswith("'s"), so trailing whitespace would mean it doesn't end with "'s"
    ],
)
def test_sanitize_possessive(text: str, expected: str) -> None:
    """Test that sanitize_possessive correctly handles various forms."""
    assert sanitize_possessive(text) == expected
