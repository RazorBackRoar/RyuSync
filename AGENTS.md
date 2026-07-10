# RyuSync AGENTS

**Package:** `ryusync`  
**Version:** 1.0.0  
**GitHub:** `RazorBackRoar/RyuSync`

Use with `../AGENTS.md`. Keep this file RyuSync-specific.

## Purpose and entry points

Native macOS drag-and-drop organizer for Nintendo Switch `.nsp` / `.xci` files (PySide6). Most domain logic lives in a large `main.py` — avoid drive-by refactors.

- Main: `src/ryusync/main.py`
- Resources: `src/ryusync/app_resources.py`
- Run: `uv run ryusync` or `uv run python -m ryusync.main`
- Build: `ryusyncbuild` or `razorbuild RyuSync`

Dev clones expect sibling `../.razorcore` (editable `razorcore>=1.211.0`). Archive extraction needs `unar` (`brew install unar`).

## Branding

- Accents: red `#ff2d55`, blue `#00d0ff` (header/footer/drop-zone borders).
- No in-app logo — icon appears in Dock, Finder, Get Info, and the `.app` bundle only.

## razorcore integration (v1.1)

| Surface | Usage |
|---------|--------|
| `logging` / `config.get_version` | Logging + version |
| `appinfo` / `updates` | Startup banner, About, update check |
| `threading.BaseWorker` | `FolderProcessingWorker` — domain `progress(str,int,int)` kept; `stop()` → `request_cancel()` |

Switch-specific filename/path sanitizers stay local (domain logic).

## Non-obvious rules

- Guard file-move/merge logic so work stays inside designated roots.
- Bundled entry must use **absolute** imports (`from ryusync.app_resources import …`). Relative imports crash PyInstaller/DMG launches.
- Resolve bundled assets via `ryusync.app_resources.get_resource_path`.
- Preserve Dry Mode semantics (preview without moving) and `[GME]` / `[UPD]` / `[DLC]` tagging.
- Packaging notes: `docs/DMG_BUILD_README.md`.

## Verification

```bash
uv run ruff check .
uv run ty check src --python-version 3.14
uv run pytest tests/ -q
```

## CI limitations

CI covers lint, types, and unit tests. It does **not** prove drag-drop UX, `unar` on every machine, or live organize of user libraries.

## Release checklist

- [ ] ruff / ty / pytest clean
- [ ] App launches after clean `uv sync`
- [ ] Dry Mode + one Real Mode organize flow exercised
- [ ] Packaging artifact smoke-tested when shipping a DMG
- [ ] `pyproject.toml` version matches README badge

## Safety and scope

- Read `../../docs/Agent Pre-Safety Rules.md` before changes.
- Keep changes scoped to this app unless asked otherwise.
- Do not create branches, commit, or push unless explicitly requested.
- Behavioral guidelines inherit from `../AGENTS.md`.
