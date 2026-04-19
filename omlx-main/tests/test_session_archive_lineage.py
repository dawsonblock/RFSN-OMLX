# SPDX-License-Identifier: Apache-2.0
"""Tests for the lineage/recovery layer."""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path
from typing import List

import pytest

from omlx.cache.session_archive import (
    INTEGRITY_HEALTHY,
    INTEGRITY_INCOMPATIBLE_MODEL,
    INTEGRITY_INVALID_MANIFEST,
    INTEGRITY_MISSING_BLOCKS,
    MANIFEST_VERSION,
    SessionArchiveError,
    SessionArchiveStore,
    classify_integrity,
    diff_sessions,
    make_turn_id,
    replay_check,
)
from omlx.cache.session_archive_portable import (
    BUNDLE_VERSION,
    BundleError,
    export_session,
    import_session,
)
from omlx.cache.session_archive_retention import integrity_grade


def _h(s: str) -> bytes:
    return hashlib.sha256(s.encode()).digest()


# ---------- Phase 1/2: v2 manifest, turns, head, lineage, fork ----------
def test_commit_first_turn_populates_v2_fields(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    tid = store.commit("m", "s", [_h("a")], label="chat-1", description="demo",
                       block_size=256)
    assert tid == make_turn_id(1)
    doc = json.loads(store.manifest_path("m", "s").read_text())
    assert doc["version"] == MANIFEST_VERSION
    assert doc["label"] == "chat-1"
    assert doc["description"] == "demo"
    assert doc["head_turn_id"] == tid
    assert doc["parent"] is None
    assert doc["model_compat"]["block_size"] == 256
    assert len(doc["turns"]) == 1


def test_commit_appends_turn_and_advances_head(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    tid2 = store.commit("m", "s", [_h("a"), _h("b")])
    tid3 = store.commit("m", "s", [_h("a"), _h("b"), _h("c")])
    turns = store.list_turns("m", "s")
    assert [t.turn_id for t in turns] == [
        make_turn_id(1), make_turn_id(2), make_turn_id(3),
    ]
    assert tid3 == turns[-1].turn_id
    # load() returns head-turn hashes — scheduler contract preserved.
    assert store.load("m", "s") == [_h("a"), _h("b"), _h("c")]
    # load_turn() gives access to any prior turn.
    assert store.load_turn("m", "s", make_turn_id(1)) == [_h("a")]
    assert tid2 == make_turn_id(2)


def test_v1_manifest_loads_and_upgrades_on_commit(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    manifest = store.manifest_path("m", "s")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    v1 = {
        "version": "1",
        "model_name": "m",
        "session_id": "s",
        "block_hashes": [_h("a").hex(), _h("b").hex()],
    }
    manifest.write_text(json.dumps(v1), encoding="utf-8")
    # load() still works.
    assert store.load("m", "s") == [_h("a"), _h("b")]
    # commit upgrades on disk.
    store.commit("m", "s", [_h("c")])
    doc = json.loads(manifest.read_text())
    assert doc["version"] == MANIFEST_VERSION
    assert len(doc["turns"]) == 2
    assert doc["turns"][0]["note"] == "migrated from v1"
    assert doc["head_turn_id"] == make_turn_id(2)


def test_fork_at_earlier_turn_creates_isolated_lineage(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "main", [_h("a")])
    store.commit("m", "main", [_h("a"), _h("b")])
    store.commit("m", "main", [_h("a"), _h("b"), _h("c")])

    src_turn = store.fork("m", "main", "branch", at_turn=make_turn_id(2),
                          label="alt")
    assert src_turn == make_turn_id(2)

    # Branch head should match main@t-00002.
    assert store.load("m", "branch") == [_h("a"), _h("b")]
    lin = store.lineage("m", "branch")
    assert lin.parent == ("main", make_turn_id(2))
    assert lin.label == "alt"
    # Further commits to branch do not leak to main.
    store.commit("m", "branch", [_h("a"), _h("b"), _h("x")])
    assert store.load("m", "main") == [_h("a"), _h("b"), _h("c")]
    assert store.load("m", "branch") == [_h("a"), _h("b"), _h("x")]


def test_fork_refuses_unknown_turn(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "main", [_h("a")])
    with pytest.raises(SessionArchiveError, match="unknown turn"):
        store.fork("m", "main", "branch", at_turn="t-99999")


def test_fork_refuses_existing_destination(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "main", [_h("a")])
    store.commit("m", "other", [_h("z")])
    with pytest.raises(SessionArchiveError, match="fork refused"):
        store.fork("m", "main", "other")


# ---------- Phase 3: diff ----------
def test_diff_two_unrelated_sessions(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "a", [_h("x")])
    store.commit("m", "b", [_h("y")])
    d = diff_sessions(store, "m", "a", "m", "b")
    assert d.common_ancestor is None
    assert d.shared_turn_count == 0
    assert d.per_turn[0].diverged is True


def test_diff_detects_fork_ancestor(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "main", [_h("a")])
    store.commit("m", "main", [_h("a"), _h("b")])
    store.fork("m", "main", "branch", at_turn=make_turn_id(2))
    store.commit("m", "branch", [_h("a"), _h("b"), _h("x")])
    d = diff_sessions(store, "m", "main", "m", "branch")
    assert d.common_ancestor == ("main", make_turn_id(2))
    # Per-turn comparison is positional (both turn_id_a/b default to the
    # same index). Fork creates its own t-00001 seeded from main@t-00002,
    # so turn-1 differs by construction; common_prefix_blocks captures
    # the shared head.
    assert d.per_turn[0].common_prefix_blocks == 1  # both start with [_h("a"),...]
    assert d.per_turn[1].common_prefix_blocks == 2  # [a,b] ... [a,b,x]
    assert d.per_turn[1].diverged is True
    # Both sides have 2 turns after this setup.
    assert d.turn_count_a == 2
    assert d.turn_count_b == 2


# ---------- Phase 4: replay-check ----------
def test_replay_check_healthy_when_all_blocks_present(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a"), _h("b")])
    rep = replay_check(store, "m", "s", has_block=lambda h: True)
    assert rep.replayable is True
    assert rep.grade == INTEGRITY_HEALTHY
    assert rep.missing_blocks == []


def test_replay_check_flags_missing(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a"), _h("b")])
    present = {_h("a")}
    rep = replay_check(store, "m", "s", has_block=lambda h: h in present)
    assert rep.replayable is False
    assert rep.grade == INTEGRITY_MISSING_BLOCKS
    assert rep.present_blocks == 1
    assert len(rep.missing_blocks) == 1


def test_replay_check_unreadable_when_session_unknown(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    rep = replay_check(store, "m", "nope", has_block=lambda h: True)
    assert rep.replayable is False
    assert rep.grade in (
        "unreadable", "invalid_manifest",
    )


# ---------- Phase 5: export / import ----------
def _make_ssd_block(ssd_dir: Path, block_hash: bytes, payload: bytes) -> Path:
    hex_h = block_hash.hex()
    p = ssd_dir / hex_h[0] / f"{hex_h}.safetensors"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def test_export_import_roundtrip(tmp_path: Path) -> None:
    src_archive = tmp_path / "src" / "archive"
    src_ssd = tmp_path / "src" / "ssd"
    src_archive.mkdir(parents=True)
    src_ssd.mkdir(parents=True)
    store_src = SessionArchiveStore(src_archive)

    h1, h2 = _h("a"), _h("b")
    _make_ssd_block(src_ssd, h1, b"bytes-a")
    _make_ssd_block(src_ssd, h2, b"bytes-b")

    store_src.commit("m", "s", [h1])
    store_src.commit("m", "s", [h1, h2], note="adds b")

    out = tmp_path / "bundle.omlx-session.tar"
    res = export_session(store_src, "m", "s", src_ssd, out)
    assert res.block_count == 2
    assert res.missing_block_count == 0
    assert out.exists()

    # Default commits must not write any payload into the archive root.
    assert not list(src_archive.rglob("*.safetensors"))

    # Verify bundle format.
    with tarfile.open(out, "r") as tar:
        names = tar.getnames()
    assert "bundle.json" in names
    assert "manifest.json" in names
    assert any(n.startswith("blocks/") for n in names)

    # Import into a clean target.
    dst_archive = tmp_path / "dst" / "archive"
    dst_ssd = tmp_path / "dst" / "ssd"
    dst_archive.mkdir(parents=True)
    dst_ssd.mkdir(parents=True)
    store_dst = SessionArchiveStore(dst_archive)
    ir = import_session(store_dst, out, dst_ssd, expected_model_name="m")
    assert ir.blocks_written == 2
    assert ir.blocks_skipped == 0

    # Manifest landed and load() returns head hashes.
    assert store_dst.load("m", "s") == [h1, h2]
    # Block files landed at SSD layout.
    for h in (h1, h2):
        assert (dst_ssd / h.hex()[0] / f"{h.hex()}.safetensors").exists()


def test_export_refuses_missing_blocks_unless_allowed(tmp_path: Path) -> None:
    archive = tmp_path / "a"
    ssd = tmp_path / "s"
    archive.mkdir()
    ssd.mkdir()
    store = SessionArchiveStore(archive)
    store.commit("m", "s", [_h("missing")])
    out = tmp_path / "b.tar"
    with pytest.raises(BundleError, match="missing"):
        export_session(store, "m", "s", ssd, out)
    # Partial export succeeds with flag.
    res = export_session(store, "m", "s", ssd, out, allow_missing_blocks=True)
    assert res.missing_block_count == 1
    assert res.grade == "partially_exportable"


def test_import_rejects_wrong_model_name(tmp_path: Path) -> None:
    # Build a valid bundle for model "m".
    archive = tmp_path / "a"; archive.mkdir()
    ssd = tmp_path / "s"; ssd.mkdir()
    store = SessionArchiveStore(archive)
    _make_ssd_block(ssd, _h("x"), b"payload")
    store.commit("m", "s", [_h("x")])
    out = tmp_path / "b.tar"
    export_session(store, "m", "s", ssd, out)

    # Import expecting a different model.
    dst_archive = tmp_path / "da"; dst_archive.mkdir()
    dst_ssd = tmp_path / "ds"; dst_ssd.mkdir()
    dst_store = SessionArchiveStore(dst_archive)
    with pytest.raises(BundleError, match="model_name"):
        import_session(
            dst_store, out, dst_ssd, expected_model_name="other-model"
        )


def test_import_rejects_corrupted_bundle(tmp_path: Path) -> None:
    archive = tmp_path / "a"; archive.mkdir()
    ssd = tmp_path / "s"; ssd.mkdir()
    store = SessionArchiveStore(archive)
    _make_ssd_block(ssd, _h("x"), b"payload")
    store.commit("m", "s", [_h("x")])
    out = tmp_path / "b.tar"
    export_session(store, "m", "s", ssd, out)

    # Corrupt a block file inside the bundle: re-tar with altered payload.
    work = tmp_path / "work"; work.mkdir()
    with tarfile.open(out, "r") as tar:
        tar.extractall(work)
    # Overwrite the block payload to break sha256.
    blk = next((work / "blocks").iterdir())
    blk.write_bytes(b"tampered")
    bad = tmp_path / "bad.tar"
    with tarfile.open(bad, "w") as tar:
        for p in work.iterdir():
            tar.add(p, arcname=p.name)

    dst_archive = tmp_path / "da"; dst_archive.mkdir()
    dst_ssd = tmp_path / "ds"; dst_ssd.mkdir()
    dst_store = SessionArchiveStore(dst_archive)
    with pytest.raises(BundleError, match="sha256 mismatch"):
        import_session(dst_store, bad, dst_ssd)


def test_import_refuses_overwriting_existing_session(tmp_path: Path) -> None:
    archive = tmp_path / "a"; archive.mkdir()
    ssd = tmp_path / "s"; ssd.mkdir()
    store = SessionArchiveStore(archive)
    _make_ssd_block(ssd, _h("x"), b"payload")
    store.commit("m", "s", [_h("x")])
    out = tmp_path / "b.tar"
    export_session(store, "m", "s", ssd, out)

    # Reuse the same archive as target — session "m/s" already exists.
    with pytest.raises(BundleError, match="already exists"):
        import_session(store, out, ssd)


# ---------- Phase 6: integrity grades ----------
def test_classify_integrity_healthy_and_unknown(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    assert classify_integrity(store, "m", "s") == INTEGRITY_HEALTHY
    assert classify_integrity(store, "m", "nope") != INTEGRITY_HEALTHY


def test_classify_integrity_incompatible_model(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    assert (
        classify_integrity(store, "m", "s", expected_model_name="other")
        == INTEGRITY_INCOMPATIBLE_MODEL
    )


def test_classify_integrity_stale(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    # updated_at is ~now; asking for a stale threshold in the future.
    from omlx.cache.session_archive import INTEGRITY_STALE
    grade = classify_integrity(
        store, "m", "s", stale_after_seconds=-1.0,  # force stale
    )
    assert grade == INTEGRITY_STALE


def test_integrity_grade_map_covers_retention_statuses() -> None:
    assert integrity_grade("ok") == INTEGRITY_HEALTHY
    assert integrity_grade("missing_blocks") == INTEGRITY_MISSING_BLOCKS
    assert integrity_grade("invalid:malformed") == INTEGRITY_INVALID_MANIFEST
    assert integrity_grade("invalid:compat") == INTEGRITY_INCOMPATIBLE_MODEL


def test_bundle_version_constant() -> None:
    assert BUNDLE_VERSION == "1"
