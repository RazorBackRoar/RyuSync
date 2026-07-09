# ruff: noqa: F401
"""RyuSync package.

Public helpers live in ``ryusync.main``. Imports are lazy so
``python -m ryusync.main`` does not trigger a runpy RuntimeWarning from
eagerly loading ``main`` during package import.
"""

from __future__ import annotations

import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    # Use importlib so we do not re-enter this __getattr__ via
    # ``from . import main`` (PEP 562 + relative import recursion).
    if name == "main":
        module = importlib.import_module(".main", __name__)
        globals()["main"] = module
        return module

    main_module = importlib.import_module(".main", __name__)
    try:
        value = getattr(main_module, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    main_module = importlib.import_module(".main", __name__)
    return sorted(set(globals()) | set(dir(main_module)))
