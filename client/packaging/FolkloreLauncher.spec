# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the portable, single-file Folklore launcher.
#
# Build from any directory with:
#     pyinstaller packaging/FolkloreLauncher.spec
#
# Bundles only developer-owned launcher code and artwork plus Tk and Pillow.
# No game executables, game metadata, user settings, credentials or saves are
# included, and no absolute developer paths are hard-coded: everything is
# resolved from this spec's location.

import os
from importlib.util import find_spec
from pathlib import Path

REPO_ROOT = Path(SPECPATH).resolve().parent
LAUNCHER_DIR = REPO_ROOT / "launcher"
ART_DIR = LAUNCHER_DIR / "art"
ENTRY_SCRIPT = LAUNCHER_DIR / "portable_entry.py"

LAUNCHER_MODULES = [
    "folklore_launcher",
    "launcher_core",
    "launcher_build",
    "launcher_display",
    "launcher_theme",
    "backup_validation",
]

EXCLUDED_NAMES = {
    "settings.json",
    "credentials.json",
    "secrets.json",
    ".env",
    "thumbs.db",
    "desktop.ini",
}
EXCLUDED_SUFFIXES = {
    ".exe",
    ".dll",
    ".sav",
    ".save",
    ".bak",
    ".log",
    ".pyc",
    ".pyo",
    ".pem",
    ".key",
    ".pfx",
}
EXCLUDED_DIRS = {"__pycache__", "saves", "backups"}

if not ENTRY_SCRIPT.is_file():
    raise SystemExit("missing portable entry: launcher/portable_entry.py")
for _module in LAUNCHER_MODULES:
    if not (LAUNCHER_DIR / (_module + ".py")).is_file():
        raise SystemExit("missing launcher module: launcher/%s.py" % _module)
if not ART_DIR.is_dir():
    raise SystemExit("missing launcher artwork directory: launcher/art")
if find_spec("PIL") is None:
    raise SystemExit("Pillow must be installed in the packaging environment")
if find_spec("tkinter") is None:
    raise SystemExit("tkinter must be available in the packaging environment")


def _art_datas():
    """Collect launcher artwork (PNGs, thumbs, ...) as (source, dest_dir)."""
    datas = []
    for root, dirs, files in os.walk(ART_DIR):
        dirs[:] = sorted(
            d for d in dirs
            if not d.startswith(".") and d.lower() not in EXCLUDED_DIRS
        )
        for filename in sorted(files):
            lowered = filename.lower()
            if filename.startswith(".") or lowered in EXCLUDED_NAMES:
                continue
            if os.path.splitext(lowered)[1] in EXCLUDED_SUFFIXES:
                continue
            source = Path(root) / filename
            dest_dir = source.parent.relative_to(LAUNCHER_DIR)
            datas.append((str(source), dest_dir.as_posix()))
    if not datas:
        raise SystemExit("launcher/art contains no bundleable artwork")
    return datas


a = Analysis(
    [str(ENTRY_SCRIPT)],
    pathex=[str(LAUNCHER_DIR)],
    binaries=[],
    datas=_art_datas(),
    hiddenimports=LAUNCHER_MODULES + [
        "tkinter",
        "tkinter.ttk",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "PIL",
        "PIL.Image",
        "PIL.ImageTk",
        "PIL._tkinter_finder",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FolkloreLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
