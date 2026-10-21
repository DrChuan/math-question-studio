from __future__ import annotations

import os
from pathlib import Path
import sys


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def configure_runtime(root: Path | None = None) -> Path:
    root = root or application_directory()
    # Keep TeX private to this process; never modify the user's system PATH.
    tex_bin = root / "runtime" / "TinyTeX" / "bin" / "windows"
    if tex_bin.is_dir():
        os.environ["PATH"] = str(tex_bin) + os.pathsep + os.environ.get("PATH", "")
    return root
