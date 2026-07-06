from pathlib import Path

from ryusync.app_resources import get_resource_dir, get_resource_path


def test_get_resource_dir_uses_script_directory(tmp_path: Path) -> None:
    script_path = tmp_path / "ryusync.py"

    assert get_resource_dir(base_file=script_path) == tmp_path / "resources"


def test_get_resource_path_uses_script_directory(tmp_path: Path) -> None:
    script_path = tmp_path / "ryusync.py"
    expected = tmp_path / "resources" / "nxx.png"

    assert get_resource_path("nxx.png", base_file=script_path) == expected


def test_get_resource_path_uses_pyinstaller_bundle_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("ryusync.app_resources.sys._MEIPASS", str(tmp_path), raising=False)

    assert get_resource_path("nxx.png") == tmp_path / "resources" / "nxx.png"


def test_game_organizer_resources_dir_uses_pyinstaller_bundle_root(
    monkeypatch, tmp_path: Path
) -> None:
    from ryusync.main import GameOrganizer

    monkeypatch.setattr("ryusync.app_resources.sys._MEIPASS", str(tmp_path), raising=False)

    assert Path(GameOrganizer().resources_dir) == tmp_path / "resources"
