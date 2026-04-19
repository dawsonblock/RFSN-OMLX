# SPDX-License-Identifier: Apache-2.0
"""Failure-mode tests for the session archive layer.

Covers operational edge cases documented in the hardening plan:

1. Interrupted / partial manifest write.
2. Double commit on the same ``(model, session_id)``.
3. Concurrent restore + commit.
4. Crash between turn completion and ``os.replace`` (temp file cleanup).
5. Validation of a manifest whose referenced blocks disappeared.

No model is loaded; these exercise only
``omlx.cache.session_archive.SessionArchiveStore`` and
``omlx.cache.session_archive_retention``.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from datetime import timedelta
from typing import List, Set

import pytest

from omlx.cache import session_archive_metrics as metrics
from omlx.cache.session_archive import (
    SessionArchiveError,
    SessionArchiveStore,
)
from omlx.cache.session_archive_retention import (
    classify_session,
    find_expired,
    find_invalid,
    iter_sessions,
    prune,
    select_over_cap,
)


class _FakeSSD:
    """Minimal SSD stand-in: ``has_block(h) -> h in self.present``."""

    def __init__(self, present: Set[bytes]) -> None:
        self.present: Set[bytes] = set(present)


    def has_block(self, h: bytes) -> bool:
        return h in self.present


def _h(tag: str) -> bytes:
    return hashlib.sha256(tag.encode()).digest()


@pytest.fixture(autouse=True)
def _reset_metrics():
    metrics.reset()
    yield
    metrics.reset()


# ---------------------------------------------------------------------------
# 1. Interrupted / partial manifest write
# ---------------------------------------------------------------------------
def test_partial_manifest_write_is_rejected_and_next_commit_requires_operator_action(tmp_path):
    store = SessionArchiveStore(tmp_path)
    model = "m"
    session = "s"

    manifest = store.manifest_path(model, session)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    # Simulate a crash that left a half-written manifest on disk.
    manifest.write_text('{"version": "1", "model_name": "m"', encoding="utf-8")

    with pytest.raises(SessionArchiveError, match="malformed manifest"):
        store.load(model, session)

    # Trust policy: do not silently repair or overwrite a malformed
    # manifest on the next commit. The operator must intervene first.
    with pytest.raises(SessionArchiveError, match="malformed manifest"):
        store.commit(model, session, [_h("a"), _h("b")])

    # Once the bad file is explicitly removed, a fresh commit succeeds.
    manifest.unlink()
    store.commit(model, session, [_h("a"), _h("b")])
    assert store.load(model, session) == [_h("a"), _h("b")]

    # No lingering temp files.
    stray = [p.name for p in manifest.parent.glob(".manifest.*.tmp")]
    assert stray == []


# ---------------------------------------------------------------------------
# 2. Double commit overwrites atomically
# ---------------------------------------------------------------------------
def test_double_commit_overwrites_with_last_writer(tmp_path):
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    store.commit("m", "s", [_h("b"), _h("c")])
    assert store.load("m", "s") == [_h("b"), _h("c")]


# ---------------------------------------------------------------------------
# 3. Concurrent restore + commit — no torn reads
# ---------------------------------------------------------------------------
def test_concurrent_restore_and_commit_never_tears(tmp_path):
    store = SessionArchiveStore(tmp_path)
    first = [_h(f"a{i}") for i in range(8)]
    second = [_h(f"b{i}") for i in range(8)]
    store.commit("m", "s", first)

    stop = threading.Event()
    errors: List[Exception] = []
    observed: List[List[bytes]] = []

    def writer():
        toggle = False
        while not stop.is_set():
            store.commit("m", "s", second if toggle else first)
            toggle = not toggle

    def reader():
        for _ in range(200):
            try:
                observed.append(store.load("m", "s"))
            except SessionArchiveError as exc:  # pragma: no cover — fail the test
                errors.append(exc)
                return

    w = threading.Thread(target=writer)
    r = threading.Thread(target=reader)
    w.start()
    r.start()
    r.join(timeout=10)
    stop.set()
    w.join(timeout=10)

    assert not errors, f"load raised during concurrent commit: {errors!r}"
    assert observed, "reader did not complete any reads"
    # Every observation must be one of the two fully-written snapshots.
    for snap in observed:
        assert snap == first or snap == second


# ---------------------------------------------------------------------------
# 4. Crash between temp write and os.replace
# ---------------------------------------------------------------------------
def test_commit_cleans_up_tempfile_when_replace_fails(tmp_path, monkeypatch):
    store = SessionArchiveStore(tmp_path)
    calls = {"n": 0}
    real_replace = os.replace

    def boom(src, dst):
        calls["n"] += 1
        raise OSError("simulated crash between write and rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.commit("m", "s", [_h("a")])
    # Restore real os.replace so the assertions below are trustworthy.
    monkeypatch.setattr(os, "replace", real_replace)

    session_dir = store.manifest_path("m", "s").parent
    assert calls["n"] == 1
    # Manifest must NOT exist after a failed commit.
    assert not store.manifest_path("m", "s").exists()
    # And no temp file should be left behind.
    stray = list(session_dir.glob(".manifest.*.tmp"))
    assert stray == [], f"temp files leaked: {stray!r}"

    # Metrics must show the commit failure.
    snap = metrics.snapshot()
    assert snap.get(metrics.EVENT_MANIFEST_COMMIT_FAILED, 0) >= 1
    assert snap.get(metrics.EVENT_MANIFEST_COMMITTED, 0) == 0

    # A subsequent real commit succeeds.
    store.commit("m", "s", [_h("a")])
    assert store.load("m", "s") == [_h("a")]


# ---------------------------------------------------------------------------
# 5. Validation surfaces post-commit missing blocks
# ---------------------------------------------------------------------------
def test_validate_reports_missing_blocks_after_ssd_loss(tmp_path):
    store = SessionArchiveStore(tmp_path)
    hashes = [_h("x"), _h("y"), _h("z")]
    store.commit("m", "s", hashes)

    ssd = _FakeSSD(present=set(hashes))
    status, detail = classify_session(store, ssd, "m", "s")
    assert status == "ok"

    # Simulate SSD losing block y.
    ssd.present.remove(_h("y"))
    status, detail = classify_session(store, ssd, "m", "s")
    assert status == "missing_blocks"
    assert "1/3" in detail
    assert "[1]" in detail  # index of the missing block

    invalid = find_invalid(store, ssd, "m")
    assert len(invalid) == 1
    sid, reason = invalid[0]
    assert sid == "s"
    assert "missing_blocks" in reason


# ---------------------------------------------------------------------------
# Retention helpers — smoke tests so CLI has something to lean on
# ---------------------------------------------------------------------------
def test_iter_sessions_skips_dirs_without_manifest(tmp_path):
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "alive", [_h("a")])
    # Orphan dir with no manifest.
    (tmp_path / "m" / "orphan").mkdir(parents=True)

    ids = [d.session_id for d in iter_sessions(store, "m")]
    assert ids == ["alive"]

    # find_invalid must still surface the orphan.
    invalid = find_invalid(store, _FakeSSD({_h("a")}), "m")
    sids = {sid for sid, _ in invalid}
    assert "orphan" in sids


def test_find_expired_by_mtime(tmp_path):
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "old", [_h("a")])
    store.commit("m", "new", [_h("a")])

    manifest_old = store.manifest_path("m", "old")
    stale = time.time() - 86400 * 30  # 30 days old
    os.utime(manifest_old, (stale, stale))

    expired = find_expired(store, "m", timedelta(days=1))
    assert expired == ["old"]


def test_select_over_cap_returns_oldest_first(tmp_path):
    store = SessionArchiveStore(tmp_path)
    for i, sid in enumerate(["a", "b", "c", "d"]):
        store.commit("m", sid, [_h(sid)])
        t = time.time() - (100 - i) * 10
        os.utime(store.manifest_path("m", sid), (t, t))

    # Cap at 2 → oldest two must go.
    over = select_over_cap(store, "m", 2)
    assert over == ["a", "b"]


def test_prune_dry_run_then_delete(tmp_path):
    store = SessionArchiveStore(tmp_path)
    hashes = [_h("a")]
    store.commit("m", "good", hashes)
    store.commit("m", "orphan", hashes)
    # Drop SSD payload for "orphan" by advertising only "good" 's hash.
    ssd = _FakeSSD(present=set(hashes))

    # Also make orphan the one without backing blocks — pretend SSD lost it.
    ssd.present.clear()  # now both are missing
    ssd.present.add(_h("a"))  # but we only really need one hash kept present
    # Actually both sessions reference the same single hash; they both stay
    # valid. To make "orphan" invalid, commit it with a different hash that
    # isn't in the SSD:
    store.commit("m", "orphan", [_h("gone")])

    report = prune(store, ssd, "m", invalid=True, dry_run=True)
    bad_ids = {sid for sid, _ in report.invalid}
    assert "orphan" in bad_ids
    assert not report.deleted  # dry-run

    report = prune(store, ssd, "m", invalid=True, dry_run=False)
    assert "orphan" in report.deleted
    # good must still be there.
    assert store.load("m", "good") == hashes
    with pytest.raises(SessionArchiveError, match="unknown session"):
        store.load("m", "orphan")


# ---------------------------------------------------------------------------
# Metrics wiring smoke-test
# ---------------------------------------------------------------------------
def test_store_load_bumps_invalid_counter_with_reason(tmp_path):
    store = SessionArchiveStore(tmp_path)
    with pytest.raises(SessionArchiveError, match="unknown session"):
        store.load("m", "ghost")
    snap = metrics.snapshot()
    assert snap.get(metrics.EVENT_SESSION_ARCHIVE_INVALID, 0) == 1
    # Reason-tagged key exists.
    assert snap.get(f"{metrics.EVENT_SESSION_ARCHIVE_INVALID}:unknown", 0) == 1


def test_store_commit_bumps_committed_counter(tmp_path):
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    snap = metrics.snapshot()
    assert snap.get(metrics.EVENT_MANIFEST_COMMITTED, 0) == 1
