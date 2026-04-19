# SPDX-License-Identifier: Apache-2.0
"""Session archive store — lineage & recovery layer."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from . import session_archive_metrics as _metrics

__all__ = [
    "SessionArchiveError",
    "SessionArchiveStore",
    "TurnInfo",
    "LineageInfo",
    "ModelCompat",
    "TurnDiff",
    "SessionDiff",
    "ReplayReport",
    "IntegrityGrade",
    "MANIFEST_VERSION",
    "LEGACY_MANIFEST_VERSION",
    "SUPPORTED_MANIFEST_VERSIONS",
    "make_turn_id",
    "diff_sessions",
    "replay_check",
    "classify_integrity",
    "ancestry_chain",
    "INTEGRITY_HEALTHY",
    "INTEGRITY_STALE",
    "INTEGRITY_INVALID_MANIFEST",
    "INTEGRITY_MISSING_BLOCKS",
    "INTEGRITY_INCOMPATIBLE_MODEL",
    "INTEGRITY_UNREADABLE",
    "INTEGRITY_PARTIALLY_EXPORTABLE",
]


# Phase 6: shared integrity-grade label vocabulary. Callers (retention,
# admin CLI, replay-check, diff) use these strings verbatim so operators
# see one consistent label set.
INTEGRITY_HEALTHY = "healthy"
INTEGRITY_STALE = "stale"
INTEGRITY_INVALID_MANIFEST = "invalid_manifest"
INTEGRITY_MISSING_BLOCKS = "missing_blocks"
INTEGRITY_INCOMPATIBLE_MODEL = "incompatible_model"
INTEGRITY_UNREADABLE = "unreadable"
INTEGRITY_PARTIALLY_EXPORTABLE = "partially_exportable"

IntegrityGrade = str  # documentary alias

_log = logging.getLogger(__name__)


MANIFEST_VERSION = "2"
LEGACY_MANIFEST_VERSION = "1"
SUPPORTED_MANIFEST_VERSIONS = (LEGACY_MANIFEST_VERSION, MANIFEST_VERSION)
_MANIFEST_NAME = "manifest.json"
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SessionArchiveError(RuntimeError):
    """Raised when a session manifest cannot be loaded or mutated."""


@dataclass(frozen=True)
class ModelCompat:
    model_name: str
    block_size: Optional[int]
    schema: str = MANIFEST_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "block_size": self.block_size,
            "schema": self.schema,
        }

    @classmethod
    def from_dict(cls, doc: Any) -> "ModelCompat":
        if not isinstance(doc, dict):
            return cls(model_name="", block_size=None, schema=MANIFEST_VERSION)
        bs = doc.get("block_size")
        return cls(
            model_name=str(doc.get("model_name") or ""),
            block_size=int(bs) if isinstance(bs, int) else None,
            schema=str(doc.get("schema") or MANIFEST_VERSION),
        )


@dataclass(frozen=True)
class TurnInfo:
    turn_id: str
    committed_at: float
    block_count: int
    note: Optional[str]
    branch_reason: Optional[str] = None


@dataclass(frozen=True)
class LineageInfo:
    session_id: str
    label: Optional[str]
    description: Optional[str]
    created_at: float
    updated_at: float
    head_turn_id: str
    parent: Optional[Tuple[str, str]]
    model_compat: ModelCompat
    turn_count: int
    task_tag: Optional[str] = None


# Bounded-text validation. Metadata is operator-readable, not a payload
# channel — keep every field tight so a manifest stays a few KB even
# after thousands of turns.
_MAX_LABEL_LEN = 120
_MAX_DESCRIPTION_LEN = 1024
_MAX_NOTE_LEN = 512
_MAX_BRANCH_REASON_LEN = 512
_MAX_TASK_TAG_LEN = 64
_TASK_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def _validate_short_text(
    field: str, value: Optional[str], *, max_len: int
) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SessionArchiveError(
            f"invalid metadata: {field} must be a string, got {type(value).__name__}"
        )
    if len(value) > max_len:
        raise SessionArchiveError(
            f"invalid metadata: {field} exceeds {max_len} characters "
            f"(got {len(value)})"
        )
    if "\x00" in value:
        raise SessionArchiveError(
            f"invalid metadata: {field} contains a NUL byte"
        )
    return value


def _validate_task_tag(value: Optional[str]) -> Optional[str]:
    v = _validate_short_text("task_tag", value, max_len=_MAX_TASK_TAG_LEN)
    if v is None or v == "":
        return v
    if not _TASK_TAG_RE.match(v):
        raise SessionArchiveError(
            f"invalid metadata: task_tag {v!r} must match "
            f"[A-Za-z0-9][A-Za-z0-9._/-]*"
        )
    return v


def _slug(name: str) -> str:
    if not name:
        return "_"
    cleaned = _SLUG_RE.sub("_", name).strip("._-")
    return cleaned or "_"


def make_turn_id(index: int) -> str:
    return f"t-{index:05d}"


class SessionArchiveStore:
    """Persists and retrieves per-session lineage manifests."""

    def __init__(self, root: Union[str, os.PathLike]) -> None:
        self._root = Path(root)

    def _session_dir(self, model_name: str, session_id: str) -> Path:
        return self._root / _slug(model_name) / _slug(session_id)

    def manifest_path(self, model_name: str, session_id: str) -> Path:
        return self._session_dir(model_name, session_id) / _MANIFEST_NAME

    def commit(
        self,
        model_name: str,
        session_id: str,
        block_hashes: List[bytes],
        *,
        note: Optional[str] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
        parent: Optional[Tuple[str, str]] = None,
        block_size: Optional[int] = None,
        branch_reason: Optional[str] = None,
        task_tag: Optional[str] = None,
    ) -> str:
        if not isinstance(block_hashes, list):
            raise TypeError("block_hashes must be a list of bytes")
        for h in block_hashes:
            if not isinstance(h, (bytes, bytearray)):
                raise TypeError("block_hashes entries must be bytes")

        label = _validate_short_text("label", label, max_len=_MAX_LABEL_LEN)
        description = _validate_short_text(
            "description", description, max_len=_MAX_DESCRIPTION_LEN
        )
        note = _validate_short_text("note", note, max_len=_MAX_NOTE_LEN)
        branch_reason = _validate_short_text(
            "branch_reason", branch_reason, max_len=_MAX_BRANCH_REASON_LEN
        )
        task_tag = _validate_task_tag(task_tag)

        session_dir = self._session_dir(model_name, session_id)
        manifest = session_dir / _MANIFEST_NAME
        if not manifest.exists() and session_dir.exists() and any(session_dir.iterdir()):
            raise SessionArchiveError(
                f"workspace already exists: model={model_name!r} session_id={session_id!r} "
                f"directory is not empty at {session_dir}"
            )
        session_dir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        existing_doc: Optional[Dict[str, Any]] = None
        if self.manifest_path(model_name, session_id).exists():
            # Trust policy: never silently repair or replace a malformed
            # manifest on commit. Operators must see the corruption and
            # decide what to do.
            existing_doc = self._load_doc(model_name, session_id)

        def _new_turn(turn_id: str) -> Dict[str, Any]:
            t: Dict[str, Any] = {
                "turn_id": turn_id,
                "committed_at": now,
                "block_hashes": [bytes(h).hex() for h in block_hashes],
                "note": note,
            }
            if branch_reason is not None:
                t["branch_reason"] = branch_reason
            return t

        if existing_doc is None:
            doc: Dict[str, Any] = {
                "version": MANIFEST_VERSION,
                "model_name": model_name,
                "session_id": session_id,
                "label": label,
                "description": description,
                "task_tag": task_tag,
                "created_at": now,
                "updated_at": now,
                "head_turn_id": make_turn_id(1),
                "parent": (
                    {"session_id": parent[0], "turn_id": parent[1]}
                    if parent is not None
                    else None
                ),
                "model_compat": ModelCompat(
                    model_name=model_name, block_size=block_size
                ).to_dict(),
                "turns": [_new_turn(make_turn_id(1))],
            }
        else:
            doc = self._upgrade_to_v2(existing_doc, session_dir)
            self._validate_v2_doc(model_name, session_id, doc)
            next_idx = len(doc["turns"]) + 1
            turn_id = make_turn_id(next_idx)
            doc["turns"].append(_new_turn(turn_id))
            doc["head_turn_id"] = turn_id
            doc["updated_at"] = now
            if label is not None:
                doc["label"] = label
            if description is not None:
                doc["description"] = description
            if task_tag is not None:
                doc["task_tag"] = task_tag
            if block_size is not None and (
                doc.get("model_compat") or {}
            ).get("block_size") is None:
                compat = dict(doc.get("model_compat") or {})
                compat["block_size"] = int(block_size)
                compat.setdefault("model_name", model_name)
                compat["schema"] = MANIFEST_VERSION
                doc["model_compat"] = compat

        self._atomic_write(session_dir, doc)
        return doc["head_turn_id"]

    def init_workspace(
        self,
        model_name: str,
        session_id: str,
        *,
        label: Optional[str] = None,
        description: Optional[str] = None,
        parent: Optional[Tuple[str, str]] = None,
        block_size: Optional[int] = None,
        task_tag: Optional[str] = None,
    ) -> None:
        """Create an empty workspace (manifest with ``turns=[]``).

        Refuses if a manifest already exists for
        ``(model_name, session_id)``. ``load()`` will still raise
        ``SessionArchiveError("empty session archive ...")`` until the
        first :meth:`commit` appends a turn — that invariant is
        preserved so every existing caller keeps working.
        """
        label = _validate_short_text("label", label, max_len=_MAX_LABEL_LEN)
        description = _validate_short_text(
            "description", description, max_len=_MAX_DESCRIPTION_LEN
        )
        task_tag = _validate_task_tag(task_tag)
        session_dir = self._session_dir(model_name, session_id)
        manifest = session_dir / _MANIFEST_NAME
        if manifest.exists() or (session_dir.exists() and any(session_dir.iterdir())):
            raise SessionArchiveError(
                f"workspace already exists: model={model_name!r} "
                f"session_id={session_id!r} at {session_dir}"
            )
        session_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        doc: Dict[str, Any] = {
            "version": MANIFEST_VERSION,
            "model_name": model_name,
            "session_id": session_id,
            "label": label,
            "description": description,
            "task_tag": task_tag,
            "created_at": now,
            "updated_at": now,
            "head_turn_id": "",
            "parent": (
                {"session_id": parent[0], "turn_id": parent[1]}
                if parent is not None
                else None
            ),
            "model_compat": ModelCompat(
                model_name=model_name, block_size=block_size
            ).to_dict(),
            "turns": [],
        }
        self._atomic_write(session_dir, doc)

    def set_label(
        self,
        model_name: str,
        session_id: str,
        *,
        label: Optional[str] = None,
        description: Optional[str] = None,
        task_tag: Optional[str] = None,
    ) -> None:
        label = _validate_short_text("label", label, max_len=_MAX_LABEL_LEN)
        description = _validate_short_text(
            "description", description, max_len=_MAX_DESCRIPTION_LEN
        )
        task_tag = _validate_task_tag(task_tag)
        doc = self.load_raw(model_name, session_id)
        if label is not None:
            doc["label"] = label
        if description is not None:
            doc["description"] = description
        if task_tag is not None:
            doc["task_tag"] = task_tag
        doc["updated_at"] = time.time()
        self._atomic_write(self._session_dir(model_name, session_id), doc)

    def fork(
        self,
        src_model_name: str,
        src_session_id: str,
        dst_session_id: str,
        *,
        at_turn: Optional[str] = None,
        dst_model_name: Optional[str] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
        branch_reason: Optional[str] = None,
        task_tag: Optional[str] = None,
        overwrite: bool = False,
    ) -> str:
        dst_model = dst_model_name or src_model_name

        src = self.load_raw(src_model_name, src_session_id)
        turns = src.get("turns") or []
        if not turns:
            raise SessionArchiveError(
                f"empty session archive: source session {src_session_id!r} "
                f"has no turns to fork from"
            )

        turn_id = at_turn or src.get("head_turn_id") or turns[-1].get("turn_id")
        turn = next((t for t in turns if t.get("turn_id") == turn_id), None)
        if turn is None:
            raise SessionArchiveError(
                f"unknown turn: source session {src_session_id!r} has no "
                f"turn_id={turn_id!r}"
            )

        dst_dir = self._session_dir(dst_model, dst_session_id)
        if dst_dir.exists() and any(dst_dir.iterdir()) and not overwrite:
            raise SessionArchiveError(
                f"fork refused: destination {dst_model!r}/{dst_session_id!r} "
                f"already exists (pass overwrite=True to replace)"
            )

        hashes_hex = list(turn.get("block_hashes") or [])
        try:
            hashes = [bytes.fromhex(h) for h in hashes_hex]
        except (TypeError, ValueError) as exc:
            raise SessionArchiveError(
                f"malformed manifest: source turn {turn_id!r} has non-hex "
                f"entries ({exc})"
            ) from exc

        src_compat = ModelCompat.from_dict(src.get("model_compat"))
        if dst_dir.exists():
            shutil.rmtree(dst_dir)

        self.commit(
            dst_model,
            dst_session_id,
            hashes,
            note=f"forked from {src_session_id!r} at {turn_id}",
            label=(label if label is not None else src.get("label")),
            description=(
                description if description is not None else src.get("description")
            ),
            parent=(src_session_id, turn_id),
            block_size=src_compat.block_size,
            branch_reason=(
                branch_reason or f"branch from {src_session_id!r}@{turn_id}"
            ),
            task_tag=(
                task_tag
                if task_tag is not None
                else src.get("task_tag")
            ),
        )
        return turn_id

    def load(self, model_name: str, session_id: str) -> List[bytes]:
        try:
            doc = self._load_doc(model_name, session_id)
            doc = self._upgrade_to_v2(doc, self._session_dir(model_name, session_id))
            self._validate_v2_doc(model_name, session_id, doc)
            hashes = self._head_hashes(doc, session_id)
        except SessionArchiveError as exc:
            reason = _classify_load_error(str(exc))
            _metrics.bump(_metrics.EVENT_SESSION_ARCHIVE_INVALID, reason=reason)
            _log.warning(
                "session archive load failed: model=%r session=%r reason=%s",
                model_name, session_id, reason,
            )
            raise

        if not hashes:
            msg = (
                f"empty session archive: head turn for session_id={session_id!r} "
                f"has no blocks"
            )
            _metrics.bump(_metrics.EVENT_SESSION_ARCHIVE_INVALID, reason="empty")
            _log.warning(
                "session archive load failed: model=%r session=%r reason=empty",
                model_name, session_id,
            )
            raise SessionArchiveError(msg)
        return hashes

    def load_raw(self, model_name: str, session_id: str) -> Dict[str, Any]:
        doc = self._load_doc(model_name, session_id)
        session_dir = self._session_dir(model_name, session_id)
        doc = self._upgrade_to_v2(doc, session_dir)
        self._validate_v2_doc(model_name, session_id, doc)
        return doc

    def load_head(
        self, model_name: str, session_id: str
    ) -> Tuple[str, List[bytes]]:
        doc = self.load_raw(model_name, session_id)
        hid = str(doc["head_turn_id"])
        return hid, self.load_turn(model_name, session_id, hid)

    def load_turn(
        self, model_name: str, session_id: str, turn_id: str
    ) -> List[bytes]:
        doc = self.load_raw(model_name, session_id)
        for t in doc.get("turns") or []:
            if t.get("turn_id") == turn_id:
                try:
                    return [bytes.fromhex(h) for h in (t.get("block_hashes") or [])]
                except (TypeError, ValueError) as exc:
                    raise SessionArchiveError(
                        f"malformed manifest: turn {turn_id!r} has non-hex "
                        f"block entries ({exc})"
                    ) from exc
        raise SessionArchiveError(
            f"unknown turn: session {session_id!r} has no turn_id={turn_id!r}"
        )

    def list_turns(
        self, model_name: str, session_id: str
    ) -> List[TurnInfo]:
        doc = self.load_raw(model_name, session_id)
        out: List[TurnInfo] = []
        for t in doc.get("turns") or []:
            try:
                count = len(t.get("block_hashes") or [])
            except Exception:
                count = 0
            out.append(
                TurnInfo(
                    turn_id=str(t.get("turn_id") or ""),
                    committed_at=float(t.get("committed_at") or 0.0),
                    block_count=int(count),
                    note=(t.get("note") if t.get("note") is not None else None),
                    branch_reason=(
                        t.get("branch_reason")
                        if t.get("branch_reason") is not None
                        else None
                    ),
                )
            )
        return out

    def lineage(self, model_name: str, session_id: str) -> LineageInfo:
        doc = self.load_raw(model_name, session_id)
        parent_doc = doc.get("parent")
        parent: Optional[Tuple[str, str]] = None
        if isinstance(parent_doc, dict):
            psid = parent_doc.get("session_id")
            ptid = parent_doc.get("turn_id")
            if isinstance(psid, str) and isinstance(ptid, str):
                parent = (psid, ptid)
        return LineageInfo(
            session_id=str(doc.get("session_id") or session_id),
            label=(doc.get("label") if doc.get("label") is not None else None),
            description=(
                doc.get("description") if doc.get("description") is not None else None
            ),
            created_at=float(doc.get("created_at") or 0.0),
            updated_at=float(doc.get("updated_at") or 0.0),
            head_turn_id=str(doc.get("head_turn_id") or ""),
            parent=parent,
            model_compat=ModelCompat.from_dict(doc.get("model_compat")),
            turn_count=len(doc.get("turns") or []),
            task_tag=(doc.get("task_tag") if doc.get("task_tag") is not None else None),
        )

    def _load_doc(self, model_name: str, session_id: str) -> Dict[str, Any]:
        manifest = self.manifest_path(model_name, session_id)
        if not manifest.exists():
            raise SessionArchiveError(
                f"unknown session: no manifest for model={model_name!r} "
                f"session_id={session_id!r}"
            )

        try:
            raw = manifest.read_text(encoding="utf-8")
        except OSError as exc:
            raise SessionArchiveError(
                f"malformed manifest: cannot read {manifest}: {exc}"
            ) from exc

        try:
            doc = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise SessionArchiveError(
                f"malformed manifest: {manifest} is not valid JSON ({exc})"
            ) from exc

        if not isinstance(doc, dict):
            raise SessionArchiveError(
                f"malformed manifest: {manifest} does not contain a JSON object"
            )

        stored_version = doc.get("version")
        stored_model = doc.get("model_name")
        if (
            stored_version not in SUPPORTED_MANIFEST_VERSIONS
            or stored_model != model_name
        ):
            raise SessionArchiveError(
                f"compatibility mismatch: manifest at {manifest} has "
                f"version={stored_version!r} model_name={stored_model!r}, "
                f"caller asked for model_name={model_name!r} "
                f"(supported versions={SUPPORTED_MANIFEST_VERSIONS})"
            )
        return doc

    def _head_hashes(
        self, doc: Dict[str, Any], session_id: str
    ) -> List[bytes]:
        version = doc.get("version")
        if version == LEGACY_MANIFEST_VERSION:
            hashes_raw = doc.get("block_hashes")
            if not isinstance(hashes_raw, list):
                raise SessionArchiveError(
                    f"malformed manifest: v1 manifest for session_id="
                    f"{session_id!r} is missing block_hashes list"
                )
            try:
                return [bytes.fromhex(h) for h in hashes_raw]
            except (TypeError, ValueError) as exc:
                raise SessionArchiveError(
                    f"malformed manifest: v1 manifest for session_id="
                    f"{session_id!r} has non-hex block entries ({exc})"
                ) from exc

        turns = doc.get("turns")
        if not isinstance(turns, list):
            raise SessionArchiveError(
                f"malformed manifest: v2 manifest for session_id={session_id!r} "
                f"is missing turns list"
            )
        if not turns:
            # Empty workspace (e.g. just init'd) — keep the legacy
            # "empty session archive" vocabulary so callers that pin on
            # it keep working.
            raise SessionArchiveError(
                f"empty session archive: session_id={session_id!r} has no "
                f"turns yet"
            )
        head_id = doc.get("head_turn_id")
        head = next((t for t in turns if t.get("turn_id") == head_id), None)
        if head is None:
            # A head_turn_id that does not resolve to any recorded turn
            # is a structural error — refuse to silently serve a
            # different turn's blocks.
            raise SessionArchiveError(
                f"malformed manifest: head_turn_id={head_id!r} for "
                f"session_id={session_id!r} does not match any recorded turn"
            )
        hashes_raw = head.get("block_hashes")
        if not isinstance(hashes_raw, list):
            raise SessionArchiveError(
                f"malformed manifest: head turn for session_id={session_id!r} "
                f"is missing block_hashes list"
            )
        try:
            return [bytes.fromhex(h) for h in hashes_raw]
        except (TypeError, ValueError) as exc:
            raise SessionArchiveError(
                f"malformed manifest: head turn for session_id={session_id!r} "
                f"has non-hex block entries ({exc})"
            ) from exc

    def _validate_v2_doc(
        self, model_name: str, session_id: str, doc: Dict[str, Any]
    ) -> None:
        if doc.get("version") != MANIFEST_VERSION:
            return

        _validate_short_text("label", doc.get("label"), max_len=_MAX_LABEL_LEN)
        _validate_short_text(
            "description", doc.get("description"), max_len=_MAX_DESCRIPTION_LEN
        )
        _validate_task_tag(doc.get("task_tag"))

        turns = doc.get("turns")
        if not isinstance(turns, list):
            raise SessionArchiveError(
                f"malformed manifest: v2 manifest for session_id={session_id!r} "
                f"is missing turns list"
            )

        seen: set[str] = set()
        for idx, turn in enumerate(turns, start=1):
            if not isinstance(turn, dict):
                raise SessionArchiveError(
                    f"malformed manifest: turn #{idx} for session_id={session_id!r} "
                    f"is not an object"
                )
            turn_id = turn.get("turn_id")
            if not isinstance(turn_id, str):
                raise SessionArchiveError(
                    f"malformed manifest: turn #{idx} for session_id={session_id!r} "
                    f"is missing a string turn_id"
                )
            if turn_id in seen:
                raise SessionArchiveError(
                    f"malformed manifest: duplicate turn_id={turn_id!r} for "
                    f"session_id={session_id!r}"
                )
            seen.add(turn_id)
            expected_turn_id = make_turn_id(idx)
            if turn_id != expected_turn_id:
                raise SessionArchiveError(
                    f"malformed manifest: out-of-order turn history for "
                    f"session_id={session_id!r}; expected {expected_turn_id!r}, "
                    f"got {turn_id!r}"
                )
            _validate_short_text("note", turn.get("note"), max_len=_MAX_NOTE_LEN)
            _validate_short_text(
                "branch_reason",
                turn.get("branch_reason"),
                max_len=_MAX_BRANCH_REASON_LEN,
            )
            if not isinstance(turn.get("block_hashes") or [], list):
                raise SessionArchiveError(
                    f"malformed manifest: turn {turn_id!r} for session_id={session_id!r} "
                    f"is missing block_hashes list"
                )

        parent_doc = doc.get("parent")
        if parent_doc is not None:
            if not isinstance(parent_doc, dict):
                raise SessionArchiveError(
                    f"malformed manifest: parent for session_id={session_id!r} must be an object"
                )
            psid = parent_doc.get("session_id")
            ptid = parent_doc.get("turn_id")
            if not isinstance(psid, str) or not isinstance(ptid, str):
                raise SessionArchiveError(
                    f"malformed manifest: parent for session_id={session_id!r} must carry string session_id and turn_id"
                )
            if psid == session_id:
                raise SessionArchiveError(
                    f"malformed manifest: self-referential parent for session_id={session_id!r}"
                )
            parent_manifest = self.manifest_path(model_name, psid)
            if parent_manifest.exists():
                parent_raw = self._load_doc(model_name, psid)
                parent_v2 = self._upgrade_to_v2(parent_raw, self._session_dir(model_name, psid))
                parent_turns = parent_v2.get("turns") or []
                if not any(t.get("turn_id") == ptid for t in parent_turns if isinstance(t, dict)):
                    raise SessionArchiveError(
                        f"malformed manifest: missing parent turn {ptid!r} in "
                        f"workspace {psid!r} for session_id={session_id!r}"
                    )

    def _upgrade_to_v2(
        self, doc: Dict[str, Any], session_dir: Path
    ) -> Dict[str, Any]:
        if doc.get("version") == MANIFEST_VERSION:
            doc.setdefault("label", None)
            doc.setdefault("description", None)
            doc.setdefault("task_tag", None)
            doc.setdefault("parent", None)
            doc.setdefault(
                "model_compat",
                {
                    "model_name": doc.get("model_name") or "",
                    "block_size": None,
                    "schema": MANIFEST_VERSION,
                },
            )
            doc.setdefault("created_at", doc.get("updated_at") or 0.0)
            doc.setdefault("updated_at", doc.get("created_at") or 0.0)
            doc.setdefault("turns", [])
            for turn in doc.get("turns") or []:
                if isinstance(turn, dict):
                    turn.setdefault("note", None)
                    turn.setdefault("branch_reason", None)
            return doc

        manifest = session_dir / _MANIFEST_NAME
        try:
            ts = manifest.stat().st_mtime if manifest.exists() else time.time()
        except OSError:
            ts = time.time()

        hashes_raw = doc.get("block_hashes") or []
        turn_id = make_turn_id(1)
        upgraded: Dict[str, Any] = {
            "version": MANIFEST_VERSION,
            "model_name": doc.get("model_name") or "",
            "session_id": doc.get("session_id") or "",
            "label": None,
            "description": None,
            "created_at": ts,
            "updated_at": ts,
            "head_turn_id": turn_id,
            "parent": None,
            "model_compat": {
                "model_name": doc.get("model_name") or "",
                "block_size": None,
                "schema": MANIFEST_VERSION,
            },
            "turns": [
                {
                    "turn_id": turn_id,
                    "committed_at": ts,
                    "block_hashes": list(hashes_raw),
                    "note": "migrated from v1",
                }
            ],
        }
        return upgraded

    def _atomic_write(self, session_dir: Path, doc: Dict[str, Any]) -> None:
        session_dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        manifest = session_dir / _MANIFEST_NAME
        fd, tmp_name = tempfile.mkstemp(
            prefix=".manifest.", suffix=".tmp", dir=str(session_dir)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, manifest)
        except Exception:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            _metrics.bump(_metrics.EVENT_MANIFEST_COMMIT_FAILED)
            raise
        _metrics.bump(_metrics.EVENT_MANIFEST_COMMITTED)


def _classify_load_error(msg: str) -> str:
    lowered = msg.lower()
    if "unknown session" in lowered or "unknown turn" in lowered:
        return "unknown"
    if "malformed manifest" in lowered:
        return "malformed"
    if "empty session archive" in lowered:
        return "empty"
    if "compatibility mismatch" in lowered:
        return "compat"
    return "unreadable"


# ---------------------------------------------------------------------------
# Phase 3: diff
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TurnDiff:
    turn_id_a: Optional[str]
    turn_id_b: Optional[str]
    block_count_a: int
    block_count_b: int
    common_prefix_blocks: int
    diverged: bool


@dataclass(frozen=True)
class SessionDiff:
    session_a: Tuple[str, str]  # (model_name, session_id)
    session_b: Tuple[str, str]
    common_ancestor: Optional[Tuple[str, str]]  # (session_id, turn_id) or None
    turn_count_a: int
    turn_count_b: int
    shared_turn_count: int
    per_turn: List[TurnDiff]


def _turn_hashes_hex(doc: Dict[str, Any], turn_id: str) -> List[str]:
    for t in doc.get("turns") or []:
        if t.get("turn_id") == turn_id:
            return list(t.get("block_hashes") or [])
    return []


def diff_sessions(
    store: "SessionArchiveStore",
    a_model: str,
    a_session: str,
    b_model: str,
    b_session: str,
) -> SessionDiff:
    """Compare two sessions by turn. Metadata-only; no SSD access."""
    a = store.load_raw(a_model, a_session)
    b = store.load_raw(b_model, b_session)

    a_turns = a.get("turns") or []
    b_turns = b.get("turns") or []

    # Common ancestor: if B's parent matches A's (session_id, turn_id) or
    # vice versa, that's the ancestor. Otherwise None.
    ancestor: Optional[Tuple[str, str]] = None
    b_parent = b.get("parent")
    if isinstance(b_parent, dict):
        psid = b_parent.get("session_id")
        ptid = b_parent.get("turn_id")
        if psid == a.get("session_id") and isinstance(ptid, str):
            ancestor = (psid, ptid)
    if ancestor is None:
        a_parent = a.get("parent")
        if isinstance(a_parent, dict):
            psid = a_parent.get("session_id")
            ptid = a_parent.get("turn_id")
            if psid == b.get("session_id") and isinstance(ptid, str):
                ancestor = (psid, ptid)

    per_turn: List[TurnDiff] = []
    n = max(len(a_turns), len(b_turns))
    shared = 0
    for i in range(n):
        ta = a_turns[i] if i < len(a_turns) else None
        tb = b_turns[i] if i < len(b_turns) else None
        ha = list((ta or {}).get("block_hashes") or []) if ta else []
        hb = list((tb or {}).get("block_hashes") or []) if tb else []
        # common-prefix block count
        prefix = 0
        for x, y in zip(ha, hb):
            if x == y:
                prefix += 1
            else:
                break
        diverged = ha != hb
        if ta is not None and tb is not None and not diverged:
            shared += 1
        per_turn.append(
            TurnDiff(
                turn_id_a=(ta or {}).get("turn_id") if ta else None,
                turn_id_b=(tb or {}).get("turn_id") if tb else None,
                block_count_a=len(ha),
                block_count_b=len(hb),
                common_prefix_blocks=prefix,
                diverged=diverged,
            )
        )
    return SessionDiff(
        session_a=(a_model, a_session),
        session_b=(b_model, b_session),
        common_ancestor=ancestor,
        turn_count_a=len(a_turns),
        turn_count_b=len(b_turns),
        shared_turn_count=shared,
        per_turn=per_turn,
    )


# ---------------------------------------------------------------------------
# Phase 4: replay-check
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReplayReport:
    session_id: str
    model_name: str
    head_turn_id: str
    total_blocks: int
    present_blocks: int
    missing_blocks: List[str]  # hex ids
    replayable: bool
    grade: str  # IntegrityGrade


def replay_check(
    store: "SessionArchiveStore",
    model_name: str,
    session_id: str,
    has_block: "callable",
    *,
    turn_id: Optional[str] = None,
    expected_model_name: Optional[str] = None,
) -> ReplayReport:
    """Validate that every block referenced by the chosen turn (default:
    head) is still present in the paged SSD cache. ``has_block`` is a
    callable ``(block_hash_bytes) -> bool`` — typically
    ``PagedSSDCacheManager.has_block``. No tensor bytes are touched.

    When ``expected_model_name`` is provided and does not match the
    manifest's ``model_name``, the report is graded
    ``incompatible_model`` and ``replayable=False`` without probing the
    SSD cache.
    """
    try:
        doc = store.load_raw(model_name, session_id)
    except SessionArchiveError as exc:
        return ReplayReport(
            session_id=session_id,
            model_name=model_name,
            head_turn_id="",
            total_blocks=0,
            present_blocks=0,
            missing_blocks=[],
            replayable=False,
            grade=(
                INTEGRITY_INVALID_MANIFEST
                if "malformed" in str(exc).lower()
                or "compatibility" in str(exc).lower()
                else INTEGRITY_UNREADABLE
            ),
        )

    if expected_model_name and doc.get("model_name") != expected_model_name:
        return ReplayReport(
            session_id=session_id,
            model_name=model_name,
            head_turn_id=str(doc.get("head_turn_id") or ""),
            total_blocks=0,
            present_blocks=0,
            missing_blocks=[],
            replayable=False,
            grade=INTEGRITY_INCOMPATIBLE_MODEL,
        )

    tid = turn_id or str(doc.get("head_turn_id") or "")
    hashes_hex = _turn_hashes_hex(doc, tid)
    if not hashes_hex:
        return ReplayReport(
            session_id=session_id,
            model_name=model_name,
            head_turn_id=tid,
            total_blocks=0,
            present_blocks=0,
            missing_blocks=[],
            replayable=False,
            grade=INTEGRITY_UNREADABLE,
        )

    missing: List[str] = []
    present = 0
    for hex_h in hashes_hex:
        try:
            raw = bytes.fromhex(hex_h)
        except (TypeError, ValueError):
            missing.append(hex_h)
            continue
        try:
            ok = bool(has_block(raw))
        except Exception:
            ok = False
        if ok:
            present += 1
        else:
            missing.append(hex_h)

    replayable = not missing
    grade = INTEGRITY_HEALTHY if replayable else INTEGRITY_MISSING_BLOCKS
    return ReplayReport(
        session_id=session_id,
        model_name=model_name,
        head_turn_id=tid,
        total_blocks=len(hashes_hex),
        present_blocks=present,
        missing_blocks=missing,
        replayable=replayable,
        grade=grade,
    )


# ---------------------------------------------------------------------------
# Phase 6: integrity grade classifier (metadata-level)
# ---------------------------------------------------------------------------
def classify_integrity(
    store: "SessionArchiveStore",
    model_name: str,
    session_id: str,
    *,
    expected_model_name: Optional[str] = None,
    stale_after_seconds: Optional[float] = None,
    now: Optional[float] = None,
) -> str:
    """Return an integrity grade for a session without touching SSD payload.

    ``expected_model_name`` — if provided and different from the manifest's
    ``model_name``, grade is ``incompatible_model``.
    ``stale_after_seconds`` — if the last ``updated_at`` is older than this,
    a healthy session is graded ``stale`` instead.
    """
    try:
        doc = store.load_raw(model_name, session_id)
    except SessionArchiveError as exc:
        lowered = str(exc).lower()
        if "compatibility" in lowered:
            return INTEGRITY_INCOMPATIBLE_MODEL
        if "malformed" in lowered:
            return INTEGRITY_INVALID_MANIFEST
        if "unknown" in lowered:
            return INTEGRITY_UNREADABLE
        return INTEGRITY_UNREADABLE

    if expected_model_name and doc.get("model_name") != expected_model_name:
        return INTEGRITY_INCOMPATIBLE_MODEL

    if stale_after_seconds is not None:
        updated = float(doc.get("updated_at") or 0.0)
        t_now = float(now if now is not None else time.time())
        if updated > 0 and (t_now - updated) > stale_after_seconds:
            return INTEGRITY_STALE

    return INTEGRITY_HEALTHY


# ---------------------------------------------------------------------------
# Phase 2 (reframe): ancestry walk
# ---------------------------------------------------------------------------
def ancestry_chain(
    store: "SessionArchiveStore",
    model_name: str,
    session_id: str,
    *,
    max_depth: int = 64,
) -> List[Tuple[str, str]]:
    """Walk ``parent`` links upward from a workspace back to the root.

    Returns a list of ``(session_id, turn_id)`` pairs starting with the
    workspace itself at index 0 and ending at the root (a workspace
    whose ``parent`` is ``None``). The walk stops:

    * at the root (normal termination — the last entry's ``turn_id`` is
      the root's ``head_turn_id``),
    * at an unreachable parent (the parent session does not exist in
      this archive — the chain still contains every reachable ancestor
      and the last entry is the parent reference recorded on the first
      unreachable workspace; callers can detect this by checking that
      the referenced workspace does not load),
    * when ``max_depth`` is exceeded (cycle guard — raises
      :class:`SessionArchiveError`).

    Metadata-only; no SSD access.
    """
    out: List[Tuple[str, str]] = []
    try:
        doc = store.load_raw(model_name, session_id)
    except SessionArchiveError as exc:
        raise SessionArchiveError(
            f"ancestry_chain: cannot load starting workspace "
            f"model={model_name!r} session_id={session_id!r}: {exc}"
        ) from exc
    current_model = model_name
    current_session = str(doc.get("session_id") or session_id)
    current_head = str(doc.get("head_turn_id") or "")
    out.append((current_session, current_head))
    seen = {(current_model, current_session)}

    for _ in range(max_depth):
        parent = doc.get("parent")
        if not isinstance(parent, dict):
            return out
        psid = parent.get("session_id")
        ptid = parent.get("turn_id")
        if not isinstance(psid, str) or not isinstance(ptid, str):
            return out
        key = (current_model, psid)
        if key in seen:
            raise SessionArchiveError(
                f"ancestry_chain: cycle detected at "
                f"model={current_model!r} session_id={psid!r}"
            )
        seen.add(key)
        try:
            parent_doc = store.load_raw(current_model, psid)
        except SessionArchiveError:
            # Unreachable parent — record the dangling reference and
            # return. Caller can detect by trying to load the last
            # tuple's session_id.
            out.append((psid, ptid))
            return out
        out.append((psid, ptid))
        doc = parent_doc
        current_session = psid
    raise SessionArchiveError(
        f"ancestry_chain: exceeded max_depth={max_depth} starting from "
        f"model={model_name!r} session_id={session_id!r}"
    )
