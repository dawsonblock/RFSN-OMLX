# SPDX-License-Identifier: Apache-2.0
"""Service layer for the UI bridge.

Every function here composes existing ``omlx.cache.session_archive*``
primitives — no new behaviour. Trust rules preserved:

* no live ``PagedSSDCacheManager`` ever instantiated;
* conflict policy defaults to ``fail``;
* malformed manifests surface their exact error text.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .. import _version as _omlx_version
from ..cache.session_archive import (
    INTEGRITY_HEALTHY,
    INTEGRITY_PARTIALLY_EXPORTABLE,
    MANIFEST_VERSION,
    SUPPORTED_MANIFEST_VERSIONS,
    SessionArchiveError,
    SessionArchiveStore,
    TurnInfo as _TurnInfo,
    ancestry_chain,
    classify_integrity,
    diff_sessions,
    replay_check,
)
from ..cache.session_archive_metrics import snapshot as _metrics_snapshot
from ..cache.session_archive_portable import (
    BUNDLE_VERSION,
    BundleError,
    ExportResult,
    ImportResult,
    export_session,
    import_session,
    inspect_bundle,
    is_bundle_pinned,
    iter_bundles,
    set_bundle_pinned,
)
from ..cache.session_archive_retention import (
    HEALTHY_STALE_DAYS,
    PRUNE_CLASSES,
    PruneCandidate as _PruneCandidate,
    PruneReport as _PruneReport,
    _slug,
    classify_candidates,
    execute_plan,
    iter_sessions,
    plan_prune,
)

# ---------------------------------------------------------------------------
# Read-only SSD probe — duplicated from scripts/session_archive_admin.py
# so the bridge does not reach across the scripts/ boundary.  Trust rule:
# do NOT instantiate PagedSSDCacheManager here; that live manager can
# scan, quarantine, or otherwise mutate unrelated cache state.
# ---------------------------------------------------------------------------
class ReadOnlySSDProbe:
    """Minimal read-only SSD-cache presence probe."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def has_block(self, block_hash: Any) -> bool:
        if isinstance(block_hash, (bytes, bytearray)):
            hex_h = bytes(block_hash).hex()
        elif isinstance(block_hash, str):
            hex_h = block_hash.strip().lower()
        else:
            return False
        if not hex_h:
            return False
        return (self._cache_dir / hex_h[0] / f"{hex_h}.safetensors").exists()


# ---------------------------------------------------------------------------
# Archive-root resolution.
# ---------------------------------------------------------------------------
_ENV_ARCHIVE_ROOT = "OMLX_UI_ARCHIVE_ROOT"
_ENV_SSD_CACHE_DIR = "OMLX_UI_SSD_CACHE_DIR"
_ENV_BASE_PATH = "OMLX_UI_BASE_PATH"


def _default_base_path() -> Path:
    override = os.environ.get(_ENV_BASE_PATH)
    if override:
        return Path(override).expanduser().resolve()
    try:
        from ..settings import get_settings  # lazy: settings init may fail in tests

        base = Path(get_settings().base_path).expanduser().resolve()
    except Exception:
        base = (Path.home() / ".omlx").resolve()
    return base


def get_archive_root() -> Path:
    override = os.environ.get(_ENV_ARCHIVE_ROOT)
    if override:
        return Path(override).expanduser().resolve()
    return _default_base_path() / "session_archive"


def get_ssd_cache_dir() -> Path:
    override = os.environ.get(_ENV_SSD_CACHE_DIR)
    if override:
        return Path(override).expanduser().resolve()
    try:
        from ..settings import get_settings

        s = get_settings()
        return s.cache.get_ssd_cache_dir(Path(s.base_path)).resolve()
    except Exception:
        return (_default_base_path() / "cache").resolve()


def get_bundle_export_dir() -> Path:
    d = _default_base_path() / "ui_exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_bundle_import_dir() -> Path:
    d = _default_base_path() / "ui_imports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_store() -> SessionArchiveStore:
    root = get_archive_root()
    root.mkdir(parents=True, exist_ok=True)
    return SessionArchiveStore(root)


def get_probe() -> ReadOnlySSDProbe:
    return ReadOnlySSDProbe(get_ssd_cache_dir())


# ---------------------------------------------------------------------------
# Cross-model enumeration (not a primitive — see gaps in docs/ui_plan.md).
# ---------------------------------------------------------------------------
def iter_model_names(store: SessionArchiveStore) -> Iterable[str]:
    """Yield every model-slug directory under the archive root.

    The store slugifies names on disk but records the ORIGINAL model_name
    inside each manifest. We return the un-slugged canonical name taken
    from the first manifest we can read in each directory (falling back
    to the slug when no manifest is loadable).
    """
    root = getattr(store, "_root", None)
    if root is None or not root.is_dir():
        return []
    out: List[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        canonical: Optional[str] = None
        for session_dir in sorted(child.iterdir()):
            if not session_dir.is_dir():
                continue
            manifest = session_dir / "manifest.json"
            if not manifest.exists():
                continue
            try:
                doc = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(doc, dict) and isinstance(doc.get("model_name"), str):
                canonical = doc["model_name"]
                break
        out.append(canonical or child.name)
    return out


# ---------------------------------------------------------------------------
# Summary / detail composition.
# ---------------------------------------------------------------------------
def _safe_classify(
    store: SessionArchiveStore, model_name: str, session_id: str, *, now: float
) -> str:
    try:
        return classify_integrity(
            store,
            model_name,
            session_id,
            stale_after_seconds=HEALTHY_STALE_DAYS * 86400,
            now=now,
        )
    except Exception:  # pragma: no cover — defensive
        return "unreadable"


def _children_count(
    store: SessionArchiveStore, model_name: str, session_id: str
) -> int:
    count = 0
    for desc in iter_sessions(store, model_name):
        if desc.session_id == session_id:
            continue
        try:
            doc = store.load_raw(model_name, desc.session_id)
        except SessionArchiveError:
            continue
        parent = doc.get("parent")
        if isinstance(parent, dict) and parent.get("session_id") == session_id:
            count += 1
    return count


def _exportable_from_grade(grade: str, *, probe: ReadOnlySSDProbe, store: SessionArchiveStore,
                           model_name: str, session_id: str) -> bool:
    # A workspace is exportable iff every referenced block resolves.
    if grade in ("invalid_manifest", "unreadable", "incompatible_model"):
        return False
    try:
        report = replay_check(
            store,
            model_name,
            session_id,
            probe.has_block,
            refresh_last_used=False,
        )
    except Exception:  # pragma: no cover — defensive
        return False
    return bool(report.replayable)


def list_workspaces(
    store: SessionArchiveStore,
    probe: ReadOnlySSDProbe,
    *,
    status_filter: Optional[str] = None,
    pinned_filter: Optional[bool] = None,
    model_filter: Optional[str] = None,
    exportable_filter: Optional[bool] = None,
    now: Optional[float] = None,
    include_exportable_probe: bool = False,
) -> List[Dict[str, Any]]:
    ts = float(now) if now is not None else time.time()
    out: List[Dict[str, Any]] = []
    for model in iter_model_names(store):
        if model_filter is not None and model != model_filter:
            continue
        for desc in iter_sessions(store, model):
            sid = desc.session_id
            try:
                doc = store.load_raw(model, sid)
            except SessionArchiveError:
                doc = None
            lineage = None
            turn_count = 0
            head = ""
            updated = desc.mtime
            label = None
            task_tag = None
            last_used = None
            pinned = False
            has_parent = False
            compat = {"model_name": model, "block_size": None, "schema": MANIFEST_VERSION}
            if doc is not None:
                turn_count = len(doc.get("turns") or [])
                head = str(doc.get("head_turn_id") or "")
                updated = float(doc.get("updated_at") or desc.mtime)
                label = doc.get("label")
                task_tag = doc.get("task_tag")
                last_used = doc.get("last_used_at")
                pinned = bool(doc.get("pinned"))
                has_parent = isinstance(doc.get("parent"), dict)
                compat = doc.get("model_compat") or compat
            grade = _safe_classify(store, model, sid, now=ts)
            if status_filter and grade != status_filter:
                continue
            if pinned_filter is not None and bool(pinned) != bool(pinned_filter):
                continue
            exportable = False
            if include_exportable_probe and doc is not None and turn_count > 0:
                exportable = _exportable_from_grade(
                    grade, probe=probe, store=store, model_name=model, session_id=sid
                )
            if exportable_filter is not None and bool(exportable) != bool(exportable_filter):
                continue
            out.append(
                {
                    "model_name": model,
                    "session_id": sid,
                    "label": label,
                    "head_turn_id": head,
                    "turn_count": turn_count,
                    "updated_at": updated,
                    "last_used_at": float(last_used) if last_used is not None else None,
                    "pinned": pinned,
                    "integrity_grade": grade,
                    "branch_count": _children_count(store, model, sid) if doc is not None else 0,
                    "has_parent": has_parent,
                    "exportable": exportable,
                    "model_compat": {
                        "model_name": str(compat.get("model_name") or model),
                        "block_size": compat.get("block_size"),
                        "schema": str(compat.get("schema") or MANIFEST_VERSION),
                    },
                    "task_tag": task_tag,
                }
            )
    return out


def get_workspace_detail(
    store: SessionArchiveStore,
    probe: ReadOnlySSDProbe,
    model_name: str,
    session_id: str,
    *,
    validate: bool = False,
    include_raw: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    doc = store.load_raw(model_name, session_id)
    lineage = store.lineage(model_name, session_id)
    turns: List[_TurnInfo] = store.list_turns(model_name, session_id)
    ts = float(now) if now is not None else time.time()
    grade = _safe_classify(store, model_name, session_id, now=ts)
    replay_obj = None
    exportable = False
    if validate and lineage.turn_count > 0:
        replay_obj = replay_check(
            store,
            model_name,
            session_id,
            probe.has_block,
            refresh_last_used=False,
        )
        exportable = bool(replay_obj.replayable)
    elif lineage.turn_count > 0:
        exportable = _exportable_from_grade(
            grade,
            probe=probe,
            store=store,
            model_name=model_name,
            session_id=session_id,
        )
    branch_reason: Optional[str] = None
    if lineage.parent is not None and turns:
        # fork() writes branch_reason on the FIRST turn of the child workspace.
        branch_reason = turns[0].branch_reason
    return {
        "model_name": model_name,
        "session_id": session_id,
        "lineage": {
            "session_id": lineage.session_id,
            "label": lineage.label,
            "description": lineage.description,
            "created_at": lineage.created_at,
            "updated_at": lineage.updated_at,
            "head_turn_id": lineage.head_turn_id,
            "parent": list(lineage.parent) if lineage.parent is not None else None,
            "model_compat": {
                "model_name": lineage.model_compat.model_name,
                "block_size": lineage.model_compat.block_size,
                "schema": lineage.model_compat.schema,
            },
            "turn_count": lineage.turn_count,
            "task_tag": lineage.task_tag,
        },
        "turns": [
            {
                "turn_id": t.turn_id,
                "committed_at": t.committed_at,
                "block_count": t.block_count,
                "note": t.note,
                "branch_reason": t.branch_reason,
            }
            for t in turns
        ],
        "pinned": bool(doc.get("pinned")),
        "last_used_at": (
            float(doc.get("last_used_at"))
            if doc.get("last_used_at") is not None
            else None
        ),
        "integrity_grade": grade,
        "exportable": exportable,
        "replay": (
            {
                "session_id": replay_obj.session_id,
                "model_name": replay_obj.model_name,
                "head_turn_id": replay_obj.head_turn_id,
                "total_blocks": replay_obj.total_blocks,
                "present_blocks": replay_obj.present_blocks,
                "missing_blocks": list(replay_obj.missing_blocks),
                "replayable": replay_obj.replayable,
                "grade": replay_obj.grade,
            }
            if replay_obj is not None
            else None
        ),
        "branch_reason": branch_reason,
        "children_count": _children_count(store, model_name, session_id),
        "raw": doc if include_raw else None,
    }


# ---------------------------------------------------------------------------
# Lineage: ancestry + children composition.
# ---------------------------------------------------------------------------
def _descendants(
    store: SessionArchiveStore, model_name: str, session_id: str
) -> List[Tuple[str, str, Dict[str, Any], int]]:
    """Return list of (session_id, head_turn_id, raw_doc, depth) descendants.

    Breadth-first walk; bounded by the real count of workspaces under the model.
    """
    by_parent: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    for desc in iter_sessions(store, model_name):
        try:
            doc = store.load_raw(model_name, desc.session_id)
        except SessionArchiveError:
            continue
        parent = doc.get("parent")
        if isinstance(parent, dict):
            psid = parent.get("session_id")
            if isinstance(psid, str):
                by_parent.setdefault(psid, []).append((desc.session_id, doc))
    out: List[Tuple[str, str, Dict[str, Any], int]] = []
    stack: List[Tuple[str, int]] = [(session_id, 0)]
    seen: set = {session_id}
    while stack:
        cur, depth = stack.pop(0)
        for child_sid, child_doc in by_parent.get(cur, []):
            if child_sid in seen:
                continue
            seen.add(child_sid)
            head = str(child_doc.get("head_turn_id") or "")
            out.append((child_sid, head, child_doc, depth + 1))
            stack.append((child_sid, depth + 1))
    return out


def build_lineage(
    store: SessionArchiveStore,
    model_name: str,
    session_id: str,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    ts = float(now) if now is not None else time.time()
    try:
        chain = ancestry_chain(store, model_name, session_id)
    except SessionArchiveError as exc:
        raise
    focus = (model_name, session_id)
    ancestors: List[Dict[str, Any]] = []
    dangling: Optional[Tuple[str, str]] = None

    for depth, (psid, ptid) in enumerate(chain):
        role = "self" if depth == 0 else "ancestor"
        label = None
        grade = "unreadable"
        pinned = False
        parent_tuple: Optional[Tuple[str, str]] = None
        branch_reason: Optional[str] = None
        try:
            doc = store.load_raw(model_name, psid)
            label = doc.get("label")
            grade = _safe_classify(store, model_name, psid, now=ts)
            pinned = bool(doc.get("pinned"))
            parent = doc.get("parent")
            if isinstance(parent, dict):
                p_sid = parent.get("session_id")
                p_tid = parent.get("turn_id")
                if isinstance(p_sid, str) and isinstance(p_tid, str):
                    parent_tuple = (p_sid, p_tid)
            turns = doc.get("turns") or []
            if parent_tuple is not None and turns:
                first = turns[0]
                if isinstance(first, dict):
                    branch_reason = first.get("branch_reason")
        except SessionArchiveError:
            # Unreachable ancestor — ancestry_chain appends the dangling ref.
            role = "dangling"
            dangling = (psid, ptid)
        ancestors.append(
            {
                "model_name": model_name,
                "session_id": psid,
                "head_turn_id": ptid,
                "label": label,
                "integrity_grade": grade,
                "branch_reason": branch_reason,
                "pinned": pinned,
                "parent": list(parent_tuple) if parent_tuple is not None else None,
                "depth": -depth,
                "role": role,
            }
        )

    descendants: List[Dict[str, Any]] = []
    for csid, chead, cdoc, depth in _descendants(store, model_name, session_id):
        parent_doc = cdoc.get("parent")
        parent_tuple: Optional[Tuple[str, str]] = None
        if isinstance(parent_doc, dict):
            psid = parent_doc.get("session_id")
            ptid = parent_doc.get("turn_id")
            if isinstance(psid, str) and isinstance(ptid, str):
                parent_tuple = (psid, ptid)
        branch_reason: Optional[str] = None
        turns = cdoc.get("turns") or []
        if turns and isinstance(turns[0], dict):
            branch_reason = turns[0].get("branch_reason")
        descendants.append(
            {
                "model_name": model_name,
                "session_id": csid,
                "head_turn_id": chead,
                "label": cdoc.get("label"),
                "integrity_grade": _safe_classify(store, model_name, csid, now=ts),
                "branch_reason": branch_reason,
                "pinned": bool(cdoc.get("pinned")),
                "parent": list(parent_tuple) if parent_tuple is not None else None,
                "depth": depth,
                "role": "descendant",
            }
        )

    return {
        "focus": list(focus),
        "ancestors": ancestors,
        "descendants": descendants,
        "dangling_parent": list(dangling) if dangling is not None else None,
    }


# ---------------------------------------------------------------------------
# Diff + validate (composed).
# ---------------------------------------------------------------------------
def do_diff(
    store: SessionArchiveStore,
    left_model: str,
    left_session: str,
    right_model: str,
    right_session: str,
) -> Dict[str, Any]:
    res = diff_sessions(store, left_model, left_session, right_model, right_session)
    return {
        "session_a": list(res.session_a),
        "session_b": list(res.session_b),
        "common_ancestor": list(res.common_ancestor) if res.common_ancestor else None,
        "turn_count_a": res.turn_count_a,
        "turn_count_b": res.turn_count_b,
        "shared_turn_count": res.shared_turn_count,
        "per_turn": [
            {
                "turn_id_a": t.turn_id_a,
                "turn_id_b": t.turn_id_b,
                "block_count_a": t.block_count_a,
                "block_count_b": t.block_count_b,
                "common_prefix_blocks": t.common_prefix_blocks,
                "diverged": t.diverged,
            }
            for t in res.per_turn
        ],
    }


def do_validate(
    store: SessionArchiveStore,
    probe: ReadOnlySSDProbe,
    model_name: str,
    session_id: str,
) -> Dict[str, Any]:
    doc = store.load_raw(model_name, session_id)
    report = replay_check(
        store,
        model_name,
        session_id,
        probe.has_block,
        refresh_last_used=False,
    )
    schema = str(doc.get("version") or "")
    return {
        "model_name": model_name,
        "session_id": session_id,
        "integrity_grade": report.grade,
        "replay": {
            "session_id": report.session_id,
            "model_name": report.model_name,
            "head_turn_id": report.head_turn_id,
            "total_blocks": report.total_blocks,
            "present_blocks": report.present_blocks,
            "missing_blocks": list(report.missing_blocks),
            "replayable": report.replayable,
            "grade": report.grade,
        },
        "manifest_schema_version": schema,
        "schema_ok": schema in SUPPORTED_MANIFEST_VERSIONS,
        "exportable": bool(report.replayable)
        or report.grade == INTEGRITY_PARTIALLY_EXPORTABLE,
        "reported_at": time.time(),
    }


# ---------------------------------------------------------------------------
# Transfers.
# ---------------------------------------------------------------------------
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _sanitize_filename(name: str) -> str:
    if not isinstance(name, str) or not name or not _SAFE_NAME_RE.match(name):
        raise ValueError(
            "bundle filename must match [A-Za-z0-9._-]+ (no path separators)"
        )
    return name


def export_workspace(
    store: SessionArchiveStore,
    model_name: str,
    session_id: str,
    *,
    out_filename: Optional[str] = None,
    allow_missing_blocks: bool = False,
) -> ExportResult:
    ssd = get_ssd_cache_dir()
    export_dir = get_bundle_export_dir()
    name = out_filename or f"{_slug(model_name)}__{_slug(session_id)}.omlx-session.tar"
    name = _sanitize_filename(name)
    out = export_dir / name
    return export_session(store, model_name, session_id, ssd, out,
                          allow_missing_blocks=allow_missing_blocks)


def list_bundles(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = root if root is not None else get_bundle_export_dir()
    out: List[Dict[str, Any]] = []
    for b in iter_bundles(root):
        stat = b.stat()
        out.append(
            {
                "path": str(b),
                "size_bytes": int(stat.st_size),
                "mtime": float(stat.st_mtime),
                "pinned": is_bundle_pinned(b),
                "envelope": None,
                "manifest": None,
            }
        )
    return out


def inspect_uploaded_bundle(bundle_filename: str) -> Dict[str, Any]:
    name = _sanitize_filename(bundle_filename)
    path = get_bundle_import_dir() / name
    if not path.exists():
        # Also check the export dir (operator may inspect their own exports).
        alt = get_bundle_export_dir() / name
        if alt.exists():
            path = alt
        else:
            raise BundleError(
                f"bundle not found in ui_imports/ or ui_exports/: {name}"
            )
    info = inspect_bundle(path)
    stat = path.stat()
    return {
        "path": str(path),
        "size_bytes": int(stat.st_size),
        "mtime": float(stat.st_mtime),
        "pinned": is_bundle_pinned(path),
        "envelope": info["envelope"],
        "manifest": info["manifest"],
    }


def import_uploaded_bundle(
    store: SessionArchiveStore,
    bundle_filename: str,
    *,
    conflict_policy: str = "fail",
    re_root_lineage: bool = False,
    expected_model_name: Optional[str] = None,
    expected_block_size: Optional[int] = None,
) -> ImportResult:
    if conflict_policy not in ("fail", "rename", "overwrite"):
        raise BundleError(
            f"unknown conflict_policy: {conflict_policy!r}"
        )
    overwrite = conflict_policy == "overwrite"
    rename = conflict_policy == "rename"
    if overwrite and rename:  # defensive; should be impossible above
        raise BundleError(
            "choose exactly one conflict policy: overwrite_session or rename_on_conflict"
        )
    name = _sanitize_filename(bundle_filename)
    path = get_bundle_import_dir() / name
    if not path.exists():
        raise BundleError(f"bundle not found in ui_imports/: {name}")
    return import_session(
        store,
        path,
        get_ssd_cache_dir(),
        expected_model_name=expected_model_name,
        expected_block_size=expected_block_size,
        overwrite_session=overwrite,
        rename_on_conflict=rename,
        re_root_lineage=re_root_lineage,
    )


def pin_bundle(bundle_filename: str, pinned: bool) -> bool:
    name = _sanitize_filename(bundle_filename)
    path = get_bundle_export_dir() / name
    if not path.exists():
        alt = get_bundle_import_dir() / name
        if alt.exists():
            path = alt
        else:
            raise BundleError(f"bundle not found: {name}")
    return set_bundle_pinned(path, pinned)


# ---------------------------------------------------------------------------
# Maintenance: prune dry-run with signed-plan handshake.
# ---------------------------------------------------------------------------
def _candidate_to_dict(c: _PruneCandidate) -> Dict[str, Any]:
    return {
        "kind": c.kind,
        "reason": c.reason,
        "action": c.action,
        "model_name": c.model_name,
        "session_id": c.session_id,
        "path": str(c.path),
        "age_seconds": float(c.age_seconds),
        "last_used_at": (
            float(c.last_used_at) if c.last_used_at is not None else None
        ),
        "integrity_grade": c.integrity_grade,
        "pinned": bool(c.pinned),
        "prune_class": c.prune_class,
    }


def _sign_plan(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def prune_dry_run(
    store: SessionArchiveStore,
    probe: ReadOnlySSDProbe,
    *,
    classes: List[str],
    model_name: Optional[str] = None,
    include_pinned: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    unknown = set(classes) - set(PRUNE_CLASSES)
    if unknown:
        raise ValueError(
            f"unknown prune class(es): {sorted(unknown)}; known: {list(PRUNE_CLASSES)}"
        )
    ts = float(now) if now is not None else time.time()
    bundle_dir = get_bundle_export_dir()
    models = [model_name] if model_name else list(iter_model_names(store))
    all_candidates: List[_PruneCandidate] = []
    by_reason: Dict[str, List[_PruneCandidate]] = {}
    for m in models:
        plan = plan_prune(
            store,
            probe,
            m,
            classes=classes,
            bundle_dir=bundle_dir,
            include_pinned=include_pinned,
            now=ts,
        )
        all_candidates.extend(plan.candidates)
        for k, v in plan.by_reason.items():
            by_reason.setdefault(k, []).extend(v)
    cand_dicts = [_candidate_to_dict(c) for c in all_candidates]
    by_reason_dicts = {
        k: [_candidate_to_dict(c) for c in v] for k, v in by_reason.items()
    }
    payload = {
        "model_name": model_name,
        "now": ts,
        "include_pinned": include_pinned,
        "requested_classes": sorted(set(classes)),
        "candidates": cand_dicts,
    }
    signature = _sign_plan(payload)
    return {
        "model_name": model_name,
        "now": ts,
        "include_pinned": include_pinned,
        "requested_classes": sorted(set(classes)),
        "candidates": cand_dicts,
        "by_reason": by_reason_dicts,
        "plan_signature": signature,
    }


def prune_execute(
    store: SessionArchiveStore,
    probe: ReadOnlySSDProbe,
    *,
    plan_signature: str,
    confirm: bool,
) -> Dict[str, Any]:
    # Re-generate the plan deterministically and re-sign; if the
    # signature drifts we refuse to execute.  We cannot recover the
    # original classes / include_pinned from the signature alone, so
    # the API requires the client to pass them back — enforced at the
    # route layer.
    raise NotImplementedError  # overridden by execute_from_request below


def execute_from_request(
    store: SessionArchiveStore,
    probe: ReadOnlySSDProbe,
    *,
    classes: List[str],
    model_name: Optional[str],
    include_pinned: bool,
    now: float,
    expected_signature: str,
    confirm: bool,
) -> Dict[str, Any]:
    # Rebuild the plan with the same parameters and the same ``now`` so
    # the signature matches, then execute.
    ts = float(now)
    bundle_dir = get_bundle_export_dir()
    models = [model_name] if model_name else list(iter_model_names(store))
    aggregate = _PruneReport(model_name=model_name or "(all)", dry_run=not confirm)
    cand_dicts: List[Dict[str, Any]] = []
    for m in models:
        plan = plan_prune(
            store,
            probe,
            m,
            classes=classes,
            bundle_dir=bundle_dir,
            include_pinned=include_pinned,
            now=ts,
        )
        cand_dicts.extend([_candidate_to_dict(c) for c in plan.candidates])
        rep = execute_plan(plan, store, confirm=confirm)
        aggregate.considered += rep.considered
        aggregate.deleted.extend(rep.deleted)
        aggregate.errors.extend(rep.errors)
    payload = {
        "model_name": model_name,
        "now": ts,
        "include_pinned": include_pinned,
        "requested_classes": sorted(set(classes)),
        "candidates": cand_dicts,
    }
    if _sign_plan(payload) != expected_signature:
        raise ValueError(
            "plan signature drift: the prune plan changed between dry-run "
            "and execute. Re-run dry-run and try again."
        )
    return {
        "model_name": model_name,
        "dry_run": aggregate.dry_run,
        "considered": aggregate.considered,
        "deleted": list(aggregate.deleted),
        "errors": [list(e) for e in aggregate.errors],
    }


# ---------------------------------------------------------------------------
# Maintenance stats + environment info.
# ---------------------------------------------------------------------------
def maintenance_stats(store: SessionArchiveStore) -> Dict[str, Any]:
    total_ws = 0
    total_bytes = 0
    for model in iter_model_names(store):
        for desc in iter_sessions(store, model):
            total_ws += 1
            total_bytes += int(desc.size_bytes or 0)
    # Bundles in the export dir.
    bundles = list(iter_bundles(get_bundle_export_dir()))
    return {
        "counters": dict(_metrics_snapshot()),
        "archive_root": str(get_archive_root()),
        "total_workspaces": total_ws,
        "total_bytes": total_bytes,
        "total_bundles": len(bundles),
    }


_MLX_LM_PIN_RE = re.compile(
    r"mlx-lm\s*@\s*git\+https://github.com/ml-explore/mlx-lm@([0-9a-fA-F]+)"
)


def _read_pinned_mlx_lm() -> Optional[str]:
    try:
        pp = Path(__file__).resolve().parents[2] / "pyproject.toml"
        text = pp.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    m = _MLX_LM_PIN_RE.search(text)
    return m.group(1) if m else None


def environment_info() -> Dict[str, Any]:
    from .. import _version as _v

    return {
        "omlx_version": getattr(_v, "__version__", "0.0.0"),
        "python_version": _platform.python_version(),
        "platform": {
            "system": _platform.system(),
            "machine": _platform.machine(),
            "release": _platform.release(),
        },
        "manifest_schema_version": MANIFEST_VERSION,
        "supported_manifest_versions": list(SUPPORTED_MANIFEST_VERSIONS),
        "bundle_version": BUNDLE_VERSION,
        "cache_layout": "paged-ssd-safetensors/v1",
        "archive_root": str(get_archive_root()),
        "ssd_cache_dir": str(get_ssd_cache_dir()),
        "base_path": str(_default_base_path()),
        "bundle_export_dir": str(get_bundle_export_dir()),
        "bundle_import_dir": str(get_bundle_import_dir()),
        "mlx_lm_pinned": _read_pinned_mlx_lm(),
    }


def health_check() -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    ok = True

    def _record(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        checks[name] = {"ok": bool(passed), "detail": detail}
        if not passed:
            ok = False

    try:
        root = get_archive_root()
        root.mkdir(parents=True, exist_ok=True)
        # Probe write access by creating a sentinel file.
        probe = root / ".ui_health_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        _record("archive_root_writable", True, str(root))
    except Exception as exc:
        _record("archive_root_writable", False, f"{type(exc).__name__}: {exc}")

    try:
        ssd = get_ssd_cache_dir()
        _record("ssd_cache_dir_exists", ssd.exists(), str(ssd))
    except Exception as exc:  # pragma: no cover — defensive
        _record("ssd_cache_dir_exists", False, f"{type(exc).__name__}: {exc}")

    _record(
        "manifest_schema",
        MANIFEST_VERSION in SUPPORTED_MANIFEST_VERSIONS,
        f"version={MANIFEST_VERSION} supported={list(SUPPORTED_MANIFEST_VERSIONS)}",
    )

    _record(
        "bundle_schema",
        BUNDLE_VERSION == "1",
        f"version={BUNDLE_VERSION}",
    )

    try:  # mlx-lm smoke import — optional, never fail overall health on this.
        import mlx_lm  # noqa: F401

        _record("mlx_lm_importable", True, "")
    except Exception as exc:
        _record("mlx_lm_importable", False, f"{type(exc).__name__}: {exc}")

    return {"ok": ok, "checks": checks, "reported_at": time.time()}
