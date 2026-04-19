# SPDX-License-Identifier: Apache-2.0
"""Portable session bundle: explicit export/import of a session with payload.

Status: **experimental / internal**. This is the one path that copies
KV payload bytes on top of the normal metadata-only session archive —
and it only runs when an operator explicitly asks for it. Bundles are
self-describing and verified on import; they never touch the paged SSD
cache outside of the explicit import call.

On-disk format (tarball, uncompressed by default)::

    <bundle>.omlx-session.tar
    ├── bundle.json                     # envelope, see _ENVELOPE_KEYS
    ├── manifest.json                   # the session's v2 manifest
    └── blocks/<hex>.safetensors        # one file per referenced block

``bundle.json`` records::

    {
        "bundle_version": "1",
        "created_at": <epoch>,
        "model_name": "...",
        "session_id": "...",
        "head_turn_id": "...",
        "block_count": <int>,
        "block_sha256": {"<hex_hash>": "<sha256 of file bytes>", ...},
        "source_cache_layout": "paged-ssd-safetensors/v1"
    }

Import validates every ``block_sha256`` before writing into the target
SSD layout (``<cache_dir>/<hex[0]>/<hex>.safetensors``) and calls
``PagedSSDCacheManager._scan_existing_files`` so the manager rebuilds
its in-memory index.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .session_archive import (
    INTEGRITY_MISSING_BLOCKS,
    INTEGRITY_PARTIALLY_EXPORTABLE,
    MANIFEST_VERSION,
    SessionArchiveError,
    SessionArchiveStore,
    _MANIFEST_NAME,
)

__all__ = [
    "export_session",
    "import_session",
    "inspect_bundle",
    "BUNDLE_VERSION",
    "BundleError",
    "ExportResult",
    "ImportResult",
]

_log = logging.getLogger(__name__)

BUNDLE_VERSION = "1"
_BUNDLE_JSON = "bundle.json"
_BLOCKS_DIR = "blocks"
_ENVELOPE_KEYS = (
    "bundle_version",
    "created_at",
    "model_name",
    "session_id",
    "head_turn_id",
    "block_count",
    "block_sha256",
    "source_cache_layout",
    "source_label",
    "source_description",
    "task_tag",
    "model_compat",
    "platform",
    "exporter_version",
    "git_commit",
)
_CACHE_LAYOUT = "paged-ssd-safetensors/v1"


class BundleError(RuntimeError):
    """Raised when a session bundle cannot be built, read, or verified."""


class ExportResult:
    __slots__ = ("path", "block_count", "missing_block_count", "grade")

    def __init__(
        self,
        path: Path,
        block_count: int,
        missing_block_count: int,
        grade: str,
    ) -> None:
        self.path = path
        self.block_count = block_count
        self.missing_block_count = missing_block_count
        self.grade = grade

    def __repr__(self) -> str:  # pragma: no cover - debug
        return (
            f"ExportResult(path={self.path!r}, blocks={self.block_count}, "
            f"missing={self.missing_block_count}, grade={self.grade!r})"
        )


class ImportResult:
    __slots__ = (
        "model_name",
        "session_id",
        "manifest_path",
        "blocks_written",
        "blocks_skipped",
        "source_session_id",
        "conflict_policy",
        "re_rooted",
        "provenance",
    )

    def __init__(
        self,
        model_name: str,
        session_id: str,
        manifest_path: Path,
        blocks_written: int,
        blocks_skipped: int,
        *,
        source_session_id: Optional[str] = None,
        conflict_policy: str = "fail",
        re_rooted: bool = False,
        provenance: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.model_name = model_name
        self.session_id = session_id
        self.manifest_path = manifest_path
        self.blocks_written = blocks_written
        self.blocks_skipped = blocks_skipped
        self.source_session_id = source_session_id or session_id
        self.conflict_policy = conflict_policy
        self.re_rooted = re_rooted
        self.provenance = provenance or {}

    def __repr__(self) -> str:  # pragma: no cover - debug
        return (
            f"ImportResult(model={self.model_name!r}, "
            f"session={self.session_id!r}, written={self.blocks_written}, "
            f"skipped={self.blocks_skipped}, policy={self.conflict_policy!r}, "
            f"re_rooted={self.re_rooted})"
        )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _block_file_from_layout(cache_dir: Path, hex_hash: str) -> Path:
    return cache_dir / hex_hash[0] / f"{hex_hash}.safetensors"


def _git_commit_for(path: Path) -> Optional[str]:
    try:
        res = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    if res.returncode == 0:
        value = (res.stdout or "").strip()
        return value or None
    return None


def inspect_bundle(bundle_path: Union[str, os.PathLike]) -> Dict[str, Any]:
    """Read a bundle envelope + manifest metadata without mutating state."""
    bp = Path(bundle_path)
    if not bp.exists():
        raise BundleError(f"bundle not found: {bp}")
    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)
        try:
            with tarfile.open(bp, "r") as tar:
                _safe_extract(tar, work)
        except (tarfile.TarError, OSError) as exc:
            raise BundleError(f"bundle unreadable: {exc}") from exc
        envelope_path = work / _BUNDLE_JSON
        manifest_path = work / _MANIFEST_NAME
        if not envelope_path.exists() or not manifest_path.exists():
            raise BundleError(
                f"bundle missing required files ({_BUNDLE_JSON} or {_MANIFEST_NAME})"
            )
        try:
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            raise BundleError(f"bundle metadata unreadable: {exc}") from exc

    if not isinstance(envelope, dict) or not isinstance(manifest, dict):
        raise BundleError("bundle metadata unreadable: envelope/manifest must be objects")
    for key in _ENVELOPE_KEYS:
        if key not in envelope:
            raise BundleError(f"bundle envelope missing required key {key!r}")
    if envelope.get("bundle_version") != BUNDLE_VERSION:
        raise BundleError(
            f"bundle version mismatch: got {envelope.get('bundle_version')!r}, expected {BUNDLE_VERSION!r}"
        )
    if manifest.get("version") != MANIFEST_VERSION:
        raise BundleError(
            f"bundled manifest must be schema v{MANIFEST_VERSION} (got {manifest.get('version')!r})"
        )
    if manifest.get("session_id") != envelope.get("session_id"):
        raise BundleError(
            f"bundle envelope/manifest session_id mismatch: "
            f"{envelope.get('session_id')!r} vs {manifest.get('session_id')!r}"
        )
    if manifest.get("head_turn_id") != envelope.get("head_turn_id"):
        raise BundleError(
            f"bundle envelope/manifest head_turn_id mismatch: "
            f"{envelope.get('head_turn_id')!r} vs {manifest.get('head_turn_id')!r}"
        )
    return {"envelope": envelope, "manifest": manifest}


def export_session(
    store: SessionArchiveStore,
    model_name: str,
    session_id: str,
    ssd_cache_dir: Union[str, os.PathLike],
    out_path: Union[str, os.PathLike],
    *,
    allow_missing_blocks: bool = False,
) -> ExportResult:
    """Export a session manifest + referenced SSD blocks to a tarball.

    Raises :class:`BundleError` when blocks are missing and
    ``allow_missing_blocks`` is False. When True, a
    ``partially_exportable`` bundle is produced and missing blocks are
    omitted (their hashes are still recorded in the manifest).
    """
    try:
        doc = store.load_raw(model_name, session_id)
    except SessionArchiveError as exc:
        raise BundleError(f"cannot export: {exc}") from exc

    ssd_dir = Path(ssd_cache_dir)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not doc.get("turns") or not doc.get("head_turn_id"):
        raise BundleError(
            f"cannot export: empty session archive for model={model_name!r} session_id={session_id!r}"
        )

    # Collect all referenced block hashes across all turns, deduped.
    hex_hashes: List[str] = []
    seen: set = set()
    for turn in doc.get("turns") or []:
        for hex_h in turn.get("block_hashes") or []:
            if hex_h not in seen:
                seen.add(hex_h)
                hex_hashes.append(hex_h)

    present_paths: List[Tuple[str, Path]] = []
    missing: List[str] = []
    for hex_h in hex_hashes:
        block_file = _block_file_from_layout(ssd_dir, hex_h)
        if block_file.exists():
            present_paths.append((hex_h, block_file))
        else:
            missing.append(hex_h)

    if missing and not allow_missing_blocks:
        raise BundleError(
            f"cannot export: {len(missing)} referenced block(s) missing from "
            f"SSD cache {ssd_dir} (e.g. {missing[:3]}); "
            f"pass allow_missing_blocks=True to produce a partial bundle"
        )

    repo_root = Path(__file__).resolve().parents[2]
    envelope = {
        "bundle_version": BUNDLE_VERSION,
        "created_at": time.time(),
        "model_name": model_name,
        "session_id": session_id,
        "head_turn_id": str(doc.get("head_turn_id") or ""),
        "block_count": len(hex_hashes),
        "block_sha256": {},
        "source_cache_layout": _CACHE_LAYOUT,
        "source_label": doc.get("label"),
        "source_description": doc.get("description"),
        "task_tag": doc.get("task_tag"),
        "model_compat": doc.get("model_compat") or {},
        "platform": {
            "system": platform.system(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "exporter_version": MANIFEST_VERSION,
        "git_commit": _git_commit_for(repo_root),
    }

    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)
        blocks_tmp = work / _BLOCKS_DIR
        blocks_tmp.mkdir()
        for hex_h, src in present_paths:
            digest = _sha256_file(src)
            envelope["block_sha256"][hex_h] = digest
            shutil.copy2(src, blocks_tmp / f"{hex_h}.safetensors")

        (work / _MANIFEST_NAME).write_text(
            json.dumps(doc, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
        (work / _BUNDLE_JSON).write_text(
            json.dumps(envelope, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )

        with tarfile.open(out, "w") as tar:
            tar.add(work / _BUNDLE_JSON, arcname=_BUNDLE_JSON)
            tar.add(work / _MANIFEST_NAME, arcname=_MANIFEST_NAME)
            tar.add(work / _BLOCKS_DIR, arcname=_BLOCKS_DIR)

    grade = INTEGRITY_PARTIALLY_EXPORTABLE if missing else "healthy"
    return ExportResult(
        path=out,
        block_count=len(present_paths),
        missing_block_count=len(missing),
        grade=grade,
    )


def _safe_extract(tar: tarfile.TarFile, dst: Path) -> None:
    dst_resolved = dst.resolve()
    for member in tar.getmembers():
        member_path = (dst / member.name).resolve()
        try:
            member_path.relative_to(dst_resolved)
        except ValueError:
            raise BundleError(
                f"bundle contains unsafe path {member.name!r} (path traversal)"
            )
        # Reject symlinks / devices — blocks and manifest are plain files.
        if member.issym() or member.islnk() or member.isdev():
            raise BundleError(
                f"bundle contains disallowed entry type for {member.name!r}"
            )
    tar.extractall(dst)


def import_session(
    store: SessionArchiveStore,
    bundle_path: Union[str, os.PathLike],
    ssd_cache_dir: Union[str, os.PathLike],
    *,
    expected_model_name: Optional[str] = None,
    expected_block_size: Optional[int] = None,
    overwrite_session: bool = False,
    rename_on_conflict: bool = False,
    re_root_lineage: bool = False,
) -> ImportResult:
    """Verify and materialize a session bundle into ``store`` + SSD layout.

    Validates:
      * envelope ``bundle_version``
      * SHA-256 for every block file vs. ``block_sha256``
      * ``model_name`` matches ``expected_model_name`` if provided
      * ``model_compat.block_size`` matches ``expected_block_size`` if
        provided (compatibility-family guard — refuses cross-family
        imports before any bytes land on disk)
      * destination session does not already exist unless
        ``overwrite_session`` is True

    Does NOT start or touch a running ``PagedSSDCacheManager``. Callers
    that need the in-memory index refreshed should call the manager's
    ``_scan_existing_files`` (or restart the process) after import.
    """
    bp = Path(bundle_path)
    ssd_dir = Path(ssd_cache_dir)

    if overwrite_session and rename_on_conflict:
        raise BundleError(
            "choose exactly one conflict policy: overwrite_session or rename_on_conflict"
        )

    if not bp.exists():
        raise BundleError(f"bundle not found: {bp}")

    with tempfile.TemporaryDirectory() as work_str:
        work = Path(work_str)
        try:
            with tarfile.open(bp, "r") as tar:
                _safe_extract(tar, work)
        except (tarfile.TarError, OSError) as exc:
            raise BundleError(f"bundle unreadable: {exc}") from exc

        envelope_path = work / _BUNDLE_JSON
        manifest_path = work / _MANIFEST_NAME
        if not envelope_path.exists() or not manifest_path.exists():
            raise BundleError(
                f"bundle missing required files ({_BUNDLE_JSON} or "
                f"{_MANIFEST_NAME})"
            )

        try:
            envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            raise BundleError(
                f"bundle metadata unreadable: {exc}"
            ) from exc

        for key in _ENVELOPE_KEYS:
            if key not in envelope:
                raise BundleError(
                    f"bundle envelope missing required key {key!r}"
                )
        if envelope["bundle_version"] != BUNDLE_VERSION:
            raise BundleError(
                f"bundle version mismatch: got "
                f"{envelope['bundle_version']!r}, expected "
                f"{BUNDLE_VERSION!r}"
            )
        if envelope.get("source_cache_layout") != _CACHE_LAYOUT:
            raise BundleError(
                f"bundle source_cache_layout mismatch: got "
                f"{envelope.get('source_cache_layout')!r}, expected {_CACHE_LAYOUT!r}"
            )
        if manifest.get("version") != MANIFEST_VERSION:
            raise BundleError(
                f"bundled manifest must be schema v{MANIFEST_VERSION} "
                f"(got {manifest.get('version')!r})"
            )

        model_name = str(envelope["model_name"])
        session_id = str(envelope["session_id"])
        if expected_model_name and model_name != expected_model_name:
            raise BundleError(
                f"bundle model_name={model_name!r} does not match expected "
                f"{expected_model_name!r}"
            )
        if manifest.get("model_name") != model_name:
            raise BundleError(
                f"bundle envelope/manifest model_name mismatch: "
                f"{model_name!r} vs {manifest.get('model_name')!r}"
            )
        if manifest.get("session_id") != session_id:
            raise BundleError(
                f"bundle envelope/manifest session_id mismatch: "
                f"{session_id!r} vs {manifest.get('session_id')!r}"
            )
        if manifest.get("head_turn_id") != envelope.get("head_turn_id"):
            raise BundleError(
                f"bundle envelope/manifest head_turn_id mismatch: "
                f"{envelope.get('head_turn_id')!r} vs {manifest.get('head_turn_id')!r}"
            )

        env_compat = envelope.get("model_compat") or {}
        if env_compat:
            env_model = env_compat.get("model_name")
            if env_model not in (None, "", model_name):
                raise BundleError(
                    f"bundle provenance model_compat mismatch: {env_model!r} vs {model_name!r}"
                )

        if expected_block_size is not None:
            bundled_compat = manifest.get("model_compat") or {}
            bundled_bs = bundled_compat.get("block_size")
            if bundled_bs is None or int(bundled_bs) != int(expected_block_size):
                raise BundleError(
                    f"compatibility mismatch: bundle block_size="
                    f"{bundled_bs!r} does not match expected "
                    f"{int(expected_block_size)!r}"
                )

        # Existing destination guard. Conservative by default:
        # fail on conflict, unless the operator explicitly asks for
        # overwrite or deterministic rename.
        original_session_id = session_id
        dst_session_dir = store._session_dir(model_name, session_id)  # noqa: SLF001
        dst_manifest = store.manifest_path(model_name, session_id)
        dst_exists = dst_session_dir.exists() and any(dst_session_dir.iterdir())
        if dst_exists:
            if overwrite_session:
                shutil.rmtree(dst_session_dir)
                dst_manifest = store.manifest_path(model_name, session_id)
            elif rename_on_conflict:
                suffix = 1
                while True:
                    candidate = f"{original_session_id}-imported-{suffix}"
                    candidate_dir = store._session_dir(model_name, candidate)  # noqa: SLF001
                    if not (candidate_dir.exists() and any(candidate_dir.iterdir())):
                        session_id = candidate
                        manifest["session_id"] = candidate
                        dst_manifest = store.manifest_path(model_name, candidate)
                        break
                    suffix += 1
            else:
                raise BundleError(
                    f"destination session already exists: {model_name!r}/{session_id!r} "
                    f"(default policy: fail). Pass overwrite_session=True or "
                    f"rename_on_conflict=True explicitly."
                )

        if re_root_lineage:
            manifest["parent"] = None

        # Validate the bundled manifest structure after any deterministic
        # import-time rewrite (rename / re-root) but before writing bytes.
        manifest["session_id"] = session_id
        try:
            store._validate_v2_doc(model_name, session_id, manifest)  # noqa: SLF001
        except SessionArchiveError as exc:
            raise BundleError(f"bundled manifest invalid: {exc}") from exc

        # Verify all block SHAs before writing anywhere.
        block_sha = envelope.get("block_sha256") or {}
        if not isinstance(block_sha, dict):
            raise BundleError("bundle envelope block_sha256 is not an object")
        blocks_src = work / _BLOCKS_DIR
        verified: List[Tuple[str, Path]] = []
        for hex_h, expected_sha in block_sha.items():
            src = blocks_src / f"{hex_h}.safetensors"
            if not src.exists():
                raise BundleError(
                    f"bundle missing block file for hash {hex_h}"
                )
            actual = _sha256_file(src)
            if actual != expected_sha:
                raise BundleError(
                    f"block {hex_h} sha256 mismatch: expected "
                    f"{expected_sha!r}, got {actual!r}"
                )
            verified.append((hex_h, src))

        # Everything checks out — install.
        ssd_dir.mkdir(parents=True, exist_ok=True)
        blocks_written = 0
        blocks_skipped = 0
        for hex_h, src in verified:
            dst_file = _block_file_from_layout(ssd_dir, hex_h)
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            if dst_file.exists():
                blocks_skipped += 1
                continue
            # Atomic-ish: copy to temp in same dir then rename.
            tmp = dst_file.parent / f".{hex_h}.import.tmp"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst_file)
            blocks_written += 1

        # Install the manifest via the store (atomic write).
        session_dir = store._session_dir(model_name, session_id)  # noqa: SLF001
        session_dir.mkdir(parents=True, exist_ok=True)
        store._atomic_write(session_dir, manifest)  # noqa: SLF001

    return ImportResult(
        model_name=model_name,
        session_id=session_id,
        manifest_path=dst_manifest,
        blocks_written=blocks_written,
        blocks_skipped=blocks_skipped,
        source_session_id=original_session_id,
        conflict_policy=(
            "overwrite" if overwrite_session else "rename" if session_id != original_session_id else "fail"
        ),
        re_rooted=bool(re_root_lineage),
        provenance=envelope,
    )
