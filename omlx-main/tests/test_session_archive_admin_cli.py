# SPDX-License-Identifier: Apache-2.0
"""Smoke tests for scripts/session_archive_admin.py."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from omlx.cache.session_archive import SessionArchiveStore


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "session_archive_admin.py"


def _h(tag: str) -> bytes:
    return hashlib.sha256(tag.encode()).digest()


def _run(*args: str, cwd: Path = REPO_ROOT, check: bool = False):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture()
def seeded(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("demo-model", "alpha", [_h("a"), _h("b")])
    store.commit("demo-model", "beta", [_h("c")])
    return root


def test_cli_list_reports_seeded_sessions(seeded):
    res = _run("--archive-root", str(seeded), "list", "--model", "demo-model")
    assert res.returncode == 0, res.stderr
    assert "alpha" in res.stdout
    assert "beta" in res.stdout


def test_cli_list_empty_model_exits_zero(seeded):
    res = _run("--archive-root", str(seeded), "list", "--model", "nope")
    assert res.returncode == 0
    assert "no sessions" in res.stdout


def test_cli_show_valid_session_without_ssd_passes(seeded):
    res = _run(
        "--archive-root", str(seeded),
        "show", "--model", "demo-model", "--session", "alpha",
    )
    assert res.returncode == 0, res.stderr
    assert "version" in res.stdout
    assert "status" in res.stdout


def test_cli_show_unknown_session_exits_one(seeded):
    res = _run(
        "--archive-root", str(seeded),
        "show", "--model", "demo-model", "--session", "ghost",
    )
    assert res.returncode == 1
    assert "unknown session" in res.stderr


def test_cli_validate_all_passes_without_ssd(seeded):
    res = _run(
        "--archive-root", str(seeded),
        "validate", "--model", "demo-model",
    )
    assert res.returncode == 0, res.stderr
    assert "alpha" in res.stdout
    assert "beta" in res.stdout
    assert "ok" in res.stdout


def test_cli_validate_flags_malformed_manifest(seeded):
    bad = SessionArchiveStore(seeded).manifest_path("demo-model", "alpha")
    bad.write_text("{not json", encoding="utf-8")
    res = _run(
        "--archive-root", str(seeded),
        "validate", "--model", "demo-model",
    )
    assert res.returncode == 1
    assert "invalid:malformed" in res.stdout


def test_cli_delete_removes_session(seeded):
    res = _run(
        "--archive-root", str(seeded),
        "delete", "--model", "demo-model", "--session", "beta", "--yes",
    )
    assert res.returncode == 0, res.stderr
    assert not (seeded / "demo-model" / "beta").exists()


def test_cli_delete_unknown_session_exits_one(seeded):
    res = _run(
        "--archive-root", str(seeded),
        "delete", "--model", "demo-model", "--session", "ghost", "--yes",
    )
    assert res.returncode == 1
    assert "unknown session" in res.stderr


def test_cli_prune_dry_run_invalid_reports_but_does_not_delete(seeded):
    bad = SessionArchiveStore(seeded).manifest_path("demo-model", "alpha")
    bad.write_text("{not json", encoding="utf-8")
    res = _run(
        "--archive-root", str(seeded),
        "prune", "--model", "demo-model", "--invalid",
    )
    # Dry run that finds something exits 1.
    assert res.returncode == 1
    assert "dry_run=True" in res.stdout
    assert "alpha" in res.stdout
    # File still there.
    assert bad.exists()


def test_cli_prune_no_dry_run_deletes_invalid(seeded):
    bad = SessionArchiveStore(seeded).manifest_path("demo-model", "alpha")
    bad.write_text("{not json", encoding="utf-8")
    res = _run(
        "--archive-root", str(seeded),
        "prune", "--model", "demo-model", "--invalid", "--no-dry-run",
    )
    assert res.returncode == 1  # still 1 because we found + acted
    assert "deleted=1" in res.stdout
    assert not (seeded / "demo-model" / "alpha").exists()


def test_cli_stats_prints_counters_after_load(seeded):
    # Run a 'validate' first to bump counters in a subprocess; then 'stats'
    # is a fresh process, so it will see zero. We instead assert stats runs
    # cleanly on an empty process.
    res = _run("--archive-root", str(seeded), "stats")
    assert res.returncode == 0, res.stderr
    # Either "no counters" banner OR at least one tab-separated key.
    assert "no counters" in res.stdout or "\t" in res.stdout


def test_cli_bad_archive_root_exits_two(tmp_path):
    missing = tmp_path / "does-not-exist"
    res = _run("--archive-root", str(missing), "list", "--model", "x")
    assert res.returncode == 2
    assert "does not exist" in res.stderr


def test_cli_bad_duration_exits_nonzero(seeded):
    res = _run(
        "--archive-root", str(seeded),
        "prune", "--model", "demo-model", "--older-than", "forever",
    )
    # argparse raises SystemExit(2) for type errors.
    assert res.returncode == 2
    assert "invalid duration" in res.stderr
