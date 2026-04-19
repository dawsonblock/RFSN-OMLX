#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""
Internal operator CLI for the session archive.

Status: **experimental / internal**. This is a stdlib-only operator
tool — not a server API, not a UI. It reads the on-disk manifests
written by :class:`omlx.cache.session_archive.SessionArchiveStore` and
(on explicit request) validates them against the paged SSD cache or
prunes stale / invalid manifests.

It never mutates KV payload bytes. The SSD cache remains the sole
authority for block data; this tool only inspects and unlinks manifest
directories. There is no background service and no automatic deletion.

Subcommands:

  list     — list sessions for a model
  show     — show one manifest and block-presence summary
  validate — load + block-presence check; exit 1 if any session fails
  delete   — remove one session manifest directory
  prune    — identify (and optionally delete) invalid / expired / over-cap sessions
  stats    — print in-process counters from session_archive_metrics

Exit codes:
  0  OK
  1  validation failures were found (validate / prune without --dry-run)
  2  bad arguments or environment
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Optional

# Make the repo importable when invoked directly from a checkout.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from omlx.cache import session_archive_metrics as _metrics  # noqa: E402
from omlx.cache.session_archive import (  # noqa: E402
    SessionArchiveError,
    SessionArchiveStore,
)
from omlx.cache.session_archive_retention import (  # noqa: E402
    classify_session,
    iter_sessions,
    prune as retention_prune,
)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="session_archive_admin",
        description=(
            "Internal operator CLI for the session archive. "
            "Status: experimental / internal. Not a latency feature."
        ),
    )
    p.add_argument(
        "--archive-root",
        required=True,
        type=Path,
        help="Path to the SessionArchiveStore root directory.",
    )
    p.add_argument(
        "--ssd-cache-dir",
        type=Path,
        default=None,
        help=(
            "Optional path to the paged SSD cache directory. When set, "
            "validate/show/prune --invalid will check block presence on "
            "disk. When omitted, only manifest-level checks run."
        ),
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    lp = sub.add_parser("list", help="List sessions for a model")
    lp.add_argument("--model", required=True)

    sp = sub.add_parser("show", help="Show one manifest")
    sp.add_argument("--model", required=True)
    sp.add_argument("--session", required=True)

    vp = sub.add_parser(
        "validate",
        help="Validate manifests (load + optional SSD block-presence check)",
    )
    vp.add_argument("--model", required=True)
    vp.add_argument(
        "--session",
        default=None,
        help="Validate a single session; omit to validate every session.",
    )

    dp = sub.add_parser("delete", help="Delete one session manifest directory")
    dp.add_argument("--model", required=True)
    dp.add_argument("--session", required=True)
    dp.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt.",
    )

    pp = sub.add_parser("prune", help="Prune invalid / expired / over-cap sessions")
    pp.add_argument("--model", required=True)
    pp.add_argument(
        "--invalid",
        action="store_true",
        help="Select manifests that fail load or reference missing blocks.",
    )
    pp.add_argument(
        "--older-than",
        default=None,
        help=(
            "Select manifests with mtime older than this duration. Accepts "
            "'<N><s|m|h|d>' (e.g. '7d', '30m', '3600s')."
        ),
    )
    pp.add_argument(
        "--max-per-model",
        type=int,
        default=None,
        help="Keep only the newest N manifests for this model.",
    )
    pp.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report candidates without deleting (default).",
    )
    pp.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually delete the selected manifests.",
    )

    sub.add_parser("stats", help="Print in-process session archive counters")

    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_DURATION_RE = re.compile(r"^(\d+)([smhd])$")


def _parse_duration(text: str) -> timedelta:
    m = _DURATION_RE.match(text.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid duration {text!r}; use <N>{{s|m|h|d}} (e.g. '7d')"
        )
    n = int(m.group(1))
    unit = m.group(2)
    seconds = n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return timedelta(seconds=seconds)


def _open_ssd_cache(ssd_dir: Optional[Path]) -> Any:
    """Return a read-only-ish SSD cache handle, or None."""
    if ssd_dir is None:
        return None
    if not ssd_dir.exists():
        print(
            f"warning: --ssd-cache-dir {ssd_dir} does not exist; "
            "skipping block-presence checks",
            file=sys.stderr,
        )
        return None
    try:
        from omlx.cache.paged_ssd_cache import PagedSSDCacheManager
    except Exception as exc:  # pragma: no cover — import guard
        print(
            f"warning: cannot import PagedSSDCacheManager ({exc}); "
            "skipping block-presence checks",
            file=sys.stderr,
        )
        return None
    try:
        return PagedSSDCacheManager(cache_dir=ssd_dir)
    except TypeError:
        # Older signatures.
        try:
            return PagedSSDCacheManager(str(ssd_dir))
        except Exception as exc:
            print(
                f"warning: cannot open PagedSSDCacheManager({ssd_dir}): {exc}",
                file=sys.stderr,
            )
            return None
    except Exception as exc:
        print(
            f"warning: cannot open PagedSSDCacheManager({ssd_dir}): {exc}",
            file=sys.stderr,
        )
        return None


def _fmt_mtime(mtime: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(mtime))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def _cmd_list(store: SessionArchiveStore, args: argparse.Namespace) -> int:
    rows = list(iter_sessions(store, args.model))
    if not rows:
        print(f"(no sessions for model {args.model!r})")
        return 0
    print(f"{'SESSION_ID':<40}  {'BLOCKS':>6}  {'SIZE':>6}  {'MTIME':<19}  PATH")
    for d in rows:
        blocks = "?" if d.block_count is None else str(d.block_count)
        print(
            f"{d.session_id:<40}  {blocks:>6}  {d.size_bytes:>6}  "
            f"{_fmt_mtime(d.mtime):<19}  {d.manifest_path}"
        )
    return 0


def _cmd_show(
    store: SessionArchiveStore, ssd_cache: Any, args: argparse.Namespace
) -> int:
    manifest = store.manifest_path(args.model, args.session)
    if not manifest.exists():
        print(
            f"unknown session: no manifest at {manifest}", file=sys.stderr
        )
        return 1
    try:
        doc = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"malformed manifest: {manifest}: {exc}", file=sys.stderr)
        return 1

    print(f"manifest: {manifest}")
    print(f"  version:     {doc.get('version')!r}")
    print(f"  model_name:  {doc.get('model_name')!r}")
    print(f"  session_id:  {doc.get('session_id')!r}")
    hashes = doc.get("block_hashes") or []
    print(f"  block_count: {len(hashes)}")
    status, detail = classify_session(
        store, ssd_cache, args.model, args.session
    )
    print(f"  status:      {status}")
    print(f"  detail:      {detail}")
    return 0 if status == "ok" else 1


def _cmd_validate(
    store: SessionArchiveStore, ssd_cache: Any, args: argparse.Namespace
) -> int:
    if args.session is not None:
        status, detail = classify_session(
            store, ssd_cache, args.model, args.session
        )
        print(f"{args.session}\t{status}\t{detail}")
        return 0 if status == "ok" else 1

    failures = 0
    any_printed = False
    for d in iter_sessions(store, args.model):
        any_printed = True
        status, detail = classify_session(
            store, ssd_cache, args.model, d.session_id
        )
        print(f"{d.session_id}\t{status}\t{detail}")
        if status != "ok":
            failures += 1
    if not any_printed:
        print(f"(no sessions for model {args.model!r})")
    return 0 if failures == 0 else 1


def _cmd_delete(
    store: SessionArchiveStore, args: argparse.Namespace
) -> int:
    manifest = store.manifest_path(args.model, args.session)
    session_dir = manifest.parent
    if not session_dir.exists():
        print(
            f"unknown session: nothing to delete at {session_dir}",
            file=sys.stderr,
        )
        return 1
    if not args.yes:
        reply = input(f"delete {session_dir}? [y/N] ").strip().lower()
        if reply not in ("y", "yes"):
            print("aborted")
            return 0
    import shutil

    shutil.rmtree(session_dir)
    print(f"deleted {session_dir}")
    return 0


def _cmd_prune(
    store: SessionArchiveStore,
    ssd_cache: Any,
    args: argparse.Namespace,
) -> int:
    older_than: Optional[timedelta] = None
    if args.older_than:
        older_than = _parse_duration(args.older_than)
    report = retention_prune(
        store,
        ssd_cache,
        args.model,
        invalid=args.invalid,
        older_than=older_than,
        max_per_model=args.max_per_model,
        dry_run=args.dry_run,
    )
    print(
        f"model={report.model_name} dry_run={report.dry_run} "
        f"considered={report.considered}"
    )
    if report.invalid:
        print("  invalid:")
        for sid, reason in report.invalid:
            print(f"    {sid}\t{reason}")
    if report.expired:
        print("  expired:")
        for sid in report.expired:
            print(f"    {sid}")
    if report.over_cap:
        print("  over_cap:")
        for sid in report.over_cap:
            print(f"    {sid}")
    if not report.dry_run:
        print(f"  deleted={len(report.deleted)}")
        for sid in report.deleted:
            print(f"    {sid}")
        if report.errors:
            print("  errors:")
            for sid, err in report.errors:
                print(f"    {sid}\t{err}")
    # Exit 1 when we found anything at all — lets CI / cron wrappers detect
    # that the archive needed attention even in dry-run mode.
    if report.invalid or report.expired or report.over_cap or report.errors:
        return 1
    return 0


def _cmd_stats(_args: argparse.Namespace) -> int:
    snap = _metrics.snapshot()
    if not snap:
        print("(no counters; process has not restored/committed any sessions)")
        return 0
    for key in sorted(snap):
        print(f"{key}\t{snap[key]}")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.archive_root.exists():
        print(
            f"error: --archive-root {args.archive_root} does not exist",
            file=sys.stderr,
        )
        return 2

    store = SessionArchiveStore(args.archive_root)
    ssd_cache = _open_ssd_cache(args.ssd_cache_dir)

    if args.cmd == "list":
        return _cmd_list(store, args)
    if args.cmd == "show":
        return _cmd_show(store, ssd_cache, args)
    if args.cmd == "validate":
        return _cmd_validate(store, ssd_cache, args)
    if args.cmd == "delete":
        return _cmd_delete(store, args)
    if args.cmd == "prune":
        return _cmd_prune(store, ssd_cache, args)
    if args.cmd == "stats":
        return _cmd_stats(args)
    parser.error(f"unknown command {args.cmd!r}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
