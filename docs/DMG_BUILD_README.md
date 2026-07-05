# RyuSync DMG Build

Shared reference: [Docs/dmg_build_guide.md](/Users/home/Workspace/Apps/Docs/dmg_build_guide.md)

## Canonical command

From `/Users/home/Workspace/Apps`:

```bash
uv run --project .razorcore razorbuild RyuSync
```

If `razorbuild` is already on your `PATH`:

```bash
razorbuild RyuSync
```

## Repo-specific inputs

- [RyuSync.spec](/Users/home/Workspace/Apps/RyuSync/RyuSync.spec)
- Bundled icons and images under [resources/](/Users/home/Workspace/Apps/RyuSync/resources/)

## Output

```text
dist/RyuSync.dmg
```

## Troubleshooting

- If packaging fails, inspect `RyuSync.spec` first — entry point, `datas`, and PySide6 hidden imports.
- If icons are missing at runtime, verify `resources/` paths match [app_resources.py](/Users/home/Workspace/Apps/RyuSync/src/ryusync/app_resources.py).
- Archive extraction requires `unar` on the target Mac (`brew install unar`); it is not bundled inside the app.
