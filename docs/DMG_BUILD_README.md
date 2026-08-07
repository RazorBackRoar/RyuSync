# RyuSync DMG Build

## Quick build

From the RyuSync repository root:

```bash
razorbuild RyuSync
# Output: dist/RyuSync.dmg
```

## Repo-specific inputs

| Path | Purpose |
|------|---------|
| `RyuSync.spec` | PyInstaller entry point, `datas`, PySide6 hidden imports |
| `resources/` | Bundled icons and images |
| `src/ryusync/app_resources.py` | Runtime asset resolution (absolute imports required) |

## Output

```text
dist/RyuSync.dmg
```

## Troubleshooting

| Symptom | What to try |
|---------|-------------|
| Packaging fails | Inspect `RyuSync.spec` — entry point, `datas`, hidden imports |
| Missing icons at runtime | Verify `resources/` paths match `app_resources.py` |
| Archive extraction fails on user Mac | `unar` is not bundled — `brew install unar` |
| `razorcore` not found locally | Standalone: `uv sync` + `ci/vendor/`; workspace: `../.razorcore` |

## Related docs

- [BUILD_AND_RELEASE.md](../BUILD_AND_RELEASE.md)
- [docs/ARCHITECTURE.md](ARCHITECTURE.md)
