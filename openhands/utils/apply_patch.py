#!/usr/bin/env python3
from __future__ import annotations

import enum
import json
import os
import pathlib
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional


# ============================================================
# Config
# ============================================================

JSON_MODE = (
    "--json" in sys.argv
    or os.environ.get("APPLY_PATCH_JSON") == "1"
)


# ============================================================
# Errors
# ============================================================

class PatchError(Exception):
    error_type = "internal_error"


class PatchParseError(PatchError):
    error_type = "parse_error"


class PatchApplyError(PatchError):
    error_type = "context_mismatch"


class AmbiguousHunkError(PatchApplyError):
    error_type = "ambiguous_hunk"


class NoOpError(PatchApplyError):
    error_type = "noop_update"


# ============================================================
# Models
# ============================================================

class PatchAction(enum.Enum):
    ADD = "ADD"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


@dataclass(frozen=True)
class DiffHunk:
    header: str
    before: List[str]
    removed: List[str]
    added: List[str]
    after: List[str]


@dataclass(frozen=True)
class PatchFile:
    action: PatchAction
    path: pathlib.Path
    diff_lines: List[str]


@dataclass(frozen=True)
class Patch:
    patch_id: Optional[str]
    files: List[PatchFile]


# ============================================================
# Utilities
# ============================================================

def emit(obj: dict, exit_code: int = 0):
    if JSON_MODE:
        print(json.dumps(obj, indent=2))
    else:
        if obj["status"] == "failed":
            print("apply_patch failed:", file=sys.stderr)
            for k, v in obj.items():
                if k not in ("status",):
                    print(f"{k}: {v}", file=sys.stderr)
        else:
            for a in obj.get("applied", []):
                print(f"✔ {a['action']} {a['file']}")
    sys.exit(exit_code)


def normalize_lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


# ============================================================
# Parsing
# ============================================================

def parse_patch(text: str) -> Patch:
    lines = normalize_lines(text)
    if not lines or not lines[0].startswith("*** Begin Patch"):
        raise PatchParseError("Missing *** Begin Patch")

    patch_id = None
    files: List[PatchFile] = []
    i = 1

    while i < len(lines):
        line = lines[i]

        if line.startswith("*** Patch-ID:"):
            patch_id = line.split(":", 1)[1].strip()
            i += 1
            continue

        if line.startswith("*** End Patch"):
            break

        if line.startswith("*** Add File:"):
            path = pathlib.Path(line.split(":", 1)[1].strip())
            i += 1
            content = []
            while i < len(lines) and not lines[i].startswith("***"):
                content.append(lines[i])
                i += 1
            files.append(PatchFile(PatchAction.ADD, path, content))
            continue

        if line.startswith("*** Update File:"):
            path = pathlib.Path(line.split(":", 1)[1].strip())
            i += 1
            diff = []
            while i < len(lines) and not lines[i].startswith("***"):
                diff.append(lines[i])
                i += 1
            files.append(PatchFile(PatchAction.UPDATE, path, diff))
            continue

        if line.startswith("*** Delete File:"):
            path = pathlib.Path(line.split(":", 1)[1].strip())
            files.append(PatchFile(PatchAction.DELETE, path, []))
            i += 1
            continue

        raise PatchParseError(f"Unexpected line: {line}")

    if not files:
        raise PatchParseError("No file actions found")

    return Patch(patch_id, files)


# ============================================================
# Diff handling
# ============================================================

def parse_hunks(diff_lines: List[str]) -> List[DiffHunk]:
    hunks = []
    i = 0

    while i < len(diff_lines):
        if not diff_lines[i].startswith("@@"):
            raise PatchParseError("Expected hunk header")
        header = diff_lines[i]
        i += 1

        before, removed, added, after = [], [], [], []
        seen_change = False

        while i < len(diff_lines) and not diff_lines[i].startswith("@@"):
            l = diff_lines[i]
            if l.startswith(" "):
                (after if seen_change else before).append(l[1:])
            elif l.startswith("-"):
                removed.append(l[1:])
                seen_change = True
            elif l.startswith("+"):
                added.append(l[1:])
                seen_change = True
            else:
                raise PatchParseError(f"Invalid diff line: {l}")
            i += 1

        hunks.append(DiffHunk(header, before, removed, added, after))

    return hunks


def find_matches(lines: List[str], hunk: DiffHunk) -> List[int]:
    pattern = hunk.before + hunk.removed + hunk.after
    matches = []
    for i in range(len(lines) - len(pattern) + 1):
        if lines[i : i + len(pattern)] == pattern:
            matches.append(i)
    return matches


def suggest(hunk: DiffHunk) -> List[str]:
    hints = []
    if len(hunk.before) < 3:
        hints.append("Add more leading context lines before the change")
    if len(hunk.after) < 3:
        hints.append("Add more trailing context lines after the change")
    hints.append("Include a function or class signature in the context")
    hints.append("Expand the hunk to include nearby unique statements")
    return hints


# ============================================================
# Apply logic
# ============================================================

def apply_update(path: pathlib.Path, original: List[str], diff: List[str]) -> List[str]:
    hunks = parse_hunks(diff)
    lines = original[:]
    offset = 0

    for h in hunks:
        matches = find_matches(lines, h)
        if not matches:
            raise PatchApplyError(f"Context mismatch in {path}")
        if len(matches) > 1:
            raise AmbiguousHunkError(
                json.dumps(
                    {
                        "file": str(path),
                        "hunk": h.header,
                        "matches": [m + 1 for m in matches],
                        "suggestions": suggest(h),
                    }
                )
            )

        idx = matches[0] + offset
        end = idx + len(h.before) + len(h.removed) + len(h.after)
        replacement = h.before + h.added + h.after
        lines[idx:end] = replacement
        offset += len(h.added) - len(h.removed)

    if lines == original:
        raise NoOpError(f"No-op update for {path}")

    return lines


def apply_patch(patch: Patch):
    cwd = pathlib.Path.cwd()
    staging = pathlib.Path(tempfile.mkdtemp(prefix="apply_patch_"))
    applied = []

    try:
        for pf in patch.files:
            src = cwd / pf.path
            dst = staging / pf.path

            if pf.action == PatchAction.ADD:
                if src.exists():
                    raise PatchApplyError("file_already_exists")
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("\n".join(pf.diff_lines) + "\n")

            elif pf.action == PatchAction.DELETE:
                if not src.exists():
                    raise PatchApplyError("file_not_found")

            elif pf.action == PatchAction.UPDATE:
                if not src.exists():
                    raise PatchApplyError("file_not_found")
                orig = normalize_lines(src.read_text())
                updated = apply_update(pf.path, orig, pf.diff_lines)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("\n".join(updated) + "\n")

            applied.append({"action": pf.action.value, "file": str(pf.path)})

        for pf in patch.files:
            src = cwd / pf.path
            dst = staging / pf.path
            if pf.action == PatchAction.DELETE:
                src.unlink()
            else:
                shutil.move(str(dst), str(src))

        emit(
            {
                "status": "success",
                "patch_id": patch.patch_id,
                "applied": applied,
            }
        )

    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ============================================================
# Entry
# ============================================================

def main():
    try:
        patch = parse_patch(sys.stdin.read())
        apply_patch(patch)
    except PatchError as e:
        emit(
            {
                "status": "failed",
                "patch_id": getattr(e, "patch_id", None),
                "error_type": e.error_type,
                "message": str(e),
            },
            exit_code=1,
        )


if __name__ == "__main__":
    main()
