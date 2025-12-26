from __future__ import annotations

from dataclasses import dataclass
import re
import subprocess
from pathlib import Path
from typing import Iterable, Sequence


class StageHunkError(Exception):
    """Errors raised while staging hunks."""


@dataclass
class HunkLine:
    index: int
    kind: str
    content: str
    old_lineno: int | None
    new_lineno: int | None


@dataclass
class FileHunk:
    hunk_id: str
    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    section: str
    lines: list[HunkLine]


@dataclass
class FileDiff:
    path: Path
    hunks: list[FileHunk]


def _run_git_diff(repo_root: Path) -> str:
    proc = subprocess.run(
        ['git', 'diff', '--no-color', '--unified=5'],
        cwd=repo_root,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise StageHunkError(proc.stderr.strip() or 'git diff failed')
    return proc.stdout


def _parse_diff_output(diff_output: str) -> list[FileDiff]:
    diffs: list[FileDiff] = []
    current_diff: FileDiff | None = None
    current_hunk: FileHunk | None = None
    old_lineno = 0
    new_lineno = 0
    hunk_index = 0

    hunk_pattern = re.compile(r'^@@ -(?P<old_start>\d+),(?P<old_count>\d+) \+(?P<new_start>\d+),(?P<new_count>\d+) @@(?P<section>.*)$')

    for line in diff_output.splitlines():
        if line.startswith('diff --git '):
            parts = line.split()
            a_path = parts[2][2:]
            b_path = parts[3][2:]
            path_str = b_path if b_path != '/dev/null' else a_path
            current_diff = FileDiff(path=Path(path_str), hunks=[])
            diffs.append(current_diff)
            hunk_index = 0
            continue

        if current_diff is None:
            continue

        if line.startswith('@@ '):
            match = hunk_pattern.match(line)
            if not match:
                raise StageHunkError(f'Unable to parse hunk header: {line}')

            old_start = int(match.group('old_start'))
            old_count = int(match.group('old_count'))
            new_start = int(match.group('new_start'))
            new_count = int(match.group('new_count'))
            section = match.group('section').strip()

            current_hunk = FileHunk(
                hunk_id=f'{current_diff.path}::hunk-{hunk_index}',
                header=line,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                section=section,
                lines=[],
            )
            current_diff.hunks.append(current_hunk)
            old_lineno = old_start
            new_lineno = new_start
            hunk_index += 1
            continue

        if current_hunk is None:
            continue

        if line.startswith(' '):
            current_hunk.lines.append(
                HunkLine(
                    index=len(current_hunk.lines) + 1,
                    kind='context',
                    content=line[1:],
                    old_lineno=old_lineno,
                    new_lineno=new_lineno,
                )
            )
            old_lineno += 1
            new_lineno += 1
        elif line.startswith('+'):
            current_hunk.lines.append(
                HunkLine(
                    index=len(current_hunk.lines) + 1,
                    kind='add',
                    content=line[1:],
                    old_lineno=None,
                    new_lineno=new_lineno,
                )
            )
            new_lineno += 1
        elif line.startswith('-'):
            current_hunk.lines.append(
                HunkLine(
                    index=len(current_hunk.lines) + 1,
                    kind='del',
                    content=line[1:],
                    old_lineno=old_lineno,
                    new_lineno=None,
                )
            )
            old_lineno += 1
        elif line.startswith('\\'):
            # Preserve trailing \ No newline annotations as context
            current_hunk.lines.append(
                HunkLine(
                    index=len(current_hunk.lines) + 1,
                    kind='context',
                    content=line,
                    old_lineno=None,
                    new_lineno=None,
                )
            )

    return diffs


def gather_available_hunks(repo_root: Path) -> list[FileDiff]:
    diff_output = _run_git_diff(repo_root)
    return _parse_diff_output(diff_output)


def _normalize_selection_path(selection_path: str, repo_root: Path) -> Path:
    candidate = Path(selection_path)
    resolved = candidate if candidate.is_absolute() else repo_root / candidate
    resolved = resolved.resolve()
    try:
        return resolved.relative_to(repo_root)
    except ValueError as exc:  # pragma: no cover - safety guard
        raise StageHunkError(
            f'Selection path {selection_path} is outside of repository root {repo_root}'
        ) from exc


def _coerce_selections(
    selections: Sequence[object], repo_root: Path
) -> list[tuple[Path, str, set[int] | None]]:
    normalized: list[tuple[Path, str, set[int] | None]] = []
    for selection in selections:
        if isinstance(selection, dict):
            file_value = selection.get('file')
            hunk_id = selection.get('hunk_id')
            include_lines = selection.get('include_lines')
        else:
            file_value = getattr(selection, 'file', None)
            hunk_id = getattr(selection, 'hunk_id', None)
            include_lines = getattr(selection, 'include_lines', None)

        if file_value is None or hunk_id is None:
            raise StageHunkError('Each selection must include file and hunk_id')

        rel_path = _normalize_selection_path(str(file_value), repo_root)

        if include_lines is None:
            normalized.append((rel_path, str(hunk_id), None))
            continue

        if not isinstance(include_lines, Iterable):
            raise StageHunkError('include_lines must be an iterable of integers')

        lines_set: set[int] = set()
        for item in include_lines:
            if not isinstance(item, int):
                raise StageHunkError('include_lines must only contain integers')
            if item < 1:
                raise StageHunkError('include_lines entries must be 1-based positive integers')
            lines_set.add(item)

        normalized.append((rel_path, str(hunk_id), lines_set))

    return normalized


def _build_patch_for_file(
    file_path: Path, selected_hunks: list[tuple[FileHunk, set[int] | None]]
) -> list[str]:
    if not selected_hunks:
        return []

    patch_lines = [
        f'diff --git a/{file_path} b/{file_path}',
        f'--- a/{file_path}',
        f'+++ b/{file_path}',
    ]

    for hunk, include_lines in selected_hunks:
        chosen_lines = include_lines or {
            line.index for line in hunk.lines if line.kind in {'add', 'del'}
        }
        context_indexes = {line.index for line in hunk.lines if line.kind == 'context'}
        scope_lines = chosen_lines.union(context_indexes)

        filtered_lines: list[tuple[str, str]] = []
        for line in hunk.lines:
            if line.kind == 'context' or line.index in chosen_lines:
                prefix = ' '
                if line.kind == 'add':
                    prefix = '+'
                elif line.kind == 'del':
                    prefix = '-'
                filtered_lines.append((prefix, line.content))

        if not filtered_lines:
            continue

        old_candidates = [
            line.old_lineno
            for line in hunk.lines
            if line.kind in {'context', 'del'} and line.index in scope_lines
        ]
        new_candidates = [
            line.new_lineno
            for line in hunk.lines
            if line.kind in {'context', 'add'} and line.index in scope_lines
        ]

        old_start = next((ln for ln in old_candidates if ln is not None), hunk.old_start)
        new_start = next((ln for ln in new_candidates if ln is not None), hunk.new_start)

        old_count = sum(1 for prefix, _ in filtered_lines if prefix in {' ', '-'})
        new_count = sum(1 for prefix, _ in filtered_lines if prefix in {' ', '+'})
        section_suffix = f' {hunk.section}' if hunk.section else ''

        patch_lines.append(
            f'@@ -{old_start},{old_count} +{new_start},{new_count} @@{section_suffix}'
        )
        patch_lines.extend([f'{prefix}{content}' for prefix, content in filtered_lines])

    return patch_lines


def stage_selected_hunks(
    repo_root: Path, selections: Sequence[object]
) -> tuple[list[dict], list[FileDiff]]:
    available_hunks = gather_available_hunks(repo_root)
    if not selections:
        return [], available_hunks

    normalized_selections = _coerce_selections(selections, repo_root)
    file_lookup = {diff.path: diff for diff in available_hunks}

    staged: list[dict] = []
    patch_sections: list[str] = []
    selections_by_file: dict[Path, list[tuple[FileHunk, set[int] | None]]] = {}

    for rel_path, hunk_id, include_lines in normalized_selections:
        diff = file_lookup.get(rel_path)
        if diff is None:
            raise StageHunkError(f'File {rel_path} has no unstaged changes')

        hunk = next((h for h in diff.hunks if h.hunk_id == hunk_id), None)
        if hunk is None:
            raise StageHunkError(
                f'Hunk {hunk_id} for {rel_path} not found in unstaged changes'
            )

        selections_by_file.setdefault(rel_path, []).append((hunk, include_lines))
        staged.append(
            {
                'file': str(rel_path),
                'hunk_id': hunk_id,
                'include_lines': sorted(include_lines) if include_lines else None,
            }
        )

    for file_path, file_hunks in selections_by_file.items():
        patch_sections.extend(_build_patch_for_file(file_path, file_hunks))

    if not patch_sections:
        return staged, gather_available_hunks(repo_root)

    patch_text = '\n'.join(patch_sections) + '\n'
    proc = subprocess.run(
        ['git', 'apply', '--cached', '--whitespace=nowarn'],
        cwd=repo_root,
        text=True,
        input=patch_text,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise StageHunkError(proc.stderr.strip() or proc.stdout.strip())

    return staged, gather_available_hunks(repo_root)


def serialize_available_hunks(
    available: list[FileDiff], repo_root: Path | None = None
) -> list[dict]:
    serialized: list[dict] = []
    for diff in available:
        file_path = Path(diff.path)
        if repo_root is not None:
            file_path = (repo_root / file_path).resolve()
        serialized.append(
            {
                'file': str(file_path),
                'hunks': [
                    {
                        'hunk_id': hunk.hunk_id,
                        'header': hunk.header,
                        'section': hunk.section,
                        'old_start': hunk.old_start,
                        'old_count': hunk.old_count,
                        'new_start': hunk.new_start,
                        'new_count': hunk.new_count,
                        'lines': [
                            {
                                'index': line.index,
                                'type': line.kind,
                                'old_lineno': line.old_lineno,
                                'new_lineno': line.new_lineno,
                                'content': line.content,
                            }
                            for line in hunk.lines
                        ],
                    }
                    for hunk in diff.hunks
                ],
            }
        )
    return serialized

