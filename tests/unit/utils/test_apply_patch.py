import json
import os
import pathlib
import subprocess
import sys
import textwrap

SCRIPT_PATH = pathlib.Path(__file__).resolve().parents[3] / "openhands" / "utils" / "apply_patch.py"


def run_apply_patch(tmp_path: pathlib.Path, patch: str, json_mode: bool = False):
    env = os.environ.copy()
    if json_mode:
        env["APPLY_PATCH_JSON"] = "1"
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=patch,
        text=True,
        cwd=tmp_path,
        capture_output=True,
        env=env,
    )


def write(tmp_path: pathlib.Path, rel: str, content: str):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def read(tmp_path: pathlib.Path, rel: str) -> str:
    return (tmp_path / rel).read_text()


def test_add_file(tmp_path: pathlib.Path):
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: add-file
        *** Add File: foo.txt
        hello
        world
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch)

    assert proc.returncode == 0
    assert (tmp_path / "foo.txt").exists()
    assert read(tmp_path, "foo.txt") == "hello\nworld\n"


def test_delete_file(tmp_path: pathlib.Path):
    write(tmp_path, "foo.txt", "keep me\n")
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: delete-file
        *** Delete File: foo.txt
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch)

    assert proc.returncode == 0
    assert not (tmp_path / "foo.txt").exists()


def test_update_file(tmp_path: pathlib.Path):
    write(tmp_path, "foo.py", "a = 1\nb = 2\n")
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: update-file
        *** Update File: foo.py
        @@
         a = 1
        -b = 2
        +b = 3
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch)

    assert proc.returncode == 0
    assert read(tmp_path, "foo.py") == "a = 1\nb = 3\n"


def test_multiple_hunks(tmp_path: pathlib.Path):
    write(tmp_path, "foo.py", "a = 1\nb = 2\nc = 3\n")

    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: multi-hunk
        *** Update File: foo.py
        @@
         a = 1
        -b = 2
        +b = 20
        @@
         b = 20
        -c = 3
        +c = 30
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch)

    assert proc.returncode == 0
    assert read(tmp_path, "foo.py") == "a = 1\nb = 20\nc = 30\n"


def test_missing_begin_patch(tmp_path: pathlib.Path):
    proc = run_apply_patch(tmp_path, "*** Update File: foo.py\n")

    assert proc.returncode != 0
    assert "Begin Patch" in proc.stderr


def test_trailing_garbage(tmp_path: pathlib.Path):
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Add File: foo.txt
        hi
        *** End Patch
        garbage
        """
    )

    proc = run_apply_patch(tmp_path, patch)

    assert proc.returncode != 0
    assert "Trailing content" in proc.stderr


def test_context_mismatch(tmp_path: pathlib.Path):
    write(tmp_path, "foo.py", "a = 1\n")
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Update File: foo.py
        @@
        -missing = 2
        +a = 2
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch)

    assert proc.returncode != 0
    assert "context mismatch" in proc.stderr.lower()


def test_ambiguous_hunk_json(tmp_path: pathlib.Path):
    write(tmp_path, "foo.py", "alpha\nbeta\ngamma\nalpha\nbeta\ngamma\n")
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: ambiguous
        *** Update File: foo.py
        @@
         alpha
        -beta
        +beta2
         gamma
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch, json_mode=True)
    payload = json.loads(proc.stdout)

    assert proc.returncode != 0
    assert payload["status"] == "failed"
    assert payload["error_type"] == "ambiguous_hunk"
    assert payload["patch_id"] == "ambiguous"
    assert "suggestions" in payload["message"]


def test_noop_update_json(tmp_path: pathlib.Path):
    write(tmp_path, "foo.txt", "hello\n")
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: noop
        *** Update File: foo.txt
        @@
        -hello
        +hello
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch, json_mode=True)
    payload = json.loads(proc.stdout)

    assert proc.returncode != 0
    assert payload["status"] == "failed"
    assert payload["patch_id"] == "noop"
    assert payload["error_type"] == "noop_update"


def test_atomicity_on_failure(tmp_path: pathlib.Path):
    write(tmp_path, "good.txt", "ok\n")
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: atomicity
        *** Add File: new.txt
        hello
        *** Update File: good.txt
        @@
        -missing
        +replacement
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch)

    assert proc.returncode != 0
    assert not (tmp_path / "new.txt").exists()
    assert read(tmp_path, "good.txt") == "ok\n"


def test_patch_id_propagates_on_failure(tmp_path: pathlib.Path):
    write(tmp_path, "foo.txt", "hello\n")
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Patch-ID: propagate
        *** Update File: foo.txt
        @@
        -missing
        +replacement
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch, json_mode=True)
    payload = json.loads(proc.stdout)

    assert proc.returncode != 0
    assert payload["patch_id"] == "propagate"
    assert payload["status"] == "failed"


def test_rejects_relative_escape(tmp_path: pathlib.Path):
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Add File: ../escape.txt
        sneaky
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch, json_mode=True)
    payload = json.loads(proc.stdout)

    assert proc.returncode != 0
    assert payload["error_type"] == "permission_error"
    assert "outside workspace" in payload["message"]
    assert not (tmp_path.parent / "escape.txt").exists()


def test_rejects_absolute_escape(tmp_path: pathlib.Path):
    patch = textwrap.dedent(
        """\
        *** Begin Patch
        *** Add File: /etc/passwd
        nope
        *** End Patch
        """
    )

    proc = run_apply_patch(tmp_path, patch, json_mode=True)
    payload = json.loads(proc.stdout)

    assert proc.returncode != 0
    assert payload["error_type"] == "permission_error"
    assert "outside workspace" in payload["message"]
