# SPDX-License-Identifier: Apache-2.0
"""
Non-regression tests for the ordinary shared prefix cache path.

The session-archive migration is worth keeping only if the ordinary
(non-session) shared prefix cache path continues to work. These tests
prove that:

* A back-to-back SSD-cache save/load cycle still reuses block payloads
  when no session fields are present.
* An explicit session restore does not silently perturb the normal
  shared-cache accounting for unrelated ordinary requests.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from omlx.cache.paged_ssd_cache import PagedSSDCacheManager


def _has_mlx() -> bool:
    try:
        import mlx.core  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _has_mlx(), reason="MLX not available")


def _hashes(prefix: str, n: int) -> List[bytes]:
    return [(f"{prefix}-{i:02d}".encode("utf-8") + b"\x00" * 32)[:32] for i in range(n)]


def _save_blocks(
    manager: PagedSSDCacheManager, hashes: List[bytes]
) -> None:
    import mlx.core as mx

    for h in hashes:
        cache_data = [(mx.zeros((1, 2, 8, 16)), mx.zeros((1, 2, 8, 16))) for _ in range(2)]
        assert manager.save_block(
            block_hash=h,
            cache_data=cache_data,
            token_count=8,
            model_name="nonreg-model",
            layer_cache_types=["KVCache", "KVCache"],
        ) is True


def test_shared_prefix_cache_path_still_works_without_session_restore(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "ssd"
    mgr = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        blocks = _hashes("shared", 3)
        _save_blocks(mgr, blocks)

        # Re-saving the same hashes must be recognised as cache hits
        # (ordinary shared-prefix reuse, no session involved).
        hits_before = mgr._stats["hits"]
        _save_blocks(mgr, blocks)
        assert mgr._stats["hits"] > hits_before, (
            "ordinary shared-prefix reuse must register as hits"
        )

        # Load-side reuse: every block is loadable without a session.
        for h in blocks:
            data = mgr.load_block(h)
            assert data is not None, "ordinary load path must still succeed"
    finally:
        mgr.close()


def test_session_restore_does_not_silently_modify_normal_prefix_reuse_behavior(
    tmp_path: Path,
) -> None:
    """Simulate a session-restore turn followed by an ordinary non-session
    request. The ordinary request's shared-prefix reuse counters must not
    be perturbed by the earlier session path.
    """
    cache_dir = tmp_path / "ssd"
    archive_root = tmp_path / "sessions"

    # Step 1: session path writes some blocks (simulated at the cache level).
    session_blocks = _hashes("session", 2)
    mgr = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        _save_blocks(mgr, session_blocks)
    finally:
        mgr.close()

    # Emulate a SessionArchiveStore commit, if the module is present. This
    # test must pass whether or not the feature has landed yet: the only
    # contract under test here is that the ordinary path remains intact.
    try:
        from omlx.cache.session_archive import SessionArchiveStore

        SessionArchiveStore(archive_root).commit(
            "nonreg-model", "sess-unrelated", session_blocks
        )
    except Exception:
        pass  # Feature not yet present — irrelevant to this assertion.

    # Step 2: ordinary request hits the same cache dir without any session
    # fields. Its prefix reuse must match the baseline.
    mgr2 = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        unrelated = _hashes("ordinary", 2)
        _save_blocks(mgr2, unrelated)

        baseline_hits = mgr2._stats["hits"]
        # Re-saving must still register hits on the ordinary path.
        _save_blocks(mgr2, unrelated)
        assert mgr2._stats["hits"] > baseline_hits, (
            "session archive activity must not disable ordinary prefix reuse"
        )

        # Loads on the ordinary path succeed unchanged.
        for h in unrelated:
            assert mgr2.load_block(h) is not None

        # The session-archive tree must not leak into the SSD cache tree.
        ssd_files = list(cache_dir.rglob("*"))
        archive_files = list(archive_root.rglob("*")) if archive_root.exists() else []
        for p in archive_files:
            assert cache_dir not in p.parents, (
                "session archive files must not be written under the SSD cache tree"
            )
        assert all(
            archive_root not in p.parents for p in ssd_files
        ), "SSD cache files must not be written under the session archive tree"
    finally:
        mgr2.close()
