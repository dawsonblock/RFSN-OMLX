# SPDX-License-Identifier: Apache-2.0
"""
Session archive store.

Status: **experimental / internal**. This module is a named-session
recovery handle, not a latency feature. Benchmarks show no meaningful
TTFT win versus a cold restart with a warm shared SSD cache; see
``docs/session_archive_after.md`` for the measurements that back this
framing. Do not expand the surface without a concrete operational use
case.

A *metadata-only* on-disk manifest that records, for a given
``(model_name, session_id)`` pair, the ordered list of block hashes that
make up a conversation's KV state. Manifests reference blocks that live
in the paged SSD cache; they never duplicate KV payload bytes.

Layout::

    <root>/<model_slug>/<session_slug>/manifest.json

A manifest is JSON with the shape::

    {
        "version": "1",
        "model_name": "<model_name>",
        "session_id": "<session_id>",
        "block_hashes": ["<hex>", "<hex>", ...]
    }

All load errors raise :class:`SessionArchiveError` with a stable lowercase
substring so operators and tests can match on it:

* ``"unknown session"``        — no manifest on disk for that pair.
* ``"malformed manifest"``     — file is not parseable JSON.
* ``"empty session archive"``  — manifest has zero committed blocks.
* ``"compatibility mismatch"`` — version or stored model_name no longer
  matches what the caller asked to load.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import List, Union

from . import session_archive_metrics as _metrics

__all__ = ["SessionArchiveError", "SessionArchiveStore", "MANIFEST_VERSION"]

_log = logging.getLogger(__name__)


MANIFEST_VERSION = "1"
_MANIFEST_NAME = "manifest.json"
# Conservative slug: keep readable characters, replace everything else.
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SessionArchiveError(RuntimeError):
    """Raised when a session manifest cannot be loaded."""


def _slug(name: str) -> str:
    """Turn an arbitrary model / session identifier into a safe path segment."""
    if not name:
        return "_"
    cleaned = _SLUG_RE.sub("_", name).strip("._-")
    return cleaned or "_"


class SessionArchiveStore:
    """Persists and retrieves per-session block-hash manifests.

    Manifests are tiny JSON files; this class does not hold any KV tensor
    data. Commits are atomic (temp file + rename) so a crash mid-write
    never leaves a partially written manifest behind.
    """

    def __init__(self, root: Union[str, os.PathLike]) -> None:
        self._root = Path(root)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    def _session_dir(self, model_name: str, session_id: str) -> Path:
        return self._root / _slug(model_name) / _slug(session_id)

    def manifest_path(self, model_name: str, session_id: str) -> Path:
        return self._session_dir(model_name, session_id) / _MANIFEST_NAME

    # ------------------------------------------------------------------
    # Commit
    # ------------------------------------------------------------------
    def commit(
        self, model_name: str, session_id: str, block_hashes: List[bytes]
    ) -> Path:
        """Atomically write the manifest for ``(model_name, session_id)``.

        ``block_hashes`` is stored as an ordered list of lowercase hex strings.
        An empty list is accepted (and later rejected on load); this keeps
        the write path simple and lets the load path speak with one voice.
        """
        if not isinstance(block_hashes, list):
            raise TypeError("block_hashes must be a list of bytes")
        for h in block_hashes:
            if not isinstance(h, (bytes, bytearray)):
                raise TypeError("block_hashes entries must be bytes")

        session_dir = self._session_dir(model_name, session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        doc = {
            "version": MANIFEST_VERSION,
            "model_name": model_name,
            "session_id": session_id,
            "block_hashes": [bytes(h).hex() for h in block_hashes],
        }
        data = json.dumps(doc, separators=(",", ":"), sort_keys=True).encode("utf-8")

        # Atomic write: temp file in the same directory, then os.replace.
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
                    # fsync is best-effort (e.g. on some tmpfs).
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
        return manifest

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------
    def load(self, model_name: str, session_id: str) -> List[bytes]:
        """Return the ordered block-hash list for ``(model_name, session_id)``.

        Raises :class:`SessionArchiveError` on any failure, with a stable
        lowercase substring describing the failure class. Every failure
        bumps the ``session_archive_invalid`` counter (reason-tagged) so
        operators can see manifest-health events without changing the
        raise contract.
        """
        try:
            return self._load(model_name, session_id)
        except SessionArchiveError as exc:
            reason = _classify_load_error(str(exc))
            _metrics.bump(_metrics.EVENT_SESSION_ARCHIVE_INVALID, reason=reason)
            _log.warning(
                "session archive load failed: model=%r session=%r reason=%s",
                model_name, session_id, reason,
            )
            raise

    def _load(self, model_name: str, session_id: str) -> List[bytes]:
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
        if stored_version != MANIFEST_VERSION or stored_model != model_name:
            raise SessionArchiveError(
                f"compatibility mismatch: manifest at {manifest} has "
                f"version={stored_version!r} model_name={stored_model!r}, "
                f"caller asked for version={MANIFEST_VERSION!r} "
                f"model_name={model_name!r}"
            )

        hashes_raw = doc.get("block_hashes")
        if not isinstance(hashes_raw, list):
            raise SessionArchiveError(
                f"malformed manifest: {manifest} is missing block_hashes list"
            )

        if len(hashes_raw) == 0:
            raise SessionArchiveError(
                f"empty session archive: manifest {manifest} has no blocks"
            )

        try:
            return [bytes.fromhex(h) for h in hashes_raw]
        except (TypeError, ValueError) as exc:
            raise SessionArchiveError(
                f"malformed manifest: {manifest} has non-hex block entries ({exc})"
            ) from exc


def _classify_load_error(msg: str) -> str:
    """Map a SessionArchiveError message to a stable reason tag."""
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
