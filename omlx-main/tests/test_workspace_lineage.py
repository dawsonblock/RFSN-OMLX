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
    store.init_workspace("m", "ws-1", label="first", task_tag="coding.fix")
    # load() must still raise on an empty workspace — the invariant
    # "loading an empty archive fails" is preserved.
    with pytest.raises(SessionArchiveError, match="empty session archive"):
        store.load("m", "ws-1")
    head = store.commit("m", "ws-1", [_h("a")], note="checkpoint")
    assert head == "t-00001"
    lin = store.lineage("m", "ws-1")
    assert lin.turn_count == 1
    assert lin.head_turn_id == "t-00001"
    # Label from init_workspace should survive the first commit.
    assert lin.label == "first"
    assert lin.task_tag == "coding.fix"


def test_invalid_metadata_rejected_cleanly(tmp_path):
    store = SessionArchiveStore(tmp_path / "archive")
    with pytest.raises(SessionArchiveError, match="task_tag"):
        store.init_workspace("m", "ws-1", task_tag="not allowed spaces")
    with pytest.raises(SessionArchiveError, match="label exceeds"):
        store.init_workspace("m", "ws-2", label="x" * 121)


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
    store.commit("m", "root", [_h("a")], label="bugfix", task_tag="coding.patch")
    store.commit("m", "root", [_h("a"), _h("b")], note="checkpoint-2")  # t-00002
    store.fork(
        "m", "root", "child", at_turn="t-00001", branch_reason="try risky refactor"
    )
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
    store.commit(
        "m", "ws-1", [_h("a"), _h("b")],
        label="Implement auth", description="fix failing login flow",
        task_tag="coding.auth", note="checkpoint 1",
    )
    res = _run(
        "--archive-root", str(root),
        "status",
        "--model-name", "m",
        "--session-id", "ws-1",
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout
    assert "session_id\tws-1" in out
    assert "label\tImplement auth" in out
    assert "description\tfix failing login flow" in out
    assert "task_tag\tcoding.auth" in out
    assert "head_turn_id\tt-00001" in out
    assert "turn_count\t1" in out
    assert "parent\t(root)" in out
    assert "has_head\tTrue" in out
    assert "replayable\tnot_checked" in out
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


def test_cli_status_can_surface_stale_and_incompatible(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])
    path = store.manifest_path("m", "ws-1")
    doc = json.loads(path.read_text("utf-8"))
    doc["updated_at"] = 1.0
    path.write_text(json.dumps(doc), encoding="utf-8")

    stale = _run(
        "--archive-root", str(root), "status",
        "--model-name", "m", "--session-id", "ws-1", "--stale-after", "1s",
    )
    assert stale.returncode == 0
    assert "grade\tstale" in stale.stdout

    incompatible = _run(
        "--archive-root", str(root), "status",
        "--model-name", "m", "--session-id", "ws-1",
        "--expected-model-name", "other-model",
    )
    assert incompatible.returncode == 0
    assert "grade\tincompatible_model" in incompatible.stdout


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


def test_cli_turns_and_show_surface_branch_reason(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "root", [_h("a")], label="base")
    store.fork(
        "m", "root", "branch", branch_reason="before risky refactor", task_tag="coding.refactor"
    )
    turns = _run(
        "--archive-root", str(root), "turns",
        "--model-name", "m", "--session-id", "branch",
    )
    assert turns.returncode == 0
    assert "before risky refactor" in turns.stdout
    show = _run(
        "--archive-root", str(root), "show",
        "--model-name", "m", "--session-id", "branch",
    )
    assert show.returncode == 0
    assert "task_tag:" in show.stdout
    assert "branch_why:" in show.stdout


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


def test_import_conflict_policy_default_fail_then_rename_and_reroot(tmp_path):
    from omlx.cache.session_archive_portable import export_session, import_session, BundleError

    src_archive = tmp_path / "src"
    ssd = tmp_path / "ssd"
    ssd.mkdir()
    store = SessionArchiveStore(src_archive)
    payload = b"payload-1"
    h = hashlib.sha256(payload).digest()
    hp = ssd / h.hex()[0] / f"{h.hex()}.safetensors"
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_bytes(payload)
    store.commit("m", "ws-1", [h], label="Coding task", task_tag="coding.demo")
    bundle = tmp_path / "bundle.tar"
    export_session(store, "m", "ws-1", ssd, bundle)

    dst_archive = tmp_path / "dst"
    dst_store = SessionArchiveStore(dst_archive)
    dst_store.commit("m", "ws-1", [h], label="Existing")

    with pytest.raises(BundleError, match="default policy: fail"):
        import_session(dst_store, bundle, tmp_path / "dst_ssd")

    renamed = import_session(
        dst_store, bundle, tmp_path / "dst_ssd", rename_on_conflict=True, re_root_lineage=True,
    )
    assert renamed.session_id == "ws-1-imported-1"
    assert renamed.conflict_policy == "rename"
    assert renamed.re_rooted is True
    imported_doc = dst_store.load_raw("m", renamed.session_id)
    assert imported_doc["parent"] is None


def test_cli_import_and_inspect_bundle_surface_provenance(tmp_path):
    from omlx.cache.session_archive_portable import export_session

    src_archive = tmp_path / "src"
    ssd = tmp_path / "ssd"
    ssd.mkdir()
    store = SessionArchiveStore(src_archive)
    payload = b"payload-2"
    h = hashlib.sha256(payload).digest()
    hp = ssd / h.hex()[0] / f"{h.hex()}.safetensors"
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_bytes(payload)
    store.commit(
        "m", "ws-provenance", [h],
        label="Refactor parser", description="safe branch for AST work",
        task_tag="coding.parser",
    )
    bundle = tmp_path / "bundle.tar"
    export_session(store, "m", "ws-provenance", ssd, bundle)

    inspect_res = _run(
        "--archive-root", str(src_archive),
        "inspect-bundle", "--bundle", str(bundle),
    )
    assert inspect_res.returncode == 0, inspect_res.stderr
    assert "source_label\tRefactor parser" in inspect_res.stdout
    assert "task_tag\tcoding.parser" in inspect_res.stdout
    assert "model_compat\tm block_size=" in inspect_res.stdout

    dst_root = tmp_path / "dst"
    dst_root.mkdir()
    dst_ssd = tmp_path / "dst_ssd"
    import_res = _run(
        "--archive-root", str(dst_root), "--ssd-cache-dir", str(dst_ssd),
        "import-session", "--bundle", str(bundle),
        "--expected-model-name", "m", "--fail-if-exists",
    )
    assert import_res.returncode == 0, import_res.stderr
    assert "conflict_policy\tfail" in import_res.stdout
    assert "source_label\tRefactor parser" in import_res.stdout


def test_commit_refuses_silent_repair_of_malformed_manifest(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])
    path = store.manifest_path("m", "ws-1")
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SessionArchiveError, match="malformed manifest"):
        store.commit("m", "ws-1", [_h("b")])


def test_duplicate_turn_ids_and_out_of_order_history_are_rejected(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])
    path = store.manifest_path("m", "ws-1")
    doc = json.loads(path.read_text("utf-8"))
    doc["turns"] = [
        {
            "turn_id": "t-00002",
            "committed_at": 2.0,
            "block_hashes": [_h("a").hex()],
            "note": "late first",
        },
        {
            "turn_id": "t-00002",
            "committed_at": 1.0,
            "block_hashes": [_h("b").hex()],
            "note": "duplicate id",
        },
    ]
    doc["head_turn_id"] = "t-00002"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SessionArchiveError, match="duplicate turn_id|out-of-order"):
        store.load_raw("m", "ws-1")


def test_parent_reference_to_missing_turn_is_invalid(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "root", [_h("a")])
    store.fork("m", "root", "branch")
    path = store.manifest_path("m", "branch")
    doc = json.loads(path.read_text("utf-8"))
    doc["parent"] = {"session_id": "root", "turn_id": "t-99999"}
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SessionArchiveError, match="missing parent turn"):
        store.lineage("m", "branch")


def test_resume_rejects_invalid_and_incompatible(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.commit("m", "ws-1", [_h("a")])

    invalid = _run(
        "--archive-root", str(root),
        "resume", "--model-name", "m", "--session-id", "ghost",
    )
    assert invalid.returncode == 1

    incompatible = _run(
        "--archive-root", str(root),
        "resume", "--model-name", "m", "--session-id", "ws-1",
        "--expected-model-name", "other-model",
    )
    assert incompatible.returncode == 1
    assert "grade\tincompatible_model" in incompatible.stdout


def test_overwrite_replaces_existing_session_dir_and_no_payload_leaks(tmp_path):
    from omlx.cache.session_archive_portable import export_session, import_session

    src_archive = tmp_path / "src"
    src_ssd = tmp_path / "src_ssd"
    src_ssd.mkdir()
    store = SessionArchiveStore(src_archive)
    payload = b"payload-3"
    h = hashlib.sha256(payload).digest()
    hp = src_ssd / h.hex()[0] / f"{h.hex()}.safetensors"
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_bytes(payload)
    store.commit("m", "ws-1", [h], label="fresh")
    bundle = tmp_path / "bundle.tar"
    export_session(store, "m", "ws-1", src_ssd, bundle)

    dst_archive = tmp_path / "dst"
    dst_store = SessionArchiveStore(dst_archive)
    dst_store.commit("m", "ws-1", [_h("old")], label="old")
    stale_file = dst_store.manifest_path("m", "ws-1").parent / "stale.txt"
    stale_file.write_text("stale", encoding="utf-8")

    res = import_session(
        dst_store, bundle, tmp_path / "dst_ssd", overwrite_session=True,
    )
    assert res.conflict_policy == "overwrite"
    assert not stale_file.exists()
    assert list(dst_archive.rglob("*.safetensors")) == []


def test_dangling_imported_lineage_is_visible_in_status(tmp_path):
    from omlx.cache.session_archive_portable import export_session, import_session

    src_archive = tmp_path / "src"
    src_ssd = tmp_path / "src_ssd"
    src_ssd.mkdir()
    store = SessionArchiveStore(src_archive)
    payload = b"payload-4"
    h = hashlib.sha256(payload).digest()
    hp = src_ssd / h.hex()[0] / f"{h.hex()}.safetensors"
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_bytes(payload)
    store.commit("m", "root", [h], label="root")
    store.fork("m", "root", "branch", branch_reason="portable handoff")
    bundle = tmp_path / "branch.tar"
    export_session(store, "m", "branch", src_ssd, bundle)

    dst_archive = tmp_path / "dst"
    dst_store = SessionArchiveStore(dst_archive)
    import_session(dst_store, bundle, tmp_path / "dst_ssd")

    status = _run(
        "--archive-root", str(dst_archive),
        "status", "--model-name", "m", "--session-id", "branch",
    )
    assert status.returncode == 0, status.stderr
    assert "parent_status\tdangling" in status.stdout
    assert "branch_origin\troot@t-00001" in status.stdout


def test_normal_workspace_ops_stay_metadata_only(tmp_path):
    root = tmp_path / "archive"
    store = SessionArchiveStore(root)
    store.init_workspace("m", "alpha", label="A")
    store.commit("m", "alpha", [_h("a")], note="checkpoint")
    store.fork("m", "alpha", "beta", branch_reason="risk split")
    _run("--archive-root", str(root), "status", "--model-name", "m", "--session-id", "alpha")
    _run("--archive-root", str(root), "diff", "--model-a", "m", "--session-a", "alpha", "--model-b", "m", "--session-b", "beta")
    assert list(root.rglob("*.safetensors")) == []
    assert list(root.rglob("*.tar")) == []


def test_import_rejects_unknown_bundle_layout(tmp_path):
    import tarfile

    from omlx.cache.session_archive_portable import BundleError, export_session, import_session

    src_archive = tmp_path / "src"
    ssd = tmp_path / "ssd"
    ssd.mkdir()
    store = SessionArchiveStore(src_archive)
    payload = b"payload-5"
    h = hashlib.sha256(payload).digest()
    hp = ssd / h.hex()[0] / f"{h.hex()}.safetensors"
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_bytes(payload)
    store.commit("m", "ws-1", [h])
    bundle = tmp_path / "bundle.tar"
    export_session(store, "m", "ws-1", ssd, bundle)

    work = tmp_path / "rewrite"
    work.mkdir()
    with tarfile.open(bundle, "r") as tar:
        tar.extractall(work)
    bundle_json = work / "bundle.json"
    data = json.loads(bundle_json.read_text("utf-8"))
    data["source_cache_layout"] = "unknown-layout/v9"
    bundle_json.write_text(json.dumps(data), encoding="utf-8")
    broken = tmp_path / "broken.tar"
    with tarfile.open(broken, "w") as tar:
        tar.add(work / "bundle.json", arcname="bundle.json")
        tar.add(work / "manifest.json", arcname="manifest.json")
        tar.add(work / "blocks", arcname="blocks")

    with pytest.raises(BundleError, match="source_cache_layout mismatch"):
        import_session(SessionArchiveStore(tmp_path / "dst"), broken, tmp_path / "dst_ssd")
