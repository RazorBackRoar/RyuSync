from __future__ import annotations

import sys
from pathlib import Path


def get_resource_root(*, base_file: str | Path | None = None) -> Path:
    """Return the directory that should be treated as the app root for resources."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)

    if base_file is None:
        return Path(__file__).resolve().parent

    start = Path(base_file).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "resources").is_dir():
            return candidate
    return start


def get_resource_dir(*, base_file: str | Path | None = None) -> Path:
    """Return the directory containing bundled resources."""
    return get_resource_root(base_file=base_file) / "resources"


def get_resource_path(name: str, *, base_file: str | Path | None = None) -> Path:
    """Return the expected path for a bundled resource."""
    return get_resource_dir(base_file=base_file) / name
