# SPDX-License-Identifier: Apache-2.0
"""Workspace-lineage contract tests.

Covers the reframe: ``init_workspace``, ``ancestry_chain``, CLI
``create`` / ``status`` / ``resume``, argument aliases on older
subcommands, and the ``--expected-block-size`` import guard.

Metadata-only; no SSD / scheduler coupling.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from omlx.cache.session_archive import (
    SessionArchiveError,
    SessionArchiveStore,
    ancestry_chain,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "scripts" / "session_archive_admin.py"


def _h(tag: str) -> bytes:
    return hashlib.sha256(tag.encode()).digest()


def _run(*args: str):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )


# ---------------------------------------------------------------------------
# init_workspace
# ---------------------------------------------------------------------------
def test_init_workspace_creates_empty_manifest(tmp_path):
    store = SessionArchiveStore(tmp_path / "archive")
    store.init_workspace(
        "m", "ws-1", label="hello", description="d", block_size=16
    )
    path = store.manifest_path("m", "ws-1")
    assert path.exists()
    doc = json.loads(path.read_text("utf-8"))
    assert doc["version"] == "2"
    assert doc["turns"] == []
    assert doc["head_turn_id"] == ""
    assert doc["label"] == "hello"
    assert doc["description"] == "d"
    assert doc["model_compat"]["block_size"] == 16
    assert doc["parent"] is None


def test_init_workspace_refuses_existing(tmp_path):
    store = SessionArchiveStore(tmp_path / "archive")
    store.init_workspace("m", "ws-1")
    with pytest.raises(SessionArchiveError, match="already exists"):
        store.init_workspace("m", "ws-1")


def test_init_workspace_then_commit_populates_head(tmp_path):
    store = SessionArchiveStore(tmp_path / "archive")
    store.init_workspace("m", "ws-1", label="first")
    # load() must still raise on an empty workspace — the invariant
    # "loading an empty archive fails" is preserved.
    with pytest.raises(SessionArchiveError, match="empty session archive"):
        store.load("m", "ws-1")
    head = store.commit("m", "ws-1", [_h("a")])
    assert head == "t-00001"
    lin = store.lineage("m", "ws-1")
    assert lin.turn_count == 1
    assert lin.head_turn_id == "t-00001"
    # Label from init_workspace should survive the first commit.
    assert lin.label == "first"


# ---------------------------------------------------------------------------
# ancestry_chain
# ---------------------------------------------------------------------------
def test_ancestry_chain_root_returns_single_entry(tmp_path):
    store = SessionArchiveStore(tmp_path / "archive")
    store.commit("m", "root", [_h("a"), _h("b")])
    chain = ancestry_chain(store, "m", "root")
    assert chain == [("root", "t-00001")]


def test_ancestry_chain_walks_parent_links(tmp_path):
    store = SessionArchiveStore(tmp_path / "archive")
    store.commit("m", "root", [_h("a")])
    store.commit("m", "root", [_h("a"), _h("b")])  # t-00002
    store.fork("m", "root", "child", at_turn="t-00001")
    store.commit("m", "child", [_h("a"), _h("c")])
    store.fork("m", "child", "grand", at_turn="t-00001")
    chain = ancestry_chain(store, "m", "grand")
    # grand -> child@t-00001 -> root@t-00001
    assert chain[0] == ("grand", "t-00001")
    assert chain[1] == ("child", "t-00001")
    assert chain[2] == ("root", "t-00001")
    assert len(chain) == 3


def test_ancestry_chain_handles_dangling_parent(tmp_path):
    store = SessionArchiveStore(tmp_path / "archive")
    store.commit("m", "root", [_h("a")])
    store.fork("m", "root", "child")
    # Dangle the parent by deleting the root manifest.
    import shutil
    shutil.rmtree(store.manifest_path("m", "root").parent)
    chain = ancestry_chain(store, "m", "child")
    # First entry is the starting workspace; last entry is the dangling
    # parent reference we could not resolve.
    assert chain[0][0] == "child"
    assert chain[-1][0] == "root"


def test_ancestry_chain_cycle_guard(tmp_path, monkeypatch):
    store = SessionArchiveStore(tmp_path / "archive")
    store.commit("m", "a", [_h("x")])
    store.commit("m", "b", [_h("x")])
    # Forge a cycle by hand-writing parent pointers that loop.
    a_path = store.manifest_path("m", "a")
    b_path = store.manifest_path("m", "b")
    a_doc = json.loads(a_path.read_text("utf-8"))
    b_doc = json.loads(b_path.read_text("utf-8"))
    a_doc["parent"] = {"session_id": "b", "turn_id": "t-00001"}
    b_doc["parent"] = {"session_id": "a", "turn_id": "t-00001"}
    a_path.write_text(json.dumps(a_doc), encoding="utf-8")
    b_path.write_text(json.dumps(b_doc), encoding="utf-8")
    with pytest.raises(SessionArchiveError, match="cycle"):
        ancestry_chain(store, "m", "a", max_depth=10)


# ---------------------------------------------------------------------------
# CLI: create / status / resume
# ---------------------------------------------------------------------------
def test_cli_create_writes_empty_workspace(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    res = _run(
        "--archive-root", str(root),
        "create",
        "--model-name", "m",
        "--session-id", "ws-1",
        "--label", "demo",
    )
    assert res.returncode == 0, res.stderr
    assert "created\tm/ws-1" in res.stdout
    assert "turns\t0" in res.stdout
    doc = json.loads(
        SessionArchiveStore(root).manifest_path("m", "ws-1").read_text("utf-8")
    )
    assert doc["turns"] == []
    assert doc["label"] == "demo"


def test_cli_create_refuses_existing_workspace(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])
    res = _run(
        "--archive-root", str(root),
        "create",
        "--model-name", "m",
        "--session-id", "ws-1",
    )
    assert res.returncode == 1
    assert "already exists" in res.stderr


def test_cli_status_reports_fields_and_grade(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a"), _h("b")])
    res = _run(
        "--archive-root", str(root),
        "status",
        "--model-name", "m",
        "--session-id", "ws-1",
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "session_id\tws-1" in out
    assert "head_turn_id\tt-00001" in out
    assert "turn_count\t1" in out
    assert "parent\t(root)" in out
    assert "has_head\tTrue" in out
    assert "can_export\tTrue" in out
    assert "grade\thealthy" in out


def test_cli_status_empty_workspace(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    _run(
        "--archive-root", str(root),
        "create", "--model-name", "m", "--session-id", "ws-1",
    )
    res = _run(
        "--archive-root", str(root),
        "status", "--model-name", "m", "--session-id", "ws-1",
    )
    assert res.returncode == 0, res.stderr
    assert "has_head\tFalse" in res.stdout
    assert "turn_count\t0" in res.stdout
    assert "can_export\tFalse" in res.stdout


def test_cli_resume_prints_next_steps(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])
    res = _run(
        "--archive-root", str(root),
        "resume", "--model-name", "m", "--session-id", "ws-1",
    )
    assert res.returncode == 0, res.stderr
    assert "next_steps:" in res.stdout
    assert "grade\thealthy" in res.stdout


# ---------------------------------------------------------------------------
# Argument alias coverage
# ---------------------------------------------------------------------------
def test_cli_turns_accepts_long_and_short_aliases(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])
    # Long form (--model-name / --session-id)
    res1 = _run(
        "--archive-root", str(root), "turns",
        "--model-name", "m", "--session-id", "ws-1",
    )
    # Short form (--model / --session) kept as alias
    res2 = _run(
        "--archive-root", str(root), "turns",
        "--model", "m", "--session", "ws-1",
    )
    assert res1.returncode == 0 and res2.returncode == 0
    assert res1.stdout == res2.stdout


def test_cli_list_accepts_model_name_alias(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "alpha", [_h("a")])
    res = _run(
        "--archive-root", str(root), "list", "--model-name", "m",
    )
    assert res.returncode == 0
    assert "alpha" in res.stdout


# ---------------------------------------------------------------------------
# replay-check: --expected-model-name short-circuits without SSD
# ---------------------------------------------------------------------------
def test_cli_replay_check_expected_model_name_skips_ssd(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])
    res = _run(
        "--archive-root", str(root),
        "replay-check", "--model-name", "m", "--session-id", "ws-1",
        "--expected-model-name", "different-model",
    )
    assert res.returncode == 1
    assert "grade\tincompatible_model" in res.stdout


# ---------------------------------------------------------------------------
# import-session: --expected-block-size guard
# ---------------------------------------------------------------------------
def test_import_rejects_mismatched_block_size(tmp_path):
    # Build a real bundle via export then try to import with a wrong
    # block_size expectation.
    from omlx.cache.session_archive_portable import (
        BundleError,
        export_session,
        import_session,
    )

    src_archive = tmp_path / "src"
    ssd = tmp_path / "ssd"
    ssd.mkdir()
    store = SessionArchiveStore(src_archive)
    payload = b"x" * 32
    h = hashlib.sha256(payload).digest()
    # Mirror the paged SSD layout directly (bucket = first hex char).
    hex_h = h.hex()
    block_path = ssd / hex_h[0] / f"{hex_h}.safetensors"
    block_path.parent.mkdir(parents=True, exist_ok=True)
    block_path.write_bytes(payload)
    store.commit("m", "ws-1", [h], block_size=16)

    out = tmp_path / "bundle.tar"
    export_session(store, "m", "ws-1", ssd, out)

    dst_archive = tmp_path / "dst"
    dst_store = SessionArchiveStore(dst_archive)
    dst_ssd = tmp_path / "dst_ssd"
    with pytest.raises(BundleError, match="compatibility mismatch"):
        import_session(
            dst_store, out, dst_ssd, expected_block_size=32,
        )

    # And a matching block_size must succeed.
    res = import_session(
        dst_store, out, dst_ssd, expected_block_size=16,
    )
    assert res.session_id == "ws-1"
