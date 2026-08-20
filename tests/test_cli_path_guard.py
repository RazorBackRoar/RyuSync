"""Tests for the CLI argv directory guard."""

from __future__ import annotations

from pathlib import Path

from ryusync.main import is_allowed_cli_directory


def test_cli_allows_home_subdirectory(monkeypatch, tmp_path: Path) -> None:
    """Paths inside the home directory must be accepted."""
    home = tmp_path / "alice"
    home.mkdir()
    real = home / "Games"
    real.mkdir()

    monkeypatch.setattr(Path, "home", lambda: home)

    assert is_allowed_cli_directory(real) is True
    assert is_allowed_cli_directory(str(real)) is True
    assert is_allowed_cli_directory(home) is True


def test_cli_rejects_home_prefix_collision(monkeypatch, tmp_path: Path) -> None:
    """A name that only shares the home prefix, like /Users/alice2/, must be rejected."""
    home = tmp_path / "alice"
    home.mkdir()
    fake = tmp_path / "alice2" / "Games"
    fake.mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: home)

    assert is_allowed_cli_directory(fake) is False
    assert is_allowed_cli_directory(str(fake)) is False


def test_cli_rejects_outside_home(monkeypatch, tmp_path: Path) -> None:
    """Paths outside the home directory are rejected."""
    home = tmp_path / "alice"
    home.mkdir()
    outside = tmp_path / "outsider"
    outside.mkdir()

    monkeypatch.setattr(Path, "home", lambda: home)

    assert is_allowed_cli_directory(outside) is False
    assert is_allowed_cli_directory("/not/under/home") is False
