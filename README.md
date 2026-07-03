# RyuSync

<!-- Workspace Health Layer -->
![Status](https://img.shields.io/badge/status-unintegrated-yellow)
![Python](https://img.shields.io/badge/python-legacy-yellow)
![Packaging](https://img.shields.io/badge/packaging-requirements.txt-yellow)
![Action](https://img.shields.io/badge/action-deferred-gray)

A macOS drag-and-drop organizer for Nintendo Switch game files (`.nsp` / `.xci`).

## What it does

- Drag a game file, folder, or archive (`.rar`, `.zip`, `.7z`) onto the window
- RyuSync organizes the contents into a clean folder structure, tagging each file as `[GME]`, `[UPD]`, or `[DLC]` based on its title ID
- Archives are extracted in place using `unar`; the original archive is preserved
- **Dry Mode** previews what would happen without touching any files

## Requirements

- macOS (Apple Silicon or Intel)
- [`unar`](https://theunarchiver.com/command-line) for archive extraction: `brew install unar`

## Usage

1. Open RyuSync
2. Toggle **Dry Mode** on to preview, or leave it off to organize
3. Drag a file, folder, or archive onto the window

## Disclaimer

RyuSync is a file organization utility. It does not download, decrypt, stream, or distribute game files, and it does not circumvent any technical protection measures. Users are solely responsible for ensuring they have the legal right to possess any files they organize with this software.

## License

MIT
