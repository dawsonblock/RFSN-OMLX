# SPDX-License-Identifier: Apache-2.0
"""
SessionArchiveStore contract tests.

Pin the manifest contract before the feature grows:

* manifests are metadata-only (ordered references to existing block hashes,
  never duplicate KV payload bytes)
* manifests are namespaced by ``(model_name, session_id)``
* malformed / empty / version-mismatched / unknown-session loads fail clearly

The store lives at ``omlx.cache.session_archive.SessionArchiveStore``.
Tests import inside each function so a missing module fails as a clear,
per-test error that pins the contract rather than poisoning collection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


MODEL = "test-model-A"
SESSION = "session-001"


def _hashes(n: int = 3) -> list[bytes]:
    return [(f"block-{i:02d}".encode("utf-8") + b"\x00" * 32)[:32] for i in range(n)]


def _store(root: Path):
    from omlx.cache.session_archive import SessionArchiveStore

    return SessionArchiveStore(root)


def _error_cls():
    from omlx.cache.session_archive import SessionArchiveError

    return SessionArchiveError


def test_session_manifest_roundtrip_succeeds(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    blocks = _hashes(3)

    writer = _store(root)
    writer.commit(MODEL, SESSION, blocks)

    reader = _store(root)
    loaded = reader.load(MODEL, SESSION)

    assert loaded == blocks, "manifest must preserve block-hash order exactly"


def test_session_manifest_is_namespaced_by_model_and_session(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    blocks_a = _hashes(2)
    blocks_b = [b"\xff" * 32, b"\xee" * 32, b"\xdd" * 32]

    store = _store(root)
    store.commit("model-A", "sess-1", blocks_a)
    store.commit("model-B", "sess-1", blocks_b)

    assert store.load("model-A", "sess-1") == blocks_a
    assert store.load("model-B", "sess-1") == blocks_b
    assert store.load("model-A", "sess-1") != store.load("model-B", "sess-1")

    # Filesystem must contain two distinct manifests for the same session_id
    # under different model namespaces.
    manifests = list(root.rglob("*"))
    manifest_files = [p for p in manifests if p.is_file()]
    assert len(manifest_files) >= 2, (
        "distinct (model, session) pairs must produce distinct manifest files"
    )


def test_malformed_manifest_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    store = _store(root)
    store.commit(MODEL, SESSION, _hashes(2))

    # Locate the manifest file (exact path is implementation-defined, but
    # there must be exactly one file for a single committed session).
    files = [p for p in root.rglob("*") if p.is_file()]
    assert len(files) == 1, f"expected exactly one manifest file, got {files}"
    files[0].write_bytes(b"this is not a manifest{{{{")

    with pytest.raises(_error_cls()) as exc_info:
        store.load(MODEL, SESSION)
    assert "malformed manifest" in str(exc_info.value).lower(), (
        f"error must mention 'malformed manifest': got {exc_info.value!r}"
    )


def test_empty_session_archive_is_rejected_for_restore(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    store = _store(root)
    store.commit(MODEL, SESSION, [])  # explicit empty commit

    with pytest.raises(_error_cls()) as exc_info:
        store.load(MODEL, SESSION)
    assert "empty session archive" in str(exc_info.value).lower(), (
        f"error must mention 'empty session archive': got {exc_info.value!r}"
    )


def test_manifest_references_existing_blocks_without_payload_duplication(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sessions"
    blocks = _hashes(4)

    store = _store(root)
    store.commit(MODEL, SESSION, blocks)

    # No safetensors payloads anywhere under the session root — manifests
    # are metadata-only by construction.
    payloads = list(root.rglob("*.safetensors"))
    assert payloads == [], (
        f"session archive must not duplicate block payloads: found {payloads}"
    )

    # Every manifest file is small and, when JSON, references block hashes
    # by their hex representation.
    manifest_files = [p for p in root.rglob("*") if p.is_file()]
    assert manifest_files, "commit must produce at least one manifest file"
    for p in manifest_files:
        assert p.stat().st_size < 64 * 1024, (
            f"manifest {p} is too large to be metadata-only"
        )
        text = p.read_text(encoding="utf-8", errors="replace")
        for h in blocks:
            assert h.hex() in text, (
                f"manifest {p} must reference block hash {h.hex()!r}"
            )


def test_manifest_version_or_compatibility_mismatch_is_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "sessions"
    store = _store(root)
    store.commit(MODEL, SESSION, _hashes(2))

    files = [p for p in root.rglob("*") if p.is_file()]
    assert len(files) == 1
    manifest_path = files[0]

    raw = manifest_path.read_text(encoding="utf-8")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        pytest.fail(
            "manifest must be JSON so a version/compatibility field can be "
            "inspected and asserted on by operators"
        )

    # Mutate version/model_name to an incompatible value. The store must
    # reject at load time, not silently accept.
    if "version" in doc:
        doc["version"] = "999.999.incompatible"
    else:
        doc["version"] = "999.999.incompatible"
    if "model_name" in doc:
        doc["model_name"] = "some-other-model"
    manifest_path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(_error_cls()) as exc_info:
        store.load(MODEL, SESSION)
    assert "compatibility mismatch" in str(exc_info.value).lower(), (
        f"error must mention 'compatibility mismatch': got {exc_info.value!r}"
    )


def test_unknown_session_raises_clearly(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    store = _store(root)

    with pytest.raises(_error_cls()) as exc_info:
        store.load(MODEL, "never-committed")
    assert "unknown session" in str(exc_info.value).lower(), (
        f"error must mention 'unknown session': got {exc_info.value!r}"
    )
