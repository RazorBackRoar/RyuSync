# Architecture — RyuSync

Developer map for the Nintendo Switch `.nsp` / `.xci` organizer (PySide6).

Most domain logic lives in `src/ryusync/main.py` — avoid drive-by refactors.

## Entry points

| Path | Role |
|------|------|
| `src/ryusync/main.py` | GUI, `GameOrganizer`, CLI handler |
| `src/ryusync/app_resources.py` | Bundled asset paths (PyInstaller-safe **absolute** imports) |

Run:

```bash
uv run ryusync
uv run ryusync /path/to/games   # CLI folder argument
```

CLI paths must start with an allowed prefix (`$HOME`, `/Volumes/…`). Other
roots are rejected before processing begins.

## Organization flow

```text
Drop / CLI path
  └─ is_protected_directory guard (blocks Home, Desktop, drive roots for folders)
  └─ scan + categorize_file (title ID suffix heuristics)
  └─ tag [GME] / [UPD] / [DLC]
  └─ Dry Mode preview OR Real Mode move/rename
  └─ optional unar extraction (.rar/.zip/.7z)
```

## Categorization rules

Title ID suffix drives primary classification:

| Suffix pattern | Tag |
|----------------|-----|
| `…000` | `[GME]` base game |
| `…800` | `[UPD]` update |
| Other / DLC heuristics | `[DLC]` |

Filename cleanup strips region tags, version noise, and shop labels. See
`tests/test_categorization.py` for the full rule set.

## Fuzzy folder matching

`GameOrganizer.similarity_threshold` is **70** (hardcoded). Requires
`rapidfuzz` at runtime; matching is disabled when the module is absent.

## Safety guardrails

- **Dry Mode** previews the full plan without moving files.
- **Protected directories** — bulk folder drops on Home, Desktop, `/`, or drive
  roots are rejected. Single game files can still be dropped.
- File moves stay inside the user-designated organize root.

## User data paths

| Path | Contents |
|------|----------|
| `~/Library/Application Support/RyuSync/settings.json` | Dry mode preference |
| `~/Library/Application Support/RyuSync/history/` | Past organize runs |
| `~/Library/Application Support/RyuSync/logs/` | File logs |

## Workers

`FolderProcessingWorker` extends `razorcore.threading.BaseWorker`. Cancel via
`stop()` → `request_cancel()`.

## razorcore setup

| Layout | Setup |
|--------|-------|
| Standalone clone | `uv sync` (CI uses `ci/vendor/` wheel) |
| Apps workspace | editable `../.razorcore` overlay |

See [ci/vendor/README.md](../ci/vendor/README.md).

## Testing

```bash
uv run pytest tests/ -q
```

Key modules: `test_categorization.py`, `test_drag_drop_safety.py`,
`test_sanitize_*.py`, `test_workers.py`.

## Related docs

- [BUILD_AND_RELEASE.md](../BUILD_AND_RELEASE.md)
- [docs/DMG_BUILD_README.md](DMG_BUILD_README.md)
- [CONTRIBUTING.md](../CONTRIBUTING.md)
