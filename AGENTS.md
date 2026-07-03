# RyuSync AGENTS

**Package:** `ryusync`
**Version:** 0.1.0

Use this file with `../AGENTS.md`. It only records RyuSync-specific context.

## Purpose And Entry Points

- Main app: `src/ryusync/main.py`
- Key areas: `src/ryusync/main.py`, `src/ryusync/app_resources.py`
- Run locally: `uv run python -m ryusync.main`
- Build through workspace wrappers: `ryusyncbuild` or `razorbuild RyuSync`

## Non-Obvious Rules

- RyuSync organizes Nintendo Switch game files (`.nsp` and `.xci`) by processing directory drags and drops.
- File-moving and merging logic should be carefully guarded to prevent accidental deletion or moving of files outside designated directories.
- Always use `ryusync.app_resources.get_resource_path` for resolving bundled assets correctly when bundled with PyInstaller.

## Verification

Baseline:

```bash
uv run ruff check .
uv run ty check src --python-version 3.14
uv run pytest tests/ -q
```
