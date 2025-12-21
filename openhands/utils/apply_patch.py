#!/usr/bin/env python3
from __future__ import annotations

"""
Patch application CLI used by the CodeAct agent and developer tooling.

Ensure the repository root is on ``PYTHONPATH`` (or the package is installed)
before invoking via ``python -m openhands.utils.apply_patch``.
"""

import enum
import json
import os
import pathlib
import shutil
import sys
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple


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


class PatchPermissionError(PatchApplyError):
    error_type = "permission_error"


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
    original: List[str]
    replacement: List[str]
    leading_context: List[str]
    trailing_context: List[str]


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


def format_apply_patch_observation(patch_text: str, result: dict) -> str:
    formatted_patch = "\n".join(
        f"> {line}" for line in normalize_lines(patch_text.rstrip("\n"))
    )

    header = "apply_patch <<'PATCH'"
    body_lines = [header]
    if formatted_patch:
        body_lines.append(formatted_patch)
    body_lines.append("PATCH")

    status = result.get("status") if result else None
    result_lines: List[str] = []

    if status == "success":
        result_lines.append("Result: success")
        for applied in result.get("applied", []):
            action = applied.get("action", "?")
            file = applied.get("file", "")
            result_lines.append(f"✔ {action} {file}".rstrip())
    elif status == "failed":
        result_lines.append("Result: failed")
        error_type = result.get("error_type")
        message = result.get("message")
        if error_type:
            result_lines.append(f"error_type: {error_type}")
        if message:
            result_lines.append(f"message: {message}")
    elif result:
        # Fallback: render the raw payload if we don't recognize the shape
        result_lines.append(json.dumps(result, indent=2))

    if result_lines:
        body_lines.append("")
        body_lines.extend(result_lines)

    return "\n".join(body_lines)


def normalize_lines(text: str) -> List[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def resolve_patch_path(
    raw_path: pathlib.Path, workspace_root: pathlib.Path
) -> Tuple[pathlib.Path, pathlib.Path]:
    workspace_root = workspace_root.resolve()
    candidate = raw_path if raw_path.is_absolute() else workspace_root / raw_path
    resolved = candidate.resolve()

    try:
        relative = resolved.relative_to(workspace_root)
    except ValueError:
        raise PatchPermissionError(f"Invalid path outside workspace: {raw_path}")

    return resolved, relative


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

    end_index = None

    while i < len(lines):
        line = lines[i]

        if line.startswith("*** Patch-ID:"):
            patch_id = line.split(":", 1)[1].strip()
            i += 1
            continue

        if line.startswith("*** End Patch"):
            end_index = i
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

    if end_index is None:
        raise PatchParseError("Missing *** End Patch")

    trailing = [ln for ln in lines[end_index + 1 :] if ln.strip()]
    if trailing:
        raise PatchParseError("Trailing content after *** End Patch")

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

        original, replacement, leading_context = [], [], []
        trailing_buffer: List[str] = []
        seen_change = False

        while i < len(diff_lines) and not diff_lines[i].startswith("@@"):
            l = diff_lines[i]
            if l.startswith(" "):
                text = l[1:]
                original.append(text)
                replacement.append(text)
                if not seen_change:
                    leading_context.append(text)
                trailing_buffer.append(text)
            elif l.startswith("-"):
                text = l[1:]
                original.append(text)
                seen_change = True
                trailing_buffer = []
            elif l.startswith("+"):
                text = l[1:]
                replacement.append(text)
                seen_change = True
                trailing_buffer = []
            else:
                raise PatchParseError(f"Invalid diff line: {l}")
            i += 1

        if not seen_change:
            raise PatchParseError("Hunk contains no changes")

        hunks.append(
            DiffHunk(
                header,
                original,
                replacement,
                leading_context,
                trailing_buffer,
            )
        )

    return hunks


def find_matches(lines: List[str], hunk: DiffHunk) -> List[int]:
    pattern = hunk.original
    matches = []
    for i in range(len(lines) - len(pattern) + 1):
        if lines[i : i + len(pattern)] == pattern:
            matches.append(i)
    return matches


def suggest(hunk: DiffHunk) -> List[str]:
    hints = []
    if len(hunk.leading_context) < 3:
        hints.append("Add more leading context lines before the change")
    if len(hunk.trailing_context) < 3:
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

        idx = matches[0]
        end = idx + len(h.original)
        replacement = h.replacement
        lines[idx:end] = replacement

    if lines == original:
        raise NoOpError(f"No-op update for {path}")

    return lines



def apply_patch(
    patch: Patch, workspace_root: pathlib.Path | str | None = None
) -> dict:
    workspace = (
        pathlib.Path(workspace_root) if workspace_root is not None else pathlib.Path.cwd()
    ).resolve()
    staging = pathlib.Path(tempfile.mkdtemp(prefix="apply_patch_"))
    applied = []
    resolved_files: List[tuple[PatchFile, pathlib.Path, pathlib.Path]] = []

    try:
        for pf in patch.files:
            target_path, relative_path = resolve_patch_path(pf.path, workspace)
            resolved_files.append((pf, target_path, relative_path))
            dst = staging / relative_path

            if pf.action == PatchAction.ADD:
                if target_path.exists():
                    raise PatchApplyError("file_already_exists")
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("\n".join(pf.diff_lines) + "\n")

            elif pf.action == PatchAction.DELETE:
                if not target_path.exists():
                    raise PatchApplyError("file_not_found")

            elif pf.action == PatchAction.UPDATE:
                if not target_path.exists():
                    raise PatchApplyError("file_not_found")
                orig = normalize_lines(target_path.read_text())
                updated = apply_update(pf.path, orig, pf.diff_lines)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_text("\n".join(updated) + "\n")

            applied.append({"action": pf.action.value, "file": str(pf.path)})

        for pf, target_path, relative_path in resolved_files:
            dst = staging / relative_path
            if pf.action == PatchAction.DELETE:
                target_path.unlink()
            else:
                shutil.move(str(dst), str(target_path))

        return {
            "status": "success",
            "patch_id": patch.patch_id,
            "applied": applied,
        }

    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ============================================================
# Entry
# ============================================================

def main():
    patch = None
    try:
        patch = parse_patch(sys.stdin.read())
        emit(apply_patch(patch))
    except PatchError as e:
        if patch is not None and not getattr(e, "patch_id", None):
            e.patch_id = patch.patch_id
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
