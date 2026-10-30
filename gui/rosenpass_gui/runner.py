"""Helpers for locating and running external binaries."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

WIREGUARD_DIR = Path(r"C:\Program Files\WireGuard")

# Homebrew's sbin paths are not always on PATH (e.g. when the GUI is launched
# from Finder or an IDE), so probe the well-known locations directly. On
# Windows the official WireGuard installer puts wg.exe/wireguard.exe outside
# PATH, so probe the install directory as well.
EXTRA_PATHS = {
    "wg": ["/opt/homebrew/bin/wg", "/usr/local/bin/wg"],
    "wg-quick": ["/opt/homebrew/bin/wg-quick", "/usr/local/bin/wg-quick"],
    "wireguard": [],
}
if IS_WINDOWS:
    EXTRA_PATHS = {
        "wg": [str(WIREGUARD_DIR / "wg.exe")],
        "wg-quick": [],
        "wireguard": [str(WIREGUARD_DIR / "wireguard.exe")],
    }


def which(name: str) -> str | None:
    """Return the absolute path of *name*, probing known locations if needed."""
    if not IS_WINDOWS or not name.endswith(".exe"):
        found = shutil.which(name)
        if found:
            return found
    exe = name if name.endswith(".exe") else f"{name}.exe"
    for candidate in EXTRA_PATHS.get(name, []) + [str(WIREGUARD_DIR / exe)]:
        if Path(candidate).is_file() and _is_executable(candidate):
            return candidate
    return None


def _is_executable(path: str) -> bool:
    return Path(path).stat().st_mode & 0o111 != 0


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run *cmd*, capture output, never raise on non-zero exit."""
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    return subprocess.run(cmd, **kwargs)
