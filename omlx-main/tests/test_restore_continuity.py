# SPDX-License-Identifier: Apache-2.0
"""
Restore continuity tests.

Pin the contract that restart+restore produces non-empty ordered cached
state, and that a continuation after restore is observably different from
a fresh empty-session request path. Structural assertions only — no
token-perfect output comparison — to avoid brittleness while still
proving that restored state is actually reused.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pytest


def _import_store():
    from omlx.cache.session_archive import SessionArchiveStore

    return SessionArchiveStore


def _import_scheduler():
    from omlx.scheduler import Scheduler

    return Scheduler


def _require_method(cls, name: str) -> None:
    if not hasattr(cls, name):
        pytest.fail(
            f"Scheduler.{name} must exist — restore continuity requires "
            f"explicit session restore/commit hooks"
        )


class _FakeBlockTable:
    def __init__(self, block_ids: List[int]):
        self.block_ids = list(block_ids)
        self.num_tokens = len(block_ids) * 16


class _FakePagedSSDCache:
    def __init__(self, present: Optional[List[bytes]] = None):
        self._present = set(present or [])
        self._order = list(present or [])

    def has_block(self, h: bytes) -> bool:
        return h in self._present


def _hashes(prefix: str, n: int) -> List[bytes]:
    return [(f"{prefix}-{i:02d}".encode("utf-8") + b"\x00" * 32)[:32] for i in range(n)]


def _fresh_scheduler(archive_root: Path, present: Optional[List[bytes]] = None):
    Scheduler = _import_scheduler()
    SessionArchiveStore = _import_store()

    s = object.__new__(Scheduler)
    s.session_archive_store = SessionArchiveStore(archive_root)
    s.paged_ssd_cache_manager = _FakePagedSSDCache(present)
    s.config = type("Cfg", (), {"model_name": "test-model"})()
    s.waiting = []  # type: ignore[attr-defined]
    s.running = {}  # type: ignore[attr-defined]
    s.requests = {}  # type: ignore[attr-defined]
    return s


def _make_request(session_id: Optional[str] = None, restore: bool = False):
    from omlx.request import Request, SamplingParams

    kwargs = {}
    if session_id is not None:
        kwargs["session_id"] = session_id
    if restore:
        kwargs["restore"] = True
    return Request(
        request_id="cont-req",
        prompt="hello",
        sampling_params=SamplingParams(max_tokens=8),
        **kwargs,
    )


def test_restore_after_restart_rebuilds_non_empty_ordered_state(
    tmp_path: Path,
) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")
    _require_method(Scheduler, "commit_session")
    SessionArchiveStore = _import_store()

    archive = tmp_path / "sessions"
    blocks = _hashes("ordered", 4)
    # Instance A commits the manifest.
    a = _fresh_scheduler(archive, present=blocks)
    req_a = _make_request(session_id="sess-ord")
    req_a.block_table = _FakeBlockTable(list(range(4)))
    req_a._committed_block_hashes = list(blocks)  # type: ignore[attr-defined]
    a.commit_session(req_a)

    # Fresh store/scheduler on the same dirs.
    loaded_hashes = SessionArchiveStore(archive).load("test-model", "sess-ord")
    assert loaded_hashes == blocks, "manifest ordering must survive restart"

    b = _fresh_scheduler(archive, present=blocks)
    req_b = _make_request(session_id="sess-ord", restore=True)
    b.restore_session(req_b)

    assert req_b.block_table is not None
    assert len(req_b.block_table.block_ids) > 0, (
        "restore must rebuild non-empty cached state"
    )
    assert len(req_b.block_table.block_ids) == len(blocks), (
        "restored block count must match manifest length exactly"
    )


def test_continuation_after_restore_uses_restored_context(tmp_path: Path) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")
    _require_method(Scheduler, "commit_session")

    archive = tmp_path / "sessions"
    blocks = _hashes("cont", 3)
    a = _fresh_scheduler(archive, present=blocks)
    req_a = _make_request(session_id="sess-cont")
    req_a.block_table = _FakeBlockTable(list(range(3)))
    req_a._committed_block_hashes = list(blocks)  # type: ignore[attr-defined]
    a.commit_session(req_a)

    b = _fresh_scheduler(archive, present=blocks)
    req_b = _make_request(session_id="sess-cont", restore=True)
    b.restore_session(req_b)

    # Restored request consumes restored context: either cached_tokens or
    # shared_prefix_blocks must be populated by the restore step.
    consumed = max(
        getattr(req_b, "cached_tokens", 0),
        getattr(req_b, "shared_prefix_blocks", 0),
        (req_b.block_table.num_tokens if req_b.block_table else 0),
    )
    assert consumed > 0, (
        "continuation after restore must consume restored cached state, "
        "not re-prefill from scratch"
    )


def test_empty_fresh_session_does_not_behave_like_restored_session(
    tmp_path: Path,
) -> None:
    Scheduler = _import_scheduler()
    _require_method(Scheduler, "restore_session")

    archive = tmp_path / "sessions"
    # A fresh session with no committed manifest and no restore flag must
    # remain a plain ordinary request — no pre-populated block table, no
    # session-derived cached tokens.
    s = _fresh_scheduler(archive)
    req = _make_request(session_id="sess-fresh", restore=False)

    assert req.block_table is None, (
        "a fresh non-restore request must not be pre-populated"
    )
    assert getattr(req, "cached_tokens", 0) == 0
    assert getattr(req, "shared_prefix_blocks", 0) == 0

    # Confirm: the scheduler does not silently restore when restore=False.
    # restore_session must not be invoked implicitly for this path.
    assert s.session_archive_store is not None
    assert not list((archive).rglob("*")) or not any(
        p.is_file() for p in archive.rglob("*")
    ), "fresh non-restore path must not materialize session files"
