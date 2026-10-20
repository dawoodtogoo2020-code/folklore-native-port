"""Portable entry point for the Folklore launcher.

This module is the script PyInstaller freezes into ``FolkloreLauncher.exe``.
It only normalizes the command line and hands control to
``folklore_launcher.main``; all launcher behavior lives in that module.

Rules:
- When no ``--config`` is supplied, the config defaults to ``settings.json``
  beside the running executable (frozen) or beside this file (source mode).
  The current working directory and ``_MEIPASS`` are never used for it.
- Explicit ``--config PATH`` / ``--config=PATH`` and every other argument are
  passed through unchanged and in order.
- Importing this module has no side effects on the filesystem.
- The launcher is run in-process; this entry never spawns or re-execs itself.
"""

import importlib
import os
import sys
from pathlib import Path

LAUNCHER_MODULE = "folklore_launcher"
CONFIG_FLAG = "--config"
CONFIG_FILENAME = "settings.json"

_ACTIVE = False


def is_frozen():
    """Return True when running from a PyInstaller-built executable."""
    return bool(getattr(sys, "frozen", False))


def base_directory(frozen=None, executable=None, entry_file=None):
    """Directory that portable defaults are resolved against.

    Frozen: the directory holding the launcher executable (sys.executable is
    the launcher itself, not a Python interpreter). Source: the directory
    holding this entry file.
    """
    if frozen is None:
        frozen = is_frozen()
    if frozen:
        anchor = executable if executable is not None else sys.executable
    else:
        anchor = entry_file if entry_file is not None else __file__
    return Path(os.path.abspath(os.fspath(anchor))).parent


def default_config_path(frozen=None, executable=None, entry_file=None):
    """Absolute path of the default portable settings file."""
    return base_directory(frozen, executable, entry_file) / CONFIG_FILENAME


def has_explicit_config(argv):
    """Return True if argv already carries a --config option."""
    for arg in argv:
        if arg == "--":
            return False
        if arg == CONFIG_FLAG or arg.startswith(CONFIG_FLAG + "="):
            return True
    return False


def normalize_argv(argv, frozen=None, executable=None, entry_file=None):
    """Return a new argv list with the portable default config applied.

    The default is prepended so every user-supplied argument keeps its
    original order and position.
    """
    args = [str(arg) for arg in argv]
    if has_explicit_config(args):
        return args
    config = default_config_path(frozen, executable, entry_file)
    return [CONFIG_FLAG, str(config)] + args


def _ensure_import_path():
    """In source mode make sibling launcher modules importable."""
    if is_frozen():
        return
    entry_dir = str(Path(os.path.abspath(__file__)).parent)
    if entry_dir not in sys.path:
        sys.path.insert(0, entry_dir)


def _ensure_std_streams():
    """Give a windowed (console=False) build usable stdout/stderr.

    A windowed executable starts with sys.stdout/sys.stderr set to None.
    When started from a terminal (e.g. ``--check`` in tests) attach to the
    parent console so output stays visible; otherwise discard output.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = None
    if sys.platform == "win32":
        try:
            import ctypes

            attach_parent_process = -1
            if ctypes.windll.kernel32.AttachConsole(attach_parent_process):
                stream = open("CONOUT$", "w", encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            stream = None
    if stream is None:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def _load_launcher():
    """Import the real launcher module lazily."""
    module = importlib.import_module(LAUNCHER_MODULE)
    if getattr(module, "__name__", None) == __name__ or module is sys.modules.get(__name__):
        raise RuntimeError("portable entry resolved to itself instead of the launcher")
    return module


def main(argv=None):
    """Run the Folklore launcher in-process with normalized arguments."""
    global _ACTIVE
    if _ACTIVE:
        raise RuntimeError("portable entry main() is already running")
    if argv is None:
        argv = sys.argv[1:]
    normalized = normalize_argv(argv)
    _ACTIVE = True
    try:
        if is_frozen():
            _ensure_std_streams()
        _ensure_import_path()
        launcher = _load_launcher()
        return launcher.main(normalized)
    finally:
        _ACTIVE = False


if __name__ == "__main__":
    sys.exit(main())
