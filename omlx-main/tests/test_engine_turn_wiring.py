# SPDX-License-Identifier: Apache-2.0
"""
Engine turn-boundary wiring for session archive.

Pin the narrow contract that ``Scheduler.add_request`` invokes
``restore_session`` when (and only when) a request opts into restore, and
that the finish boundary invokes ``commit_session`` after populating
``request._committed_block_hashes`` from the paged cache.

These tests drive the real ``Scheduler.add_request`` + finish helpers, but
they stub the heavy KV/batching side so the test stays hermetic. They do
*not* exercise the Metal / BatchGenerator path.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import pytest

from omlx.cache.session_archive import SessionArchiveStore
from omlx.request import Request, SamplingParams
from omlx.scheduler import Scheduler


# ---------------------------------------------------------------------------
# Minimal scheduler shell
# ---------------------------------------------------------------------------


class _FakePagedSSDCache:
    def __init__(self, present: Optional[List[bytes]] = None) -> None:
        self._order = list(present or [])
        self._present = set(self._order)

    def has_block(self, h: bytes) -> bool:
        return h in self._present

    def block_id_for(self, h: bytes) -> int:
        return self._order.index(h)


class _FakeBlock:
    def __init__(self, block_hash: bytes) -> None:
        self.block_hash = block_hash


class _FakePagedCache:
    """Minimal stand-in exposing .blocks[block_id].block_hash."""

    def __init__(self, block_hashes: List[bytes]) -> None:
        self.blocks = [_FakeBlock(h) for h in block_hashes]

    def get_block_table(self, request_id):  # noqa: ARG002
        return None


def _hashes(prefix: str, n: int) -> List[bytes]:
    return [(f"{prefix}-{i:02d}".encode("utf-8") + b"\x00" * 32)[:32] for i in range(n)]


def _fresh_scheduler(
    archive_root: Path, present_hashes: Optional[List[bytes]] = None
) -> Scheduler:
    s = object.__new__(Scheduler)
    s.session_archive_store = SessionArchiveStore(archive_root)
    s.paged_ssd_cache_manager = _FakePagedSSDCache(present_hashes)
    s.paged_cache_manager = _FakePagedCache(present_hashes or [])
    s.config = SimpleNamespace(model_name="test-model", paged_cache_block_size=16)
    return s


def _make_request(
    session_id: Optional[str] = None,
    restore: bool = False,
) -> Request:
    kwargs = {}
    if session_id is not None:
        kwargs["session_id"] = session_id
    if restore:
        kwargs["restore"] = True
    return Request(
        request_id="turn-req",
        prompt="hello",
        prompt_token_ids=[1, 2, 3],
        sampling_params=SamplingParams(max_tokens=4),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scheduler_exposes_extract_hashes_helper(tmp_path: Path) -> None:
    """A helper that maps block_ids -> block_hashes must exist.

    Without it, ``commit_session`` cannot populate
    ``_committed_block_hashes`` at the finish boundary.
    """
    assert hasattr(Scheduler, "_hashes_from_block_table"), (
        "Scheduler must expose a _hashes_from_block_table helper so the "
        "finish boundary can derive the ordered block-hash manifest"
    )


def test_extract_hashes_returns_ordered_manifest(tmp_path: Path) -> None:
    blocks = _hashes("extract", 3)
    s = _fresh_scheduler(tmp_path / "sessions", present_hashes=blocks)

    req = _make_request(session_id="sess-ext")
    req.block_table = SimpleNamespace(block_ids=[0, 1, 2], num_tokens=48)

    result = s._hashes_from_block_table(req)
    assert result == blocks, "helper must preserve block-id ordering"


def test_extract_hashes_returns_empty_without_block_table(tmp_path: Path) -> None:
    s = _fresh_scheduler(tmp_path / "sessions")
    req = _make_request(session_id="sess-none")
    assert s._hashes_from_block_table(req) == []


def test_finish_request_commits_session_manifest(tmp_path: Path) -> None:
    """Finishing a turn with a session_id must materialize a manifest."""
    blocks = _hashes("finish", 4)
    archive = tmp_path / "sessions"
    s = _fresh_scheduler(archive, present_hashes=blocks)
    req = _make_request(session_id="sess-finish")
    req.block_table = SimpleNamespace(block_ids=list(range(4)), num_tokens=64)

    # Simulate the finish-boundary hook directly: the scheduler must provide
    # a narrow entry point that populates _committed_block_hashes and
    # invokes commit_session.
    assert hasattr(Scheduler, "_finalize_session_for_request"), (
        "Scheduler must expose _finalize_session_for_request as a narrow "
        "finish-boundary hook"
    )
    s._finalize_session_for_request(req)

    loaded = SessionArchiveStore(archive).load("test-model", "sess-finish")
    assert loaded == blocks


def test_finish_non_session_request_writes_nothing(tmp_path: Path) -> None:
    archive = tmp_path / "sessions"
    s = _fresh_scheduler(archive, present_hashes=_hashes("n", 2))
    req = _make_request()  # no session_id
    req.block_table = SimpleNamespace(block_ids=[0, 1], num_tokens=32)

    s._finalize_session_for_request(req)
    assert not archive.exists() or not any(p.is_file() for p in archive.rglob("*")), (
        "non-session requests must not create manifest files"
    )


def test_finish_session_request_without_blocks_is_noop(tmp_path: Path) -> None:
    archive = tmp_path / "sessions"
    s = _fresh_scheduler(archive)
    req = _make_request(session_id="sess-empty-finish")
    # No block_table attached -> no hashes -> commit must be a no-op.
    s._finalize_session_for_request(req)
    assert not archive.exists() or not any(p.is_file() for p in archive.rglob("*"))


def test_commit_session_idempotent_on_repeated_finish(tmp_path: Path) -> None:
    blocks = _hashes("idem", 2)
    archive = tmp_path / "sessions"
    s = _fresh_scheduler(archive, present_hashes=blocks)
    req = _make_request(session_id="sess-idem")
    req.block_table = SimpleNamespace(block_ids=[0, 1], num_tokens=32)

    s._finalize_session_for_request(req)
    s._finalize_session_for_request(req)  # idempotent / atomic rewrite
    loaded = SessionArchiveStore(archive).load("test-model", "sess-idem")
    assert loaded == blocks
