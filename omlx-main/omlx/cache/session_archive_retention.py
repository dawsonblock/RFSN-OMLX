# SPDX-License-Identifier: Apache-2.0
"""
On-demand retention helpers for the session archive.

Status: **experimental / internal**. This is a small library consumed by
``scripts/session_archive_admin.py``. It never runs automatically and
never touches KV payload bytes; its only job is to identify and (on
request) delete manifest directories under a :class:`SessionArchiveStore`
root.

There is deliberately no background service, no cron hook, and no
deletion from the request path.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Tuple

from .session_archive import (
    MANIFEST_VERSION,
    SessionArchiveError,
    SessionArchiveStore,
    _slug,
)

__all__ = [
    "SessionDescriptor",
    "PruneReport",
    "iter_sessions",
    "classify_session",
    "find_invalid",
    "find_expired",
    "select_over_cap",
    "prune",
]


@dataclass(frozen=True)
class SessionDescriptor:
    """Cheap, operator-facing view of one manifest on disk."""

    session_id: str
    manifest_path: Path
    size_bytes: int
    mtime: float
    block_count: Optional[int]


@dataclass
class PruneReport:
    """Structured result of a :func:`prune` call."""

    model_name: str
    dry_run: bool
    considered: int = 0
    invalid: List[Tuple[str, str]] = field(default_factory=list)
    expired: List[str] = field(default_factory=list)
    over_cap: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def to_delete(self) -> List[str]:
        seen: List[str] = []
        for sid, _ in self.invalid:
            if sid not in seen:
                seen.append(sid)
        for sid in self.expired:
            if sid not in seen:
                seen.append(sid)
        for sid in self.over_cap:
            if sid not in seen:
                seen.append(sid)
        return seen


def _model_root(store: SessionArchiveStore, model_name: str) -> Path:
    # SessionArchiveStore keeps ``_root`` private; derive the model dir
    # using the same slug rule the store uses so we stay in lockstep.
    return store._root / _slug(model_name)  # noqa: SLF001 — intentional.


def iter_sessions(
    store: SessionArchiveStore, model_name: str
) -> Iterator[SessionDescriptor]:
    """Yield one :class:`SessionDescriptor` per manifest on disk.

    Silently skips directories without a ``manifest.json`` so partially
    cleaned-up trees do not raise here; :func:`find_invalid` will pick
    those up.
    """
    model_dir = _model_root(store, model_name)
    if not model_dir.is_dir():
        return
    for child in sorted(model_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / "manifest.json"
        if not manifest.exists():
            continue
        try:
            stat = manifest.stat()
        except OSError:
            continue
        session_id = child.name
        block_count: Optional[int] = None
        try:
            hashes = store.load(model_name, session_id)
            block_count = len(hashes)
        except SessionArchiveError:
            block_count = None
        except Exception:
            block_count = None
        yield SessionDescriptor(
            session_id=session_id,
            manifest_path=manifest,
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            block_count=block_count,
        )


def classify_session(
    store: SessionArchiveStore,
    ssd_cache: Any,
    model_name: str,
    session_id: str,
) -> Tuple[str, str]:
    """Return ``(status, detail)`` for one session manifest.

    ``status`` is one of:

    * ``"ok"`` — manifest loads and every referenced block is present in
      the SSD cache.
    * ``"missing_blocks"`` — manifest loads but one or more referenced
      blocks are no longer present.
    * ``"invalid:<reason>"`` — manifest fails :meth:`SessionArchiveStore.load`.
      ``<reason>`` is one of the stable substrings the store emits.
    """
    try:
        hashes = store.load(model_name, session_id)
    except SessionArchiveError as exc:
        reason = _classify_archive_error(str(exc))
        return f"invalid:{reason}", str(exc)
    except Exception as exc:  # pragma: no cover — defensive
        return "invalid:unreadable", f"{type(exc).__name__}: {exc}"

    if ssd_cache is None:
        return "ok", f"{len(hashes)} blocks (ssd check skipped)"

    has_block = getattr(ssd_cache, "has_block", None)
    if not callable(has_block):
        return "ok", f"{len(hashes)} blocks (ssd has_block unavailable)"

    missing: List[int] = []
    for idx, h in enumerate(hashes):
        try:
            if not has_block(h):
                missing.append(idx)
        except Exception:
            missing.append(idx)
    if missing:
        detail = (
            f"{len(missing)}/{len(hashes)} blocks missing; first indexes="
            f"{missing[:5]}"
        )
        return "missing_blocks", detail
    return "ok", f"{len(hashes)} blocks"


def _classify_archive_error(msg: str) -> str:
    lowered = msg.lower()
    if "unknown session" in lowered:
        return "unknown"
    if "malformed manifest" in lowered:
        return "malformed"
    if "empty session archive" in lowered:
        return "empty"
    if "compatibility mismatch" in lowered:
        return "compat"
    return "unreadable"


def find_invalid(
    store: SessionArchiveStore,
    ssd_cache: Any,
    model_name: str,
) -> List[Tuple[str, str]]:
    """Return ``(session_id, reason)`` for every session that is not OK."""
    out: List[Tuple[str, str]] = []
    for desc in iter_sessions(store, model_name):
        status, detail = classify_session(
            store, ssd_cache, model_name, desc.session_id
        )
        if status != "ok":
            out.append((desc.session_id, f"{status}: {detail}"))
    # Also catch manifest-less session dirs so operators can clean them.
    model_dir = _model_root(store, model_name)
    if model_dir.is_dir():
        for child in sorted(model_dir.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "manifest.json").exists():
                out.append((child.name, "invalid:missing_manifest"))
    return out


def find_expired(
    store: SessionArchiveStore,
    model_name: str,
    older_than: timedelta,
    *,
    now: Optional[float] = None,
) -> List[str]:
    """Return session ids whose manifest mtime is older than ``older_than``."""
    if older_than.total_seconds() <= 0:
        return []
    threshold = (now if now is not None else time.time()) - older_than.total_seconds()
    expired: List[str] = []
    for desc in iter_sessions(store, model_name):
        if desc.mtime < threshold:
            expired.append(desc.session_id)
    return expired


def select_over_cap(
    store: SessionArchiveStore, model_name: str, max_count: int
) -> List[str]:
    """Return the oldest session ids beyond ``max_count`` (oldest first)."""
    if max_count <= 0:
        return [d.session_id for d in iter_sessions(store, model_name)]
    descs = sorted(iter_sessions(store, model_name), key=lambda d: d.mtime)
    if len(descs) <= max_count:
        return []
    return [d.session_id for d in descs[: len(descs) - max_count]]


def _delete_session_dir(store: SessionArchiveStore, model_name: str, session_id: str) -> None:
    session_dir = (_model_root(store, model_name) / _slug(session_id))
    if not session_dir.exists():
        return
    shutil.rmtree(session_dir)


def prune(
    store: SessionArchiveStore,
    ssd_cache: Any,
    model_name: str,
    *,
    invalid: bool = False,
    older_than: Optional[timedelta] = None,
    max_per_model: Optional[int] = None,
    dry_run: bool = True,
) -> PruneReport:
    """Identify and (unless ``dry_run``) delete prunable session manifests."""
    report = PruneReport(model_name=model_name, dry_run=dry_run)
    all_ids = [d.session_id for d in iter_sessions(store, model_name)]
    report.considered = len(all_ids)

    if invalid:
        report.invalid = find_invalid(store, ssd_cache, model_name)
    if older_than is not None:
        report.expired = find_expired(store, model_name, older_than)
    if max_per_model is not None:
        report.over_cap = select_over_cap(store, model_name, max_per_model)

    if dry_run:
        return report

    for sid in report.to_delete:
        try:
            _delete_session_dir(store, model_name, sid)
            report.deleted.append(sid)
        except Exception as exc:
            report.errors.append((sid, f"{type(exc).__name__}: {exc}"))
    return report
