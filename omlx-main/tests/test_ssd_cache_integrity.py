# SPDX-License-Identifier: Apache-2.0
"""
SSD cache integrity tests.

Pin the contract that a single corrupt, missing, or unreadable persisted
block MUST NOT poison the whole paged SSD cache. Good blocks must remain
discoverable and reusable across restarts.

Scope: unit-level tests against ``PagedSSDCacheManager``. No scheduler,
no real model. MLX is required for the save/load path and tests are
skipped automatically when it is not available.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import List, Tuple

import pytest

from omlx.cache.paged_ssd_cache import PagedSSDCacheManager


def _has_mlx() -> bool:
    try:
        import mlx.core  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(not _has_mlx(), reason="MLX not available")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manager(cache_dir: Path) -> PagedSSDCacheManager:
    """Construct a manager with a permissive size limit and hot cache off."""
    return PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )


def _save_valid_block(
    manager: PagedSSDCacheManager,
    block_hash: bytes,
    *,
    num_layers: int = 2,
) -> Path:
    """Save a small valid block, flush to disk, return the on-disk path."""
    import mlx.core as mx

    cache_data = [
        (mx.zeros((1, 2, 8, 16)), mx.zeros((1, 2, 8, 16))) for _ in range(num_layers)
    ]
    assert manager.save_block(
        block_hash=block_hash,
        cache_data=cache_data,
        token_count=8,
        model_name="integrity-test",
        layer_cache_types=["KVCache"] * num_layers,
    ) is True
    # Force the background writer to drain so the file actually exists on disk.
    manager.close()
    meta = manager._index.get(block_hash)
    if meta is not None and meta.file_path.exists():
        return meta.file_path
    # Fall back to deterministic path lookup.
    hex_hash = block_hash.hex()
    return Path(manager._cache_dir) / hex_hash[0] / f"{hex_hash}.safetensors"


def _find_block_files(cache_dir: Path) -> List[Path]:
    return sorted(p for p in cache_dir.rglob("*.safetensors") if p.is_file())


def _corrupt_header_bytes(path: Path) -> None:
    """Smash the safetensors header length field so parsing fails."""
    with open(path, "r+b") as f:
        # 8-byte little-endian uint64 header length — overwrite with junk.
        f.seek(0)
        f.write(struct.pack("<Q", 2 ** 62))  # absurd header length


def _truncate_to_empty(path: Path) -> None:
    with open(path, "wb") as f:
        f.truncate(0)


def _mutate_stored_block_hash(path: Path, fake_hex: str) -> None:
    """Rewrite the safetensors header JSON so its stored block_hash differs
    from the filename. This simulates a checksum / identity mismatch: the
    content no longer matches its claimed identity.
    """
    with open(path, "rb") as f:
        raw = f.read()
    header_len = struct.unpack("<Q", raw[:8])[0]
    header_bytes = raw[8 : 8 + header_len]
    payload = raw[8 + header_len :]
    import json

    header = json.loads(header_bytes.decode("utf-8"))
    meta = header.get("__metadata__", {})
    assert "block_hash" in meta, "expected block_hash in safetensors metadata"
    meta["block_hash"] = fake_hex
    header["__metadata__"] = meta
    new_header = json.dumps(header, separators=(",", ":")).encode("utf-8")
    pad = (8 - len(new_header) % 8) % 8
    new_header += b" " * pad
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(new_header)))
        f.write(new_header)
        f.write(payload)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_startup_scan_skips_corrupt_block_and_keeps_good_blocks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache_dir = tmp_path / "ssd"
    good = b"good_block_hash____" + b"\x01" * 13
    bad = b"bad_block_hash_____" + b"\x02" * 13

    mgr = _make_manager(cache_dir)
    good_path = _save_valid_block(mgr, good)
    assert good_path.exists()

    mgr2 = _make_manager(cache_dir)
    bad_path = _save_valid_block(mgr2, bad)
    assert bad_path.exists()
    _corrupt_header_bytes(bad_path)

    # Fresh manager must not raise and must expose the good block.
    caplog.set_level("WARNING")
    fresh = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        assert fresh.has_block(good), "good block must survive a corrupt sibling"
        assert not fresh.has_block(bad), "corrupt block must not be indexed"
        loaded = fresh.load_block(good)
        assert loaded is not None and len(loaded) == 2
    finally:
        fresh.close()


def test_checksum_mismatch_prevents_block_reuse(tmp_path: Path) -> None:
    """A block whose stored identity no longer matches its on-disk bytes
    must not be served as a hit for the original hash. The cache layer
    may either remove it or mark it unusable; either way, a correct-hash
    query must not return the tampered block's data.
    """
    cache_dir = tmp_path / "ssd"
    block_hash = b"checksum_test______" + b"\x03" * 13

    mgr = _make_manager(cache_dir)
    path = _save_valid_block(mgr, block_hash)

    fake_hex = ("f" * (len(block_hash) * 2))[: len(block_hash) * 2]
    assert fake_hex != block_hash.hex()
    _mutate_stored_block_hash(path, fake_hex)

    fresh = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        # Correct-hash lookup must not be satisfied by the tampered file.
        result = fresh.load_block(block_hash)
        assert result is None, (
            "block whose stored hash no longer matches its claimed identity "
            "must not be returned for the original hash"
        )
        assert not fresh.has_block(block_hash), (
            "tampered block must not be indexed under its original hash"
        )
    finally:
        fresh.close()


def test_missing_block_file_is_non_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache_dir = tmp_path / "ssd"
    block_hash = b"missing_file_test__" + b"\x04" * 13

    mgr = _make_manager(cache_dir)
    path = _save_valid_block(mgr, block_hash)
    assert path.exists()

    # Reopen and force the index to contain the block, then delete the file.
    mgr2 = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        assert mgr2.has_block(block_hash)
        path.unlink()

        caplog.set_level("WARNING")
        result = mgr2.load_block(block_hash)
        assert result is None
        assert any(
            "SSD cache file missing" in rec.getMessage() for rec in caplog.records
        ), "missing file must produce an operator-visible warning, not a crash"
        assert not mgr2.has_block(block_hash)
    finally:
        mgr2.close()


def test_unreadable_block_is_quarantined_or_marked_unusable(tmp_path: Path) -> None:
    cache_dir = tmp_path / "ssd"
    block_hash = b"unreadable_test____" + b"\x05" * 13

    mgr = _make_manager(cache_dir)
    path = _save_valid_block(mgr, block_hash)
    _truncate_to_empty(path)

    fresh = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        assert not fresh.has_block(block_hash), (
            "an unreadable / empty block file must not be indexed as usable"
        )

        # Either quarantined (moved to a 'quarantine' sibling dir) or removed
        # from the active tree — both are acceptable contract outcomes.
        quarantine = cache_dir / "quarantine"
        still_in_tree = [
            p for p in _find_block_files(cache_dir)
            if "quarantine" not in p.parts
        ]
        was_quarantined = quarantine.exists() and any(quarantine.rglob("*"))
        was_removed = path not in still_in_tree
        assert was_quarantined or was_removed, (
            "bad block must be quarantined or removed from the active index"
        )
    finally:
        fresh.close()


def test_mixed_good_and_bad_blocks_do_not_poison_cache_startup(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "ssd"
    hashes: List[bytes] = []
    for i in range(5):
        h = (f"mixed_test_block_{i:02d}".encode("utf-8") + b"\x00" * 32)[:32]
        hashes.append(h)

    paths: List[Tuple[bytes, Path]] = []
    for h in hashes:
        mgr = _make_manager(cache_dir)
        paths.append((h, _save_valid_block(mgr, h)))

    # Corrupt two distinct files via different failure modes.
    _corrupt_header_bytes(paths[1][1])
    _truncate_to_empty(paths[3][1])

    fresh = PagedSSDCacheManager(
        cache_dir=cache_dir,
        max_size_bytes=1024 ** 3,
        hot_cache_max_bytes=0,
    )
    try:
        survivors = [h for h, _ in paths if fresh.has_block(h)]
        assert len(survivors) == 3, (
            f"expected 3 good blocks to survive, got {len(survivors)} "
            f"({[s.hex()[:12] for s in survivors]})"
        )
        for h in survivors:
            data = fresh.load_block(h)
            assert data is not None, "good block must remain reusable"
    finally:
        fresh.close()
