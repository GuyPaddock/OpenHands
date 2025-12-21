#!/usr/bin/env python3
"""
Helper wrapper for the apply_patch CLI that works even when the
`openhands` package is not installed into the active Python environment.

Usage:
    python scripts/apply_patch.py < patch.txt

This script adds the repository root to ``sys.path`` before delegating to
``openhands.utils.apply_patch`` so you can run it from any directory as
long as the OpenHands checkout is available.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT))
    runpy.run_module("openhands.utils.apply_patch", run_name="__main__")
