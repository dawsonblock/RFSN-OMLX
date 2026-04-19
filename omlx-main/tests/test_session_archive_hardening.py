# SPDX-License-Identifier: Apache-2.0
"""Hardening tests for the lineage/export/import layer.

These tests are *contract* tests, not broad end-to-end coverage.
Each one pins a single invariant that the user requirements list
calls out explicitly:

A3 — malformed / out-of-order head rejected clearly
B3 — fork at head (no ``at_turn`` argument)
B5 — fork does not duplicate payload bytes
C1 — diff between identical sessions is stable
C2 — diff does not mutate either manifest
C4 — diff against an unknown session raises clearly
D3 — replay-check flags incompatible model
D4 — replay-check distinguishes empty session state from healthy
E-extra — import rejects a bundle missing its ``blocks/`` directory
F-isolation — normal ops never write payload bytes into the archive root
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from omlx.cache.session_archive import (
    INTEGRITY_HEALTHY,
    INTEGRITY_INCOMPATIBLE_MODEL,
    INTEGRITY_UNREADABLE,
    SessionArchiveError,
    SessionArchiveStore,
    classify_integrity,
    diff_sessions,
    make_turn_id,
    replay_check,
)
from omlx.cache.session_archive_portable import (
    BundleError,
    export_session,
    import_session,
)
from omlx.cache.session_archive_retention import prune


def _h(s: str) -> bytes:
    return hashlib.sha256(s.encode()).digest()


# --------------------------------------------------------------------------
# A3 — head tracking must reject a head_turn_id that doesn't resolve.
# --------------------------------------------------------------------------
def test_head_turn_id_mismatch_is_rejected_clearly(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    store.commit("m", "s", [_h("a"), _h("b")])
    # Tamper the manifest so head_turn_id points to a turn that doesn't
    # exist. A silent fallback to turns[-1] would mask data corruption.
    mp = store.manifest_path("m", "s")
    doc = json.loads(mp.read_text())
    doc["head_turn_id"] = "t-99999"
    mp.write_text(json.dumps(doc))
    with pytest.raises(SessionArchiveError, match="head_turn_id"):
        store.load("m", "s")


# --------------------------------------------------------------------------
# B3 — fork at head works without an ``at_turn`` argument.
# --------------------------------------------------------------------------
def test_fork_at_head_without_at_turn_argument(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "main", [_h("a")])
    store.commit("m", "main", [_h("a"), _h("b")])
    used = store.fork("m", "main", "branch")
    # Fork defaulted to the source head.
    assert used == make_turn_id(2)
    # Branch's first turn carries the source head's block list verbatim.
    assert store.load("m", "branch") == [_h("a"), _h("b")]
    lin = store.lineage("m", "branch")
    assert lin.parent == ("main", make_turn_id(2))


# --------------------------------------------------------------------------
# B5 — fork must not duplicate any payload bytes.
# --------------------------------------------------------------------------
def test_fork_does_not_write_payload_into_archive(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "main", [_h("a"), _h("b")])
    store.fork("m", "main", "branch")
    # No safetensors files, no other KV-shaped blobs under the archive.
    for pattern in ("*.safetensors", "*.bin", "*.npz"):
        assert not list(tmp_path.rglob(pattern)), (
            f"fork wrote payload-like files ({pattern}) under archive root"
        )


# --------------------------------------------------------------------------
# C1 — diff between identical sessions is stable.
# C2 — diff is read-only.
# C4 — diff against an unknown session raises clearly.
# --------------------------------------------------------------------------
def test_diff_between_identical_sessions_is_stable(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    # Same model, same session id — diff must be trivially consistent.
    store.commit("m", "s", [_h("a")])
    store.commit("m", "s", [_h("a"), _h("b")])
    d = diff_sessions(store, "m", "s", "m", "s")
    assert d.turn_count_a == d.turn_count_b == 2
    # Every positional turn is identical — diverged=False, full prefix match.
    for per in d.per_turn:
        assert per.diverged is False
        assert per.block_count_a == per.block_count_b
        assert per.common_prefix_blocks == per.block_count_a
    assert d.shared_turn_count == 2


def test_diff_does_not_mutate_either_manifest(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "a", [_h("x")])
    store.commit("m", "b", [_h("y")])
    snap_a = store.manifest_path("m", "a").read_bytes()
    snap_b = store.manifest_path("m", "b").read_bytes()
    mtime_a = store.manifest_path("m", "a").stat().st_mtime_ns
    mtime_b = store.manifest_path("m", "b").stat().st_mtime_ns
    diff_sessions(store, "m", "a", "m", "b")
    # Byte-for-byte identical and untouched.
    assert store.manifest_path("m", "a").read_bytes() == snap_a
    assert store.manifest_path("m", "b").read_bytes() == snap_b
    assert store.manifest_path("m", "a").stat().st_mtime_ns == mtime_a
    assert store.manifest_path("m", "b").stat().st_mtime_ns == mtime_b


def test_diff_against_unknown_session_raises(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "a", [_h("x")])
    with pytest.raises(SessionArchiveError, match="unknown session"):
        diff_sessions(store, "m", "a", "m", "no-such-session")


# --------------------------------------------------------------------------
# D3 — replay-check flags an incompatible expected model cleanly.
# D4 — empty / unreadable session state is distinguishable from healthy.
# --------------------------------------------------------------------------
def test_replay_check_flags_incompatible_model(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    rep = replay_check(
        store, "m", "s", has_block=lambda h: True,
        expected_model_name="other-model",
    )
    assert rep.replayable is False
    assert rep.grade == INTEGRITY_INCOMPATIBLE_MODEL
    # Probe never ran — no false presence count.
    assert rep.present_blocks == 0
    assert rep.total_blocks == 0


def test_replay_check_distinguishes_empty_from_healthy(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "healthy", [_h("a")])
    healthy = replay_check(store, "m", "healthy", has_block=lambda h: True)
    assert healthy.grade == INTEGRITY_HEALTHY
    assert healthy.replayable is True

    empty = replay_check(store, "m", "nope", has_block=lambda h: True)
    assert empty.grade == INTEGRITY_UNREADABLE
    assert empty.replayable is False
    # The two reports must not compare equal at the grade/replayable level.
    assert healthy.grade != empty.grade


# --------------------------------------------------------------------------
# E-extra — bundle missing ``blocks/`` directory is rejected.
# --------------------------------------------------------------------------
def test_import_rejects_bundle_missing_manifest(tmp_path: Path) -> None:
    # Hand-build a "bundle" that has bundle.json but no manifest.json.
    bad = tmp_path / "bad.tar"
    envelope = {
        "bundle_version": "1",
        "created_at": 0,
        "model_name": "m",
        "session_id": "s",
        "head_turn_id": "t-00001",
        "block_count": 0,
        "block_sha256": {},
        "source_cache_layout": "paged-ssd-safetensors/v1",
    }
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "bundle.json").write_text(json.dumps(envelope))
    (stage / "blocks").mkdir()
    with tarfile.open(bad, "w") as tar:
        tar.add(stage / "bundle.json", arcname="bundle.json")
        tar.add(stage / "blocks", arcname="blocks")

    dst_archive = tmp_path / "da"
    dst_archive.mkdir()
    dst_ssd = tmp_path / "ds"
    dst_ssd.mkdir()
    store = SessionArchiveStore(dst_archive)
    with pytest.raises(BundleError, match="manifest"):
        import_session(store, bad, dst_ssd)


# --------------------------------------------------------------------------
# F-isolation — normal operator ops never write payload bytes.
# --------------------------------------------------------------------------
def test_normal_ops_never_write_payload_bytes_into_archive(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    store = SessionArchiveStore(archive)

    # Every op the operator may reach from the CLI except export-session
    # and import-session.
    store.commit("m", "a", [_h("x")])
    store.commit("m", "a", [_h("x"), _h("y")])
    store.fork("m", "a", "b")
    store.list_turns("m", "a")
    store.load_head("m", "a")
    store.load_turn("m", "a", make_turn_id(1))
    store.lineage("m", "a")
    store.set_label("m", "a", label="renamed")
    diff_sessions(store, "m", "a", "m", "b")
    replay_check(store, "m", "a", has_block=lambda h: True)
    classify_integrity(store, "m", "a")
    prune(store, ssd_cache=None, model_name="m", dry_run=True)

    # The archive tree must contain only JSON manifests — no payload-shaped
    # files anywhere.
    all_files = [p for p in archive.rglob("*") if p.is_file()]
    assert all_files, "expected at least one manifest on disk"
    for p in all_files:
        assert p.suffix == ".json", f"unexpected non-JSON file under archive: {p}"
        # And nothing hash-named like an SSD block file.
        assert not p.name.endswith(".safetensors")


# --------------------------------------------------------------------------
# Retention bonus — a freshly-committed, healthy session must not be
# deletable by the default prune (no flags ⇒ empty to_delete list).
# --------------------------------------------------------------------------
def test_prune_without_flags_does_not_target_healthy_session(
    tmp_path: Path,
) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "recent", [_h("a")])
    report = prune(store, ssd_cache=None, model_name="m", dry_run=True)
    assert report.considered == 1
    assert report.to_delete == []
    assert report.deleted == []


def test_prune_dry_run_does_not_delete_even_when_flagged(
    tmp_path: Path,
) -> None:
    # Session is over-cap (max_per_model=0 ⇒ every session is over cap)
    # but dry_run=True must not remove anything.
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "a", [_h("a")])
    store.commit("m", "b", [_h("b")])
    report = prune(
        store, ssd_cache=None, model_name="m",
        max_per_model=0, dry_run=True,
    )
    assert set(report.over_cap) == {"a", "b"}
    assert report.deleted == []
    # Manifests untouched on disk.
    assert store.manifest_path("m", "a").exists()
    assert store.manifest_path("m", "b").exists()
