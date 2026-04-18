# SPDX-License-Identifier: Apache-2.0
"""
Scheduler session restore/commit contract tests.

Pin the narrow contract that:

* after a successful turn, the scheduler commits a session manifest
  referencing the block hashes produced during that turn
* on a subsequent restart, a request with ``session_id`` and
  ``restore=True`` has its block table rebuilt from the manifest
* unknown / empty / gapped / unreadable manifests fail clearly, never
  silently degrade
* one session cannot satisfy another session's restore
* requests without session fields take the normal path (no session
  archive files produced)

These tests target hypothetical ``Scheduler.restore_session(request)`` and
``Scheduler.commit_session(request)`` methods. The Scheduler constructor
is heavy, so tests instantiate a minimal scheduler shell via
``object.__new__`` and hand-wire only the state exercised by the two
methods. This keeps the suite hermetic and fast while still pinning the
production contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pytest


# ---------------------------------------------------------------------------
# Import helpers that fail clearly if the feature hasn't landed yet
# ---------------------------------------------------------------------------


def _import_store():
    from omlx.cache.session_archive import SessionArchiveError, SessionArchiveStore

    return SessionArchiveStore, SessionArchiveError


def _import_scheduler():
    from omlx.scheduler import Scheduler

    return Scheduler


def _require_method(cls, name: str) -> None:
    if not hasattr(cls, name):
        pytest.fail(
            f"Scheduler.{name} must exist — migration contract requires "
            f"explicit session restore/commit hooks"
        )


# ---------------------------------------------------------------------------
# Minimal scheduler shell + fake SSD cache
# ---------------------------------------------------------------------------


class _FakeBlockTable:
    def __init__(self, block_ids: List[int]):
        self.block_ids = list(block_ids)
        self.num_tokens = len(block_ids) * 16


class _FakePagedSSDCache:
    """Tracks present block hashes and returns deterministic block ids."""

    def __init__(self, present: Optional[List[bytes]] = None):
        self._present = set(present or [])
        self._order = list(present or [])

    def has_block(self, h: bytes) -> bool:
        return h in self._present

    def add(self, h: bytes) -> None:
        if h not in self._present:
            self._present.add(h)
            self._order.append(h)

    def block_id_for(self, h: bytes) -> int:
        return self._order.index(h)


def _fresh_scheduler(
    archive_root: Path, present_hashes: Optional[List[bytes]] = None
):
    Scheduler = _import_scheduler()
    SessionArchiveStore, _ = _import_store()

    sched = object.__new__(Scheduler)
    # Minimal state the restore/commit methods are expected to touch.
    sched.session_archive_store = SessionArchiveStore(archive_root)
    sched.paged_ssd_cache_manager = _FakePagedSSDCache(present_hashes)
    sched.config = type("Cfg", (), {"model_name": "test-model"})()
    # Narrow request book-keeping some implementations may inspect.
    sched.waiting = []  # type: ignore[attr-defined]
    sched.running = {}  # type: ignore[attr-defined]
    sched.requests = {}  # type: ignore[attr-defined]
    return sched


def _make_request(session_id: Optional[str] = None, restore: bool = False):
    from omlx.request import Request, SamplingParams

    kwargs = {}
    if session_id is not None:
        kwargs["session_id"] = session_id
    if restore:
        kwargs["restore"] = True
    return Request(
        request_id="req-1",
        prompt="hello",
        sampling_params=SamplingParams(max_tokens=8),
        **kwargs,
    )


def _hashes(prefix: str, n: int) -> List[bytes]:
    return [(f"{prefix}-{i:02d}".encode("utf-8") + b"\x00" * 32)[:32] for i in range(n)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scheduler_restores_same_session_after_restart(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")
    _require_method(Scheduler, "commit_session")

    archive = tmp_path / "sessions"
    SessionArchiveStore, _ = _import_store()

    # Instance A: commit a manifest referencing three block hashes.
    a = _fresh_scheduler(archive, present_hashes=_hashes("turn1", 3))
    req_a = _make_request(session_id="sess-A")
    req_a.block_table = _FakeBlockTable(list(range(3)))
    req_a._committed_block_hashes = list(a.paged_ssd_cache_manager._order)  # type: ignore[attr-defined]
    a.commit_session(req_a)

    # Instance B: same archive dir + same SSD cache contents, fresh state.
    b = _fresh_scheduler(archive, present_hashes=_hashes("turn1", 3))
    req_b = _make_request(session_id="sess-A", restore=True)
    b.restore_session(req_b)

    assert req_b.block_table is not None, "restored request must have a block table"
    assert len(req_b.block_table.block_ids) == 3, (
        "restored block table must have one entry per manifest hash, in order"
    )


def test_scheduler_rejects_unknown_session_restore(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")
    _, SessionArchiveError = _import_store()

    s = _fresh_scheduler(tmp_path / "sessions")
    req = _make_request(session_id="never-existed", restore=True)
    with pytest.raises((SessionArchiveError, FileNotFoundError, ValueError)) as exc_info:
        s.restore_session(req)
    assert "unknown session" in str(exc_info.value).lower(), (
        f"unknown session must surface 'unknown session': {exc_info.value!r}"
    )


def test_scheduler_rejects_empty_session_archive(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")
    SessionArchiveStore, SessionArchiveError = _import_store()

    archive = tmp_path / "sessions"
    SessionArchiveStore(archive).commit("test-model", "sess-empty", [])

    s = _fresh_scheduler(archive)
    req = _make_request(session_id="sess-empty", restore=True)
    with pytest.raises((SessionArchiveError, ValueError)) as exc_info:
        s.restore_session(req)
    assert "empty session archive" in str(exc_info.value).lower(), (
        f"empty archive must surface 'empty session archive': {exc_info.value!r}"
    )
    assert req.block_table is None, (
        "rejected restore must not attach partial block-table state"
    )


def test_scheduler_rejects_gapped_or_unreadable_archive(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")
    SessionArchiveStore, SessionArchiveError = _import_store()

    archive = tmp_path / "sessions"
    committed = _hashes("gap", 3)
    SessionArchiveStore(archive).commit("test-model", "sess-gap", committed)

    # SSD cache is missing the middle block — the archive is gapped.
    s = _fresh_scheduler(archive, present_hashes=[committed[0], committed[2]])
    req = _make_request(session_id="sess-gap", restore=True)
    with pytest.raises((SessionArchiveError, FileNotFoundError, ValueError)) as exc_info:
        s.restore_session(req)
    msg = str(exc_info.value).lower()
    assert any(tok in msg for tok in ("gapped", "missing block", "unreadable")), (
        f"gapped archive must surface a clear error, got {exc_info.value!r}"
    )
    assert req.block_table is None, (
        "gapped restore must not silently produce a partial block table"
    )


def test_scheduler_does_not_cross_restore_between_sessions(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")
    _require_method(Scheduler, "commit_session")
    SessionArchiveStore, _ = _import_store()

    archive = tmp_path / "sessions"
    store = SessionArchiveStore(archive)
    blocks_1 = _hashes("s1", 2)
    blocks_2 = _hashes("s2", 3)
    store.commit("test-model", "sess-1", blocks_1)
    store.commit("test-model", "sess-2", blocks_2)

    s = _fresh_scheduler(archive, present_hashes=blocks_1 + blocks_2)
    req = _make_request(session_id="sess-2", restore=True)
    s.restore_session(req)

    assert req.block_table is not None
    # Only sess-2's manifest length — sess-1 state must not leak in.
    assert len(req.block_table.block_ids) == len(blocks_2), (
        "restored block table must match the requested session's manifest only"
    )


def test_normal_request_path_is_unchanged_without_restore(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "commit_session")

    archive = tmp_path / "sessions"
    s = _fresh_scheduler(archive)

    # No session fields → commit_session must be a no-op.
    req = _make_request()
    req.block_table = _FakeBlockTable(list(range(2)))
    req._committed_block_hashes = _hashes("turn", 2)  # type: ignore[attr-defined]
    s.commit_session(req)

    files = list(archive.rglob("*")) if archive.exists() else []
    files = [p for p in files if p.is_file()]
    assert files == [], (
        f"requests without session fields must not produce session archive "
        f"files: {files}"
    )


def test_manifest_is_committed_after_successful_turn(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "commit_session")
    SessionArchiveStore, _ = _import_store()

    archive = tmp_path / "sessions"
    s = _fresh_scheduler(archive, present_hashes=_hashes("commit", 3))
    req = _make_request(session_id="sess-commit")
    req.block_table = _FakeBlockTable(list(range(3)))
    req._committed_block_hashes = list(s.paged_ssd_cache_manager._order)  # type: ignore[attr-defined]

    s.commit_session(req)

    # A fresh store instance on the same root must read back the manifest.
    loaded = SessionArchiveStore(archive).load("test-model", "sess-commit")
    assert loaded == req._committed_block_hashes, (
        "commit_session must persist the ordered block-hash manifest for the turn"
    )
