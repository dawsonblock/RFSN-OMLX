# SPDX-License-Identifier: Apache-2.0
"""Pass 6 pruning-policy contract tests.

These tests pin the conservative retention policy described in
``docs/pruning_policy.md``. All clocks are injected (``now=...``) so
the suite is deterministic.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import List, Optional

import pytest

from omlx.cache.session_archive import (
    INTEGRITY_HEALTHY,
    INTEGRITY_STALE,
    SessionArchiveStore,
    replay_check,
)
from omlx.cache.session_archive_portable import (
    bundle_pin_path,
    export_session,
    is_bundle_pinned,
    iter_bundles,
    set_bundle_pinned,
)
from omlx.cache.session_archive_retention import (
    BUNDLE_RETENTION_DAYS,
    HEALTHY_RECENT_DAYS,
    HEALTHY_STALE_DAYS,
    INVALID_GRACE_DAYS,
    ORPHANED_GRACE_DAYS,
    PRUNE_CLASS_EXPORTS,
    PRUNE_CLASS_INVALID,
    PRUNE_CLASS_ORPHANED,
    PRUNE_CLASS_STALE,
    PRUNE_CLASSES,
    REASON_EXPORT_BUNDLE_OLD,
    REASON_HEALTHY_RECENT,
    REASON_HEALTHY_STALE,
    REASON_INVALID_MANIFEST,
    REASON_ORPHANED,
    REASON_PINNED,
    REASON_PROTECTED_LATEST_HEAD,
    classify_candidates,
    execute_plan,
    plan_prune,
)


MODEL = "test-model"


class _AllPresentSSD:
    """Minimal ssd_cache stub: claims every block is present."""

    def has_block(self, _h: bytes) -> bool:  # noqa: D401
        return True


class _NoneSSD:
    def has_block(self, _h: bytes) -> bool:  # noqa: D401
        return False


def _h(tag: str) -> bytes:
    return hashlib.sha256(tag.encode()).digest()


def _hashes(n: int) -> List[bytes]:
    return [_h(f"block-{i}") for i in range(n)]


def _age_manifest(manifest_path: Path, age_seconds: float) -> None:
    """Set the manifest's mtime to ``now - age_seconds``."""
    target = time.time() - age_seconds
    os.utime(manifest_path, (target, target))


def _session_dir(store: SessionArchiveStore, model: str, session: str) -> Path:
    from omlx.cache.session_archive import _slug

    return store._root / _slug(model) / _slug(session)  # noqa: SLF001


# ---------------------------------------------------------------------------
# T1. Class tokens + reason constants are frozen.
# ---------------------------------------------------------------------------
def test_prune_classes_and_reasons_are_frozen() -> None:
    assert set(PRUNE_CLASSES) == {
        "stale",
        "invalid",
        "orphaned",
        "exports",
        "empty",
        "unreadable",
    }
    # Retention windows are coarse integers (days).
    assert HEALTHY_RECENT_DAYS == 30
    assert HEALTHY_STALE_DAYS == 90
    assert INVALID_GRACE_DAYS == 7
    assert ORPHANED_GRACE_DAYS == 14
    assert BUNDLE_RETENTION_DAYS == 21


# ---------------------------------------------------------------------------
# T2. Healthy recent sessions are always protected.
# ---------------------------------------------------------------------------
def test_healthy_recent_is_never_eligible(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "recent", _hashes(3))

    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes=PRUNE_CLASSES,  # request everything
        now=time.time(),
    )
    eligible_ids = {c.session_id for c in plan.eligible}
    assert "recent" not in eligible_ids
    reasons = {c.reason for c in plan.candidates}
    # Recent healthy rows are omitted (class not 'stale') OR protected as
    # latest head. Either way, "recent" is not eligible.
    assert REASON_HEALTHY_RECENT not in reasons or all(
        c.action == "protected"
        for c in plan.candidates
        if c.reason == REASON_HEALTHY_RECENT
    )


# ---------------------------------------------------------------------------
# T3. Healthy stale is eligible only when --prune-stale is requested,
#     AND only if it is not the latest healthy head.
# ---------------------------------------------------------------------------
def test_healthy_stale_eligible_only_with_opt_in(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    # "head" is more recent than "stale_old" — head is protected as latest.
    store.commit(MODEL, "stale_old", _hashes(2))
    stale_manifest = _session_dir(store, MODEL, "stale_old") / "manifest.json"
    _age_manifest(stale_manifest, (HEALTHY_RECENT_DAYS + 5) * 86400)
    store.commit(MODEL, "head", _hashes(2))  # written "now" → latest head

    # Without --prune-stale: nothing eligible.
    plan_nostale = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_INVALID},
        now=time.time(),
    )
    assert plan_nostale.eligible == []

    # With --prune-stale: stale_old becomes eligible; "head" is protected.
    plan_stale = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_STALE},
        now=time.time(),
    )
    eligible_ids = {c.session_id for c in plan_stale.eligible}
    assert "stale_old" in eligible_ids
    assert "head" not in eligible_ids


# ---------------------------------------------------------------------------
# T4. Latest healthy head is always protected even when aged.
# ---------------------------------------------------------------------------
def test_latest_head_protected_regardless_of_age(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "only_head", _hashes(2))
    # Pretend it's ancient — still protected because it's the only head.
    _age_manifest(
        _session_dir(store, MODEL, "only_head") / "manifest.json",
        (HEALTHY_STALE_DAYS + 10) * 86400,
    )
    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_STALE},
        now=time.time(),
    )
    assert plan.eligible == []
    assert any(
        c.reason == REASON_PROTECTED_LATEST_HEAD for c in plan.protected
    )


# ---------------------------------------------------------------------------
# T5. Invalid manifest respects the 7d grace window.
# ---------------------------------------------------------------------------
def test_invalid_manifest_in_grace_is_protected(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "bad", _hashes(2))
    # A healthy head so the invalid one doesn't become the latest.
    store.commit(MODEL, "head", _hashes(2))
    manifest = _session_dir(store, MODEL, "bad") / "manifest.json"
    manifest.write_text("{not json", encoding="utf-8")
    _age_manifest(manifest, (INVALID_GRACE_DAYS - 1) * 86400)

    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_INVALID},
        now=time.time(),
    )
    assert not any(c.session_id == "bad" for c in plan.eligible)


def test_invalid_manifest_past_grace_is_eligible(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "bad", _hashes(2))
    store.commit(MODEL, "head", _hashes(2))
    manifest = _session_dir(store, MODEL, "bad") / "manifest.json"
    manifest.write_text("{not json", encoding="utf-8")
    _age_manifest(manifest, (INVALID_GRACE_DAYS + 1) * 86400)

    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_INVALID},
        now=time.time(),
    )
    assert any(
        c.session_id == "bad" and c.action == "eligible" for c in plan.candidates
    )


# ---------------------------------------------------------------------------
# T6. Orphaned session dirs use their own (14d) window.
# ---------------------------------------------------------------------------
def test_orphaned_dir_respects_14d_window(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "head", _hashes(2))
    from omlx.cache.session_archive import _slug

    orphan_dir = store._root / _slug(MODEL) / "zz_orphan"  # noqa: SLF001
    orphan_dir.mkdir(parents=True, exist_ok=True)
    # In grace → protected.
    target = time.time() - (ORPHANED_GRACE_DAYS - 1) * 86400
    os.utime(orphan_dir, (target, target))

    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_ORPHANED},
        now=time.time(),
    )
    assert not any(c.session_id == "zz_orphan" and c.action == "eligible" for c in plan.candidates)

    # Aged past grace → eligible.
    target = time.time() - (ORPHANED_GRACE_DAYS + 1) * 86400
    os.utime(orphan_dir, (target, target))
    plan2 = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_ORPHANED},
        now=time.time(),
    )
    assert any(
        c.session_id == "zz_orphan"
        and c.reason == REASON_ORPHANED
        and c.action == "eligible"
        for c in plan2.candidates
    )


# ---------------------------------------------------------------------------
# T7. Pinned workspaces are protected; --include-pinned lifts it.
# ---------------------------------------------------------------------------
def test_pinned_workspace_is_protected(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "pinned_old", _hashes(2))
    store.commit(MODEL, "head", _hashes(2))
    # Mark the candidate as stale via last_used_at (survives later writes).
    store.touch_last_used(
        MODEL,
        "pinned_old",
        now=time.time() - (HEALTHY_STALE_DAYS + 5) * 86400,
    )
    store.set_pinned(MODEL, "pinned_old", True)

    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_STALE},
        now=time.time(),
    )
    assert all(c.session_id != "pinned_old" for c in plan.eligible)
    assert any(
        c.session_id == "pinned_old" and c.pinned is True
        for c in plan.protected
    )

    # --include-pinned lifts protection (only when class is requested).
    plan_forced = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_STALE},
        include_pinned=True,
        now=time.time(),
    )
    assert any(
        c.session_id == "pinned_old" and c.action == "eligible"
        for c in plan_forced.candidates
    )


# ---------------------------------------------------------------------------
# T8. Bundles respect the 21d window and the sidecar pin marker.
# ---------------------------------------------------------------------------
def _ssd_with_blocks(ssd: Path, hashes: List[bytes]) -> None:
    for bh in hashes:
        hex_h = bh.hex()
        p = ssd / hex_h[0] / f"{hex_h}.safetensors"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"payload-" + bh[:4])


def test_bundle_retention_window_and_pin(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    ssd = tmp_path / "ssd"
    ssd.mkdir()
    hashes = _hashes(2)
    _ssd_with_blocks(ssd, hashes)
    store.commit(MODEL, "s", hashes)

    bundles_dir = tmp_path / "bundles"
    bundles_dir.mkdir()
    # Two bundles with different ages.
    young = bundles_dir / "young.tar"
    old = bundles_dir / "old.tar"
    export_session(store, MODEL, "s", ssd, young)
    export_session(store, MODEL, "s", ssd, old)
    # Age the "old" bundle past the retention window.
    ts_old = time.time() - (BUNDLE_RETENTION_DAYS + 1) * 86400
    os.utime(old, (ts_old, ts_old))

    # Discovery works and is non-recursive.
    discovered = list(iter_bundles(bundles_dir))
    assert young in discovered and old in discovered

    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_EXPORTS},
        bundle_dir=bundles_dir,
        now=time.time(),
    )
    eligible_paths = {c.path for c in plan.eligible}
    assert old in eligible_paths
    assert young not in eligible_paths

    # Pinning the old bundle via sidecar protects it.
    assert set_bundle_pinned(old, True) is True
    assert is_bundle_pinned(old)
    plan2 = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_EXPORTS},
        bundle_dir=bundles_dir,
        now=time.time(),
    )
    assert old not in {c.path for c in plan2.eligible}


# ---------------------------------------------------------------------------
# T9. execute_plan refuses to delete without confirm=True.
# ---------------------------------------------------------------------------
def test_execute_plan_requires_confirm(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "stale_old", _hashes(2))
    store.commit(MODEL, "head", _hashes(2))
    _age_manifest(
        _session_dir(store, MODEL, "stale_old") / "manifest.json",
        (HEALTHY_STALE_DAYS + 5) * 86400,
    )
    plan = plan_prune(
        store,
        _AllPresentSSD(),
        MODEL,
        classes={PRUNE_CLASS_STALE},
        now=time.time(),
    )
    assert any(c.session_id == "stale_old" for c in plan.eligible)

    # Dry-run: no deletion.
    report = execute_plan(plan, store, confirm=False)
    assert report.dry_run is True
    assert report.deleted == []
    assert _session_dir(store, MODEL, "stale_old").exists()

    # Confirm: deletion happens.
    report2 = execute_plan(plan, store, confirm=True)
    assert report2.dry_run is False
    assert "stale_old" in report2.deleted
    assert not _session_dir(store, MODEL, "stale_old").exists()


# ---------------------------------------------------------------------------
# T10. Pin round-trip on workspace manifests.
# ---------------------------------------------------------------------------
def test_pinned_roundtrip_on_manifest(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "s", _hashes(1))
    assert store.is_pinned(MODEL, "s") is False
    store.set_pinned(MODEL, "s", True)
    assert store.is_pinned(MODEL, "s") is True
    store.set_pinned(MODEL, "s", False)
    assert store.is_pinned(MODEL, "s") is False


# ---------------------------------------------------------------------------
# T11. replay_check refreshes last_used_at on healthy, skips on opt-out.
# ---------------------------------------------------------------------------
def test_replay_check_refreshes_last_used_when_healthy(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    hashes = _hashes(2)
    store.commit(MODEL, "s", hashes)
    doc_before = store.load_raw(MODEL, "s")
    assert doc_before.get("last_used_at") in (None, 0)

    report = replay_check(store, MODEL, "s", _AllPresentSSD().has_block)
    assert report.grade == INTEGRITY_HEALTHY

    doc_after = store.load_raw(MODEL, "s")
    assert isinstance(doc_after.get("last_used_at"), float)
    ts1 = doc_after["last_used_at"]

    # Opt-out: do not refresh.
    # Force a later-clock touch so any change would be visible.
    time.sleep(0.01)
    report2 = replay_check(
        store, MODEL, "s", _AllPresentSSD().has_block, refresh_last_used=False
    )
    assert report2.grade == INTEGRITY_HEALTHY
    doc_after2 = store.load_raw(MODEL, "s")
    assert doc_after2["last_used_at"] == ts1


# ---------------------------------------------------------------------------
# T12. Unknown prune-class tokens are rejected (fail closed).
# ---------------------------------------------------------------------------
def test_unknown_prune_class_raises(tmp_path: Path) -> None:
    store = SessionArchiveStore(tmp_path / "a")
    store.commit(MODEL, "s", _hashes(1))
    with pytest.raises(ValueError):
        plan_prune(
            store, _AllPresentSSD(), MODEL, classes={"definitely-not-a-class"}
        )
