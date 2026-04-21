# SPDX-License-Identifier: Apache-2.0
"""Schema-contract tests (Pass 5 — final stabilization).

These tests freeze the public schema surface so it cannot drift silently:

  * manifest version constants (``MANIFEST_VERSION``,
    ``SUPPORTED_MANIFEST_VERSIONS``);
  * portable-bundle version constant (``BUNDLE_VERSION``) and the
    required envelope key set (``_ENVELOPE_KEYS``);
  * the integrity-grade vocabulary exposed to operators;
  * rejection of unknown future manifest versions;
  * rejection of a wrong ``bundle_version`` in a portable bundle;
  * rejection of a bundle that is missing any single required
    envelope key.

No runtime (model, mlx_lm, GPU) is touched. The suite is deterministic
and safe to run in any environment that has the repo installed.
"""

from __future__ import annotations

import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from omlx.cache.session_archive import (
    INTEGRITY_HEALTHY,
    INTEGRITY_INCOMPATIBLE_MODEL,
    INTEGRITY_INVALID_MANIFEST,
    INTEGRITY_MISSING_BLOCKS,
    INTEGRITY_PARTIALLY_EXPORTABLE,
    INTEGRITY_STALE,
    INTEGRITY_UNREADABLE,
    LEGACY_MANIFEST_VERSION,
    MANIFEST_VERSION,
    SUPPORTED_MANIFEST_VERSIONS,
    SessionArchiveError,
    SessionArchiveStore,
)
from omlx.cache.session_archive_portable import (
    BUNDLE_VERSION,
    BundleError,
    _ENVELOPE_KEYS,
    export_session,
    inspect_bundle,
)


def _h(s: str) -> bytes:
    return hashlib.sha256(s.encode()).digest()


def _make_ssd_block(ssd_dir: Path, block_hash: bytes, payload: bytes) -> Path:
    hex_h = block_hash.hex()
    p = ssd_dir / hex_h[0] / f"{hex_h}.safetensors"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def _build_valid_bundle(tmp_path: Path) -> Path:
    archive = tmp_path / "a"
    ssd = tmp_path / "s"
    archive.mkdir(parents=True, exist_ok=True)
    ssd.mkdir(parents=True, exist_ok=True)
    store = SessionArchiveStore(archive)
    h1 = _h("a")
    _make_ssd_block(ssd, h1, b"bytes-a")
    store.commit("m", "s", [h1])
    out = tmp_path / "bundle.tar"
    export_session(store, "m", "s", ssd, out)
    return out


def _rewrite_bundle(bundle: Path, *, envelope_mutator) -> Path:
    """Extract ``bundle``, mutate its ``bundle.json`` in place, re-tar.

    ``envelope_mutator(envelope: dict) -> dict`` returns the envelope
    object that should be written back. Returning ``None`` leaves the
    envelope untouched.
    """
    work = bundle.parent / (bundle.stem + "_work")
    work.mkdir(parents=True, exist_ok=True)
    with tarfile.open(bundle, "r") as tar:
        tar.extractall(work)
    envelope_path = work / "bundle.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    new_env = envelope_mutator(envelope)
    if new_env is not None:
        envelope_path.write_text(json.dumps(new_env), encoding="utf-8")
    out = bundle.parent / (bundle.stem + "_mut.tar")
    with tarfile.open(out, "w") as tar:
        for entry in work.rglob("*"):
            tar.add(entry, arcname=str(entry.relative_to(work)))
    return out


# --------------------------------------------------------------------------
# Manifest-version constants are frozen.
# --------------------------------------------------------------------------
def test_manifest_version_constant_contract() -> None:
    # A silent bump of these strings changes the on-disk compatibility
    # contract. Flag any drift loudly.
    assert MANIFEST_VERSION == "2"
    assert LEGACY_MANIFEST_VERSION == "1"


def test_supported_manifest_versions_contract() -> None:
    # Removing an entry from SUPPORTED_MANIFEST_VERSIONS is a breaking
    # change (legacy v1 manifests would stop loading). Adding one is
    # non-breaking, but must be intentional — hence the equality pin.
    assert SUPPORTED_MANIFEST_VERSIONS == ("1", "2")
    assert MANIFEST_VERSION in SUPPORTED_MANIFEST_VERSIONS
    assert LEGACY_MANIFEST_VERSION in SUPPORTED_MANIFEST_VERSIONS


def test_bundle_version_constant_contract() -> None:
    assert BUNDLE_VERSION == "1"


# --------------------------------------------------------------------------
# Unknown future manifest version is rejected as a compatibility mismatch.
# --------------------------------------------------------------------------
def test_unknown_future_manifest_version_is_rejected(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    manifest_path = store.manifest_path("m", "s")
    doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    doc["version"] = "99"
    manifest_path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SessionArchiveError) as exc_info:
        store.load("m", "s")
    msg = str(exc_info.value)
    assert "compatibility mismatch" in msg
    assert "supported versions" in msg


# --------------------------------------------------------------------------
# model_compat.schema is locked to MANIFEST_VERSION at commit time.
# --------------------------------------------------------------------------
def test_model_compat_schema_is_locked_to_manifest_version(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path)
    store.commit("m", "s", [_h("a")])
    doc = json.loads(store.manifest_path("m", "s").read_text(encoding="utf-8"))
    # v2 committed manifest must carry model_compat with schema ==
    # MANIFEST_VERSION; dropping or stale-pinning this field would
    # silently break compatibility-family checks on import.
    assert doc.get("version") == MANIFEST_VERSION
    compat = doc.get("model_compat")
    assert isinstance(compat, dict)
    assert compat.get("schema") == MANIFEST_VERSION
    assert compat.get("model_name") == "m"


# --------------------------------------------------------------------------
# Integrity-grade vocabulary is a closed set.
# --------------------------------------------------------------------------
def test_integrity_grade_vocabulary_is_fixed() -> None:
    # These strings are the operator-facing vocabulary that retention,
    # replay-check, diff, and the admin CLI all emit. Silent drift here
    # would break scripted filters. Pin the literal values and the full
    # set at once.
    assert INTEGRITY_HEALTHY == "healthy"
    assert INTEGRITY_STALE == "stale"
    assert INTEGRITY_INVALID_MANIFEST == "invalid_manifest"
    assert INTEGRITY_MISSING_BLOCKS == "missing_blocks"
    assert INTEGRITY_INCOMPATIBLE_MODEL == "incompatible_model"
    assert INTEGRITY_UNREADABLE == "unreadable"
    assert INTEGRITY_PARTIALLY_EXPORTABLE == "partially_exportable"
    assert {
        INTEGRITY_HEALTHY,
        INTEGRITY_STALE,
        INTEGRITY_INVALID_MANIFEST,
        INTEGRITY_MISSING_BLOCKS,
        INTEGRITY_INCOMPATIBLE_MODEL,
        INTEGRITY_UNREADABLE,
        INTEGRITY_PARTIALLY_EXPORTABLE,
    } == {
        "healthy",
        "stale",
        "invalid_manifest",
        "missing_blocks",
        "incompatible_model",
        "unreadable",
        "partially_exportable",
    }


# --------------------------------------------------------------------------
# Bundle envelope rejects a wrong bundle_version.
# --------------------------------------------------------------------------
def test_bundle_version_mismatch_is_rejected(tmp_path: Path) -> None:
    valid = _build_valid_bundle(tmp_path)

    def _bump(env):
        env["bundle_version"] = "99"
        return env

    mutated = _rewrite_bundle(valid, envelope_mutator=_bump)
    with pytest.raises(BundleError, match="bundle version mismatch"):
        inspect_bundle(mutated)


# --------------------------------------------------------------------------
# Bundle envelope rejects any single missing required key.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("missing_key", list(_ENVELOPE_KEYS))
def test_bundle_envelope_missing_required_key(
    tmp_path: Path, missing_key: str
) -> None:
    valid = _build_valid_bundle(tmp_path / missing_key)

    def _drop(env, key=missing_key):
        env.pop(key, None)
        return env

    mutated = _rewrite_bundle(valid, envelope_mutator=_drop)
    with pytest.raises(BundleError) as exc_info:
        inspect_bundle(mutated)
    msg = str(exc_info.value)
    # The specific key that is missing must appear in the error so
    # operators can diagnose which producer emitted a bad bundle.
    assert "bundle envelope missing required key" in msg or "bundle version mismatch" in msg
    # For every key except bundle_version, the message must name the
    # missing key. (Dropping bundle_version produces a version-mismatch
    # message instead, which is still a hard rejection.)
    if missing_key != "bundle_version":
        assert repr(missing_key) in msg or missing_key in msg
