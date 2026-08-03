# Driver Patcher

A macOS GUI tool that patches legacy installer packages (`.pkg`) and disk images (`.dmg`) to run on modern macOS versions and Apple Silicon (arm64).

> **Disclaimer:** Driver Patcher is an independent, community-made tool. It is **not affiliated with, endorsed by, or supported by Apple Inc.** Patching modifies and strips code signatures from installer packages. Use entirely at your own risk. Always keep a backup of the original file.

---

## What it does

- Strips RSA and CMS code signatures from flat `.pkg` archives
- Adds `arm64` to the `hostArchitectures` attribute so installers run natively on Apple Silicon
- Neutralizes `InstallationCheck` scripts and OS version gates that block installation on modern macOS
- Supports both flat `.pkg` files and `.dmg` disk images containing packages
- Recalculates all xar checksums and heap offsets so the patched archive remains structurally valid

## Requirements

- macOS (uses system tools `hdiutil` and `osascript`)
- Python 3.10 or later
- PySide6

## Installation

```bash
# Recommended: use a virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Running

**Double-click** `Driver Patcher.command` in Finder — it will check for PySide6 and offer to install it if missing.

Or launch directly from the terminal:

```bash
python3 main.py
```

## Usage

1. Click **Browse…** and select a `.pkg` or `.dmg` file
2. Click **Analyze Package** to inspect the package metadata
3. Review the report — warnings flag version locks and architecture restrictions
4. Confirm the output path and click **Patch Package**
5. Optionally click **Install Patched Package** to install immediately (requires administrator password)

## Notes

- Patched packages are **unsigned**. macOS Gatekeeper may block them on first run. To install, you may need to right-click → Open, or allow the install in **System Settings → Privacy & Security**.
- The `Driver Patcher.command` launcher installs PySide6 globally if you choose to. Using a virtual environment (see above) keeps your system Python clean.
- DMG patching mounts the source image read-only, patches all embedded packages in a temporary directory, then builds a new compressed DMG with `hdiutil`.

## License

MIT — see [LICENSE](LICENSE).
