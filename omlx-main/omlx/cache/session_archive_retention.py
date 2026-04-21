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
    INTEGRITY_HEALTHY,
    INTEGRITY_INCOMPATIBLE_MODEL,
    INTEGRITY_INVALID_MANIFEST,
    INTEGRITY_MISSING_BLOCKS,
    INTEGRITY_STALE,
    INTEGRITY_UNREADABLE,
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
    "integrity_grade",
    "find_invalid",
    "find_expired",
    "select_over_cap",
    "prune",
    # Pass 6: structured prune-plan layer.
    "HEALTHY_RECENT_DAYS",
    "HEALTHY_STALE_DAYS",
    "INVALID_GRACE_DAYS",
    "ORPHANED_GRACE_DAYS",
    "BUNDLE_RETENTION_DAYS",
    "REASON_HEALTHY_RECENT",
    "REASON_HEALTHY_STALE",
    "REASON_INVALID_MANIFEST",
    "REASON_UNREADABLE_MANIFEST",
    "REASON_EMPTY_ARCHIVE",
    "REASON_ORPHANED",
    "REASON_EXPORT_BUNDLE_OLD",
    "REASON_PINNED",
    "REASON_PROTECTED_LATEST_HEAD",
    "PRUNE_CLASS_STALE",
    "PRUNE_CLASS_INVALID",
    "PRUNE_CLASS_ORPHANED",
    "PRUNE_CLASS_EXPORTS",
    "PRUNE_CLASS_EMPTY",
    "PRUNE_CLASS_UNREADABLE",
    "PRUNE_CLASSES",
    "PruneCandidate",
    "PrunePlan",
    "classify_candidates",
    "plan_prune",
    "execute_plan",
]


# ---------------------------------------------------------------------------
# Pass 6: conservative retention windows and reason vocabulary.
# These constants are deliberately frozen — changes must be reviewed
# alongside docs/pruning_policy.md. All windows are expressed in days.
# ---------------------------------------------------------------------------
HEALTHY_RECENT_DAYS = 30
HEALTHY_STALE_DAYS = 90
INVALID_GRACE_DAYS = 7
ORPHANED_GRACE_DAYS = 14
BUNDLE_RETENTION_DAYS = 21

# Reason constants (closed set — callers must not invent new values).
REASON_HEALTHY_RECENT = "healthy_recent"
REASON_HEALTHY_STALE = "healthy_stale"
REASON_INVALID_MANIFEST = "invalid_manifest"
REASON_UNREADABLE_MANIFEST = "unreadable_manifest"
REASON_EMPTY_ARCHIVE = "empty_archive"
REASON_ORPHANED = "orphaned"
REASON_EXPORT_BUNDLE_OLD = "export_bundle_old"
REASON_PINNED = "pinned"
REASON_PROTECTED_LATEST_HEAD = "protected_latest_head"

# Prune classes — the stable tokens used by the admin CLI to opt into
# deletion of a specific kind of candidate. A candidate whose class is
# not in the requested set is omitted from the plan entirely.
PRUNE_CLASS_STALE = "stale"
PRUNE_CLASS_INVALID = "invalid"
PRUNE_CLASS_ORPHANED = "orphaned"
PRUNE_CLASS_EXPORTS = "exports"
PRUNE_CLASS_EMPTY = "empty"
PRUNE_CLASS_UNREADABLE = "unreadable"
PRUNE_CLASSES = (
    PRUNE_CLASS_STALE,
    PRUNE_CLASS_INVALID,
    PRUNE_CLASS_ORPHANED,
    PRUNE_CLASS_EXPORTS,
    PRUNE_CLASS_EMPTY,
    PRUNE_CLASS_UNREADABLE,
)

# Reason → class mapping. ``REASON_HEALTHY_RECENT`` has no class because
# recent healthy sessions are never eligible for pruning.
_REASON_TO_CLASS = {
    REASON_HEALTHY_STALE: PRUNE_CLASS_STALE,
    REASON_INVALID_MANIFEST: PRUNE_CLASS_INVALID,
    REASON_UNREADABLE_MANIFEST: PRUNE_CLASS_UNREADABLE,
    REASON_EMPTY_ARCHIVE: PRUNE_CLASS_EMPTY,
    REASON_ORPHANED: PRUNE_CLASS_ORPHANED,
    REASON_EXPORT_BUNDLE_OLD: PRUNE_CLASS_EXPORTS,
}


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


def integrity_grade(
    status: str,
    *,
    stale: bool = False,
) -> str:
    """Map a ``classify_session`` status to a shared integrity grade.

    ``status`` is the first element of the tuple returned by
    :func:`classify_session` (``"ok"``, ``"missing_blocks"``,
    ``"invalid:<reason>"``). ``stale`` overrides a healthy result when
    the caller has determined the manifest has aged past a retention
    threshold.
    """
    if status == "ok":
        return INTEGRITY_STALE if stale else INTEGRITY_HEALTHY
    if status == "missing_blocks":
        return INTEGRITY_MISSING_BLOCKS
    if status.startswith("invalid:"):
        reason = status.split(":", 1)[1]
        if reason == "compat":
            return INTEGRITY_INCOMPATIBLE_MODEL
        if reason in ("malformed", "empty"):
            return INTEGRITY_INVALID_MANIFEST
        return INTEGRITY_UNREADABLE
    return INTEGRITY_UNREADABLE


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


# ===========================================================================
# Pass 6: structured prune-plan layer.
#
# Adds a conservative, reason-grouped alternative to :func:`prune`. The
# legacy ``prune()`` function is kept as-is so existing callers continue
# to work; new callers should prefer :func:`plan_prune` + :func:`execute_plan`
# which surface per-candidate reasons, honour pinning, protect the latest
# healthy head per model, and require ``confirm=True`` for destructive
# execution.
# ===========================================================================
from dataclasses import dataclass as _dataclass  # noqa: E402
from dataclasses import field as _field  # noqa: E402
from typing import Dict as _Dict, Set as _Set  # noqa: E402

from .session_archive_portable import (  # noqa: E402
    BUNDLE_PIN_SUFFIX as _BUNDLE_PIN_SUFFIX,
    is_bundle_pinned as _is_bundle_pinned,
    iter_bundles as _iter_bundles,
)


_ACTION_ELIGIBLE = "eligible"
_ACTION_PROTECTED = "protected"


@_dataclass(frozen=True)
class PruneCandidate:
    """One row in a :class:`PrunePlan`.

    ``kind`` is either ``"workspace"`` or ``"bundle"``. Workspace
    candidates are identified by ``model_name`` + ``session_id``; bundle
    candidates carry the sidecar-less bundle ``path`` and leave
    ``session_id`` empty.
    """

    kind: str
    reason: str
    action: str
    model_name: str
    session_id: str
    path: Path
    age_seconds: float
    last_used_at: Optional[float]
    integrity_grade: Optional[str]
    pinned: bool

    @property
    def prune_class(self) -> Optional[str]:
        return _REASON_TO_CLASS.get(self.reason)


@_dataclass
class PrunePlan:
    """Structured prune proposal grouped by reason.

    :attr:`by_reason` maps each reason constant to the list of
    candidates it produced, in deterministic order (oldest first).
    :attr:`requested_classes` records the classes the caller opted into
    so :func:`execute_plan` can reject surprises.
    """

    model_name: str
    now: float
    include_pinned: bool
    requested_classes: _Set[str] = _field(default_factory=set)
    candidates: List[PruneCandidate] = _field(default_factory=list)
    by_reason: _Dict[str, List[PruneCandidate]] = _field(default_factory=dict)

    @property
    def eligible(self) -> List[PruneCandidate]:
        return [c for c in self.candidates if c.action == _ACTION_ELIGIBLE]

    @property
    def protected(self) -> List[PruneCandidate]:
        return [c for c in self.candidates if c.action == _ACTION_PROTECTED]

    def eligible_by_reason(self) -> _Dict[str, List[PruneCandidate]]:
        out: _Dict[str, List[PruneCandidate]] = {}
        for c in self.eligible:
            out.setdefault(c.reason, []).append(c)
        return out


def _manifest_field(
    store: SessionArchiveStore, model_name: str, session_id: str, key: str
) -> Any:
    try:
        doc = store.load_raw(model_name, session_id)
    except SessionArchiveError:
        return None
    except Exception:  # pragma: no cover — defensive
        return None
    return doc.get(key)


def _workspace_reason_and_grade(
    store: SessionArchiveStore,
    ssd_cache: Any,
    model_name: str,
    desc: SessionDescriptor,
    *,
    now: float,
) -> Tuple[str, Optional[str], Optional[float], bool]:
    """Return ``(reason, integrity_grade, last_used_at, pinned)``.

    Classification is purely metadata-driven; no KV payload is read.
    """
    session_id = desc.session_id
    manifest_path = desc.manifest_path
    # Manifest-less directory → handled by caller (find_invalid path).
    if not manifest_path.exists():
        return REASON_ORPHANED, None, None, False

    try:
        doc = store.load_raw(model_name, session_id)
    except SessionArchiveError as exc:
        low = str(exc).lower()
        if "malformed" in low or "compatibility" in low:
            return REASON_INVALID_MANIFEST, INTEGRITY_INVALID_MANIFEST, None, False
        if "empty" in low:
            return REASON_EMPTY_ARCHIVE, INTEGRITY_INVALID_MANIFEST, None, False
        return REASON_UNREADABLE_MANIFEST, INTEGRITY_UNREADABLE, None, False
    except Exception:  # pragma: no cover — defensive
        return REASON_UNREADABLE_MANIFEST, INTEGRITY_UNREADABLE, None, False

    pinned = bool(doc.get("pinned"))
    last_used_at = doc.get("last_used_at")
    try:
        last_used_at_f = float(last_used_at) if last_used_at is not None else None
    except (TypeError, ValueError):
        last_used_at_f = None

    turns = doc.get("turns") or []
    if not turns or not doc.get("head_turn_id"):
        return REASON_EMPTY_ARCHIVE, INTEGRITY_INVALID_MANIFEST, last_used_at_f, pinned

    status, _detail = classify_session(store, ssd_cache, model_name, session_id)
    if status.startswith("invalid:"):
        kind = status.split(":", 1)[1]
        if kind in ("malformed", "empty"):
            return REASON_INVALID_MANIFEST, INTEGRITY_INVALID_MANIFEST, last_used_at_f, pinned
        return REASON_UNREADABLE_MANIFEST, INTEGRITY_UNREADABLE, last_used_at_f, pinned
    if status == "missing_blocks":
        # Missing blocks is unhealthy but NOT a delete-class by default —
        # operators route it as "invalid" so it requires an explicit opt-in.
        return REASON_INVALID_MANIFEST, INTEGRITY_MISSING_BLOCKS, last_used_at_f, pinned

    # Healthy: pick recent vs stale by (last_used_at or mtime).
    freshness_ts = last_used_at_f if last_used_at_f is not None else desc.mtime
    age_seconds = max(0.0, now - freshness_ts)
    if age_seconds >= HEALTHY_STALE_DAYS * 86400:
        return REASON_HEALTHY_STALE, INTEGRITY_STALE, last_used_at_f, pinned
    if age_seconds >= HEALTHY_RECENT_DAYS * 86400:
        return REASON_HEALTHY_STALE, INTEGRITY_STALE, last_used_at_f, pinned
    return REASON_HEALTHY_RECENT, INTEGRITY_HEALTHY, last_used_at_f, pinned


def _latest_healthy_head(
    store: SessionArchiveStore, model_name: str
) -> Optional[str]:
    """Return the session_id with the most recent ``updated_at`` that is
    healthy (loads without error). ``None`` if no healthy session exists.
    """
    best_sid: Optional[str] = None
    best_ts: float = -1.0
    for desc in iter_sessions(store, model_name):
        try:
            doc = store.load_raw(model_name, desc.session_id)
        except SessionArchiveError:
            continue
        except Exception:  # pragma: no cover — defensive
            continue
        if not doc.get("turns") or not doc.get("head_turn_id"):
            continue
        ts = float(doc.get("updated_at") or desc.mtime)
        if ts > best_ts:
            best_ts = ts
            best_sid = desc.session_id
    return best_sid


def _in_grace(age_seconds: float, days: int) -> bool:
    """Return True iff the candidate is still inside its grace window."""
    return age_seconds < days * 86400


def classify_candidates(
    store: SessionArchiveStore,
    ssd_cache: Any,
    model_name: str,
    *,
    bundle_dir: Optional[Path] = None,
    now: Optional[float] = None,
) -> List[PruneCandidate]:
    """Walk workspaces (+ optional bundle dir) and classify every entry.

    The returned list contains BOTH eligible and protected rows; the
    caller is expected to filter via :func:`plan_prune`. ``now`` accepts
    an injected clock for tests.
    """
    ts_now = now if now is not None else time.time()
    rows: List[PruneCandidate] = []
    latest_head = _latest_healthy_head(store, model_name)

    model_dir = _model_root(store, model_name)
    # Orphaned subdirectories (no manifest) — caught separately so they
    # don't shadow real sessions.
    if model_dir.is_dir():
        for child in sorted(model_dir.iterdir()):
            if not child.is_dir():
                continue
            if (child / "manifest.json").exists():
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                mtime = ts_now
            age = max(0.0, ts_now - mtime)
            action = (
                _ACTION_PROTECTED
                if _in_grace(age, ORPHANED_GRACE_DAYS)
                else _ACTION_ELIGIBLE
            )
            rows.append(
                PruneCandidate(
                    kind="workspace",
                    reason=REASON_ORPHANED,
                    action=action,
                    model_name=model_name,
                    session_id=child.name,
                    path=child,
                    age_seconds=age,
                    last_used_at=None,
                    integrity_grade=None,
                    pinned=False,
                )
            )

    # Real manifests.
    for desc in iter_sessions(store, model_name):
        reason, grade, last_used, pinned = _workspace_reason_and_grade(
            store, ssd_cache, model_name, desc, now=ts_now
        )
        freshness_ts = last_used if last_used is not None else desc.mtime
        age = max(0.0, ts_now - freshness_ts)

        # Decide action.
        if reason == REASON_HEALTHY_RECENT:
            action = _ACTION_PROTECTED
        elif desc.session_id == latest_head:
            # Latest healthy head is always protected regardless of age.
            reason = REASON_PROTECTED_LATEST_HEAD
            action = _ACTION_PROTECTED
        elif pinned:
            # Keep the underlying reason so plan_prune can decide whether
            # --include-pinned should lift protection for the requested
            # class. Action starts as protected; plan_prune may upgrade.
            action = _ACTION_PROTECTED
        elif reason == REASON_HEALTHY_STALE:
            action = _ACTION_ELIGIBLE
        elif reason in (REASON_INVALID_MANIFEST, REASON_UNREADABLE_MANIFEST, REASON_EMPTY_ARCHIVE):
            action = (
                _ACTION_PROTECTED
                if _in_grace(age, INVALID_GRACE_DAYS)
                else _ACTION_ELIGIBLE
            )
        else:
            action = _ACTION_PROTECTED

        rows.append(
            PruneCandidate(
                kind="workspace",
                reason=reason,
                action=action,
                model_name=model_name,
                session_id=desc.session_id,
                path=desc.manifest_path.parent,
                age_seconds=age,
                last_used_at=last_used,
                integrity_grade=grade,
                pinned=pinned,
            )
        )

    # Portable bundles (optional).
    if bundle_dir is not None:
        for bundle_path in _iter_bundles(bundle_dir):
            try:
                mtime = bundle_path.stat().st_mtime
            except OSError:
                mtime = ts_now
            age = max(0.0, ts_now - mtime)
            pinned = _is_bundle_pinned(bundle_path)
            if pinned:
                # Keep underlying "export_bundle_old" reason so the pin
                # marker can be lifted via --include-pinned + --prune-exports.
                reason = REASON_EXPORT_BUNDLE_OLD
                action = _ACTION_PROTECTED
            elif age >= BUNDLE_RETENTION_DAYS * 86400:
                reason = REASON_EXPORT_BUNDLE_OLD
                action = _ACTION_ELIGIBLE
            else:
                reason = REASON_EXPORT_BUNDLE_OLD
                action = _ACTION_PROTECTED
            rows.append(
                PruneCandidate(
                    kind="bundle",
                    reason=reason,
                    action=action,
                    model_name=model_name,
                    session_id="",
                    path=bundle_path,
                    age_seconds=age,
                    last_used_at=None,
                    integrity_grade=None,
                    pinned=pinned,
                )
            )

    # Deterministic order: oldest first, stable by path.
    rows.sort(key=lambda c: (-c.age_seconds, str(c.path)))
    return rows


def plan_prune(
    store: SessionArchiveStore,
    ssd_cache: Any,
    model_name: str,
    *,
    classes: Iterable[str],
    bundle_dir: Optional[Path] = None,
    include_pinned: bool = False,
    now: Optional[float] = None,
) -> PrunePlan:
    """Build a :class:`PrunePlan` limited to the requested prune classes.

    Unknown class tokens raise :class:`ValueError` so typos do not
    silently widen deletion scope.
    """
    requested = set(classes)
    unknown = requested - set(PRUNE_CLASSES)
    if unknown:
        raise ValueError(
            f"unknown prune class(es): {sorted(unknown)}; known: {list(PRUNE_CLASSES)}"
        )
    ts_now = now if now is not None else time.time()
    plan = PrunePlan(
        model_name=model_name,
        now=ts_now,
        include_pinned=include_pinned,
        requested_classes=requested,
    )
    for cand in classify_candidates(
        store, ssd_cache, model_name, bundle_dir=bundle_dir, now=ts_now
    ):
        cls = cand.prune_class
        # Pinned / latest-head rows are always visible in the plan but
        # only upgradable to eligible when include_pinned is True AND
        # their underlying class was requested.
        if cand.reason == REASON_PROTECTED_LATEST_HEAD:
            plan.candidates.append(cand)
            plan.by_reason.setdefault(cand.reason, []).append(cand)
            continue
        if cand.pinned:
            if (
                include_pinned
                and cls is not None
                and cls in requested
                and cand.action == _ACTION_PROTECTED
            ):
                cand_eligible = PruneCandidate(
                    kind=cand.kind,
                    reason=cand.reason,
                    action=_ACTION_ELIGIBLE,
                    model_name=cand.model_name,
                    session_id=cand.session_id,
                    path=cand.path,
                    age_seconds=cand.age_seconds,
                    last_used_at=cand.last_used_at,
                    integrity_grade=cand.integrity_grade,
                    pinned=cand.pinned,
                )
                plan.candidates.append(cand_eligible)
                plan.by_reason.setdefault(cand.reason, []).append(cand_eligible)
            else:
                # Surface the pin protection only when the class would
                # otherwise have been in scope; otherwise omit to keep
                # dry-run output focused.
                if cls is None or cls in requested:
                    plan.candidates.append(cand)
                    plan.by_reason.setdefault(cand.reason, []).append(cand)
            continue
        if cls is None or cls not in requested:
            # Not in scope — omit from the plan entirely so dry-run output
            # is focused on what the operator actually asked about.
            continue
        plan.candidates.append(cand)
        plan.by_reason.setdefault(cand.reason, []).append(cand)
    return plan


def _delete_bundle(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        raise
    # Remove sidecar pin marker too, if any (prevents orphaned markers).
    marker = Path(str(path) + _BUNDLE_PIN_SUFFIX)
    try:
        if marker.exists():
            marker.unlink()
    except OSError:
        pass


def execute_plan(
    plan: PrunePlan,
    store: SessionArchiveStore,
    *,
    confirm: bool,
) -> PruneReport:
    """Execute a :class:`PrunePlan`. Destructive only when ``confirm`` is True.

    Protected candidates are never touched. A plan with no eligible
    candidates is a no-op regardless of ``confirm``.
    """
    report = PruneReport(model_name=plan.model_name, dry_run=not confirm)
    report.considered = len(plan.candidates)
    for cand in plan.candidates:
        bucket = (
            report.expired
            if cand.reason == REASON_HEALTHY_STALE
            else report.over_cap
            if cand.reason == REASON_EXPORT_BUNDLE_OLD
            else None
        )
        if cand.action != _ACTION_ELIGIBLE:
            continue
        if bucket is not None:
            bucket.append(str(cand.path))
        else:
            report.invalid.append((cand.session_id or str(cand.path), cand.reason))

    if not confirm:
        return report

    for cand in plan.candidates:
        if cand.action != _ACTION_ELIGIBLE:
            continue
        try:
            if cand.kind == "bundle":
                _delete_bundle(cand.path)
                report.deleted.append(str(cand.path))
            else:
                _delete_session_dir(store, cand.model_name, cand.session_id)
                report.deleted.append(cand.session_id)
        except Exception as exc:
            report.errors.append(
                (cand.session_id or str(cand.path), f"{type(exc).__name__}: {exc}")
            )
    return report
