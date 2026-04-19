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
    ancestry_chain,
    classify_integrity,
    diff_sessions,
    replay_check,
)
from omlx.cache.session_archive_retention import (  # noqa: E402
    classify_session,
    integrity_grade,
    iter_sessions,
    prune as retention_prune,
)
from omlx.cache.session_archive_portable import (  # noqa: E402
    BundleError,
    export_session,
    import_session,
    inspect_bundle,
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
    lp.add_argument("--model", "--model-name", dest="model", required=True)

    sp = sub.add_parser("show", help="Show one manifest")
    sp.add_argument("--model", "--model-name", dest="model", required=True)
    sp.add_argument("--session", "--session-id", dest="session", required=True)

    vp = sub.add_parser(
        "validate",
        help="Validate manifests (load + optional SSD block-presence check)",
    )
    vp.add_argument("--model", "--model-name", dest="model", required=True)
    vp.add_argument(
        "--session",
        "--session-id",
        dest="session",
        default=None,
        help="Validate a single session; omit to validate every session.",
    )
    vp.add_argument(
        "--stale-after",
        default=None,
        type=_parse_duration,
        help="Optionally grade healthy workspaces as stale after this age.",
    )
    vp.add_argument(
        "--expected-model-name",
        default=None,
        help="Optionally grade a workspace incompatible if its model_name differs.",
    )

    dp = sub.add_parser("delete", help="Delete one session manifest directory")
    dp.add_argument("--model", "--model-name", dest="model", required=True)
    dp.add_argument("--session", "--session-id", dest="session", required=True)
    dp.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt.",
    )

    pp = sub.add_parser("prune", help="Prune invalid / expired / over-cap sessions")
    pp.add_argument("--model", "--model-name", dest="model", required=True)
    pp.add_argument(
        "--invalid",
        action="store_true",
        help="Select manifests that fail load or reference missing blocks.",
    )
    pp.add_argument(
        "--older-than",
        default=None,
        type=_parse_duration,
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

    # ------- Workspace-lineage verbs -------
    cp = sub.add_parser(
        "create",
        help="Create an empty workspace (manifest with no turns yet)",
    )
    cp.add_argument("--model-name", required=True)
    cp.add_argument("--session-id", required=True)
    cp.add_argument("--label", default=None)
    cp.add_argument("--description", default=None)
    cp.add_argument("--task-tag", default=None)
    cp.add_argument("--block-size", type=int, default=None)

    stp = sub.add_parser(
        "status",
        help="Compact status block: head? healthy? parent? can-export?",
    )
    stp.add_argument("--model-name", required=True)
    stp.add_argument("--session-id", required=True)
    stp.add_argument(
        "--stale-after",
        default=None,
        type=_parse_duration,
        help="Optionally grade healthy workspaces as stale after this age.",
    )
    stp.add_argument(
        "--expected-model-name",
        default=None,
        help="Optionally grade the workspace incompatible if model_name differs.",
    )

    rsp = sub.add_parser(
        "resume",
        help="Alias of status plus next-step hints",
    )
    rsp.add_argument("--model-name", required=True)
    rsp.add_argument("--session-id", required=True)
    rsp.add_argument(
        "--stale-after",
        default=None,
        type=_parse_duration,
        help="Optionally grade healthy workspaces as stale after this age.",
    )
    rsp.add_argument(
        "--expected-model-name",
        default=None,
        help="Optionally grade the workspace incompatible if model_name differs.",
    )

    # ------- Phase 7: lineage / recovery subcommands -------
    tp = sub.add_parser("turns", help="List turns for one session")
    tp.add_argument("--model-name", "--model", dest="model_name", required=True)
    tp.add_argument("--session-id", "--session", dest="session_id", required=True)

    hp = sub.add_parser("head", help="Show head turn id and block count")
    hp.add_argument("--model-name", "--model", dest="model_name", required=True)
    hp.add_argument("--session-id", "--session", dest="session_id", required=True)

    lp2 = sub.add_parser("lineage", help="Show lineage (label, parent, timestamps)")
    lp2.add_argument("--model-name", "--model", dest="model_name", required=True)
    lp2.add_argument("--session-id", "--session", dest="session_id", required=True)

    fp = sub.add_parser("fork", help="Fork a session at a given turn (metadata-only)")
    fp.add_argument("--model-name", "--model", dest="model_name", required=True)
    fp.add_argument("--src-session-id", required=True)
    fp.add_argument("--dst-session-id", required=True)
    fp.add_argument("--at-turn", default=None, help="Turn id to fork from (default: head)")
    fp.add_argument("--label", default=None)
    fp.add_argument("--description", default=None)
    fp.add_argument("--branch-reason", default=None)
    fp.add_argument("--task-tag", default=None)
    fp.add_argument("--overwrite", action="store_true")

    dfp = sub.add_parser("diff", help="Diff two sessions (metadata only)")
    dfp.add_argument("--model-a", required=True)
    dfp.add_argument("--session-a", required=True)
    dfp.add_argument("--model-b", required=True)
    dfp.add_argument("--session-b", required=True)

    rcp = sub.add_parser(
        "replay-check",
        help="Validate every block referenced by the session's head is still on SSD",
    )
    rcp.add_argument("--model-name", "--model", dest="model_name", required=True)
    rcp.add_argument("--session-id", "--session", dest="session_id", required=True)
    rcp.add_argument("--turn", default=None, help="Turn id (default: head)")
    rcp.add_argument(
        "--expected-model-name",
        default=None,
        help=(
            "If set and the manifest's model_name does not match, grade "
            "incompatible_model without touching the SSD cache."
        ),
    )

    ep = sub.add_parser(
        "export-session",
        help="Export a session (manifest + SSD blocks) to a tarball bundle",
    )
    ep.add_argument("--model-name", "--model", dest="model_name", required=True)
    ep.add_argument("--session-id", "--session", dest="session_id", required=True)
    ep.add_argument("--out", required=True, type=Path)
    ep.add_argument(
        "--allow-missing-blocks",
        action="store_true",
        help="Produce a partial bundle instead of failing on missing blocks.",
    )

    ib = sub.add_parser(
        "inspect-bundle",
        help="Show bundle provenance + compatibility without mutating anything",
    )
    ib.add_argument("--bundle", required=True, type=Path)

    ip = sub.add_parser(
        "import-session",
        help="Import a session bundle; verifies sha256 before writing anything",
    )
    ip.add_argument("--bundle", required=True, type=Path)
    ip.add_argument(
        "--expected-model-name",
        default=None,
        help="Reject bundle if its model_name does not match this.",
    )
    ip.add_argument(
        "--expected-block-size",
        type=int,
        default=None,
        help=(
            "Reject bundle if its model_compat.block_size does not match "
            "this. Guards the compatibility-family invariant."
        ),
    )
    ip.add_argument(
        "--fail-if-exists",
        action="store_true",
        help="Explicitly request the default conservative policy: fail on conflict.",
    )
    ip.add_argument(
        "--rename-on-conflict",
        action="store_true",
        help="Deterministically rename imported session_id to '<id>-imported-N'.",
    )
    ip.add_argument(
        "--overwrite-session", "--overwrite",
        dest="overwrite_session",
        action="store_true",
        help="Overwrite an existing destination session only with explicit intent.",
    )
    ip.add_argument(
        "--re-root-lineage",
        action="store_true",
        help="Clear the imported parent pointer instead of preserving external ancestry.",
    )

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


class _ReadOnlySSDProbe:
    """Minimal read-only SSD probe for operator validation commands."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = Path(cache_dir)

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


def _open_ssd_cache(ssd_dir: Optional[Path]) -> Any:
    """Return a strictly read-only SSD presence probe, or None."""
    if ssd_dir is None:
        return None
    if not ssd_dir.exists():
        print(
            f"warning: --ssd-cache-dir {ssd_dir} does not exist; "
            "skipping block-presence checks",
            file=sys.stderr,
        )
        return None
    # Operator status/validate/resume only need block presence by hash.
    # Do not instantiate the live PagedSSDCacheManager here: that can
    # scan, quarantine, or otherwise mutate unrelated cache state.
    return _ReadOnlySSDProbe(ssd_dir)


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
    print(
        f"{'SESSION_ID':<24}  {'BLOCKS':>6}  {'MTIME':<19}  {'TAG':<18}  {'LABEL':<20}  PATH"
    )
    for d in rows:
        blocks = "?" if d.block_count is None else str(d.block_count)
        label = ""
        task_tag = ""
        try:
            lin = store.lineage(args.model, d.session_id)
            label = (lin.label or "")[:20]
            task_tag = (lin.task_tag or "")[:18]
        except SessionArchiveError:
            pass
        print(
            f"{d.session_id:<24}  {blocks:>6}  {_fmt_mtime(d.mtime):<19}  "
            f"{task_tag:<18}  {label:<20}  {d.manifest_path}"
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
    print(f"  label:       {doc.get('label')!r}")
    print(f"  description: {doc.get('description')!r}")
    print(f"  task_tag:    {doc.get('task_tag')!r}")
    print(f"  head_turn:   {doc.get('head_turn_id')!r}")
    print(f"  parent:      {doc.get('parent')!r}")
    turns = doc.get("turns") or []
    print(f"  turn_count:  {len(turns)}")
    head_id = doc.get("head_turn_id")
    head = next((t for t in turns if isinstance(t, dict) and t.get("turn_id") == head_id), None)
    if head is not None:
        head_hashes = head.get("block_hashes") or []
        print(f"  head_blocks: {len(head_hashes)}")
        print(f"  head_note:   {head.get('note')!r}")
        print(f"  branch_why:  {head.get('branch_reason')!r}")
    status, detail = classify_session(
        store, ssd_cache, args.model, args.session
    )
    print(f"  status:      {status}")
    print(f"  detail:      {detail}")
    print(f"  grade:       {integrity_grade(status)}")
    return 0 if status == "ok" else 1


def _cmd_validate(
    store: SessionArchiveStore, ssd_cache: Any, args: argparse.Namespace
) -> int:
    stale_after_seconds = (
        args.stale_after.total_seconds() if args.stale_after is not None else None
    )
    if args.session is not None:
        status, detail = classify_session(
            store, ssd_cache, args.model, args.session
        )
        grade = classify_integrity(
            store,
            args.model,
            args.session,
            expected_model_name=args.expected_model_name,
            stale_after_seconds=stale_after_seconds,
        ) if status == "ok" else integrity_grade(status)
        print(f"{args.session}\t{status}\t{detail}")
        print(f"grade\t{grade}")
        return 0 if grade in ("healthy", "stale") else 1

    failures = 0
    any_printed = False
    for d in iter_sessions(store, args.model):
        any_printed = True
        status, detail = classify_session(
            store, ssd_cache, args.model, d.session_id
        )
        grade = classify_integrity(
            store,
            args.model,
            d.session_id,
            expected_model_name=args.expected_model_name,
            stale_after_seconds=stale_after_seconds,
        ) if status == "ok" else integrity_grade(status)
        print(f"{d.session_id}\t{status}\t{detail}\tgrade={grade}")
        if grade not in ("healthy", "stale"):
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
    older_than: Optional[timedelta] = args.older_than
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


def _cmd_turns(store: SessionArchiveStore, args: argparse.Namespace) -> int:
    try:
        turns = store.list_turns(args.model_name, args.session_id)
    except SessionArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not turns:
        print("(no turns)")
        return 0
    print(f"turn_id\tcommitted_at\tblocks\tnote\tbranch_reason")
    for t in turns:
        note = t.note if t.note is not None else ""
        branch_reason = t.branch_reason if t.branch_reason is not None else ""
        print(
            f"{t.turn_id}\t{t.committed_at:.3f}\t{t.block_count}\t"
            f"{note}\t{branch_reason}"
        )
    return 0


def _cmd_head(store: SessionArchiveStore, args: argparse.Namespace) -> int:
    try:
        hid, hashes = store.load_head(args.model_name, args.session_id)
    except SessionArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"head_turn_id\t{hid}")
    print(f"block_count\t{len(hashes)}")
    return 0


def _cmd_lineage(store: SessionArchiveStore, args: argparse.Namespace) -> int:
    try:
        lin = store.lineage(args.model_name, args.session_id)
    except SessionArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parent = (
        f"{lin.parent[0]}@{lin.parent[1]}" if lin.parent is not None else "(root)"
    )
    print(f"session_id\t{lin.session_id}")
    print(f"label\t{lin.label if lin.label is not None else ''}")
    print(f"description\t{lin.description if lin.description is not None else ''}")
    print(f"task_tag\t{lin.task_tag if lin.task_tag is not None else ''}")
    print(f"head_turn_id\t{lin.head_turn_id}")
    print(f"parent\t{parent}")
    print(f"turn_count\t{lin.turn_count}")
    print(f"created_at\t{lin.created_at:.3f}")
    print(f"updated_at\t{lin.updated_at:.3f}")
    print(
        f"model_compat\t{lin.model_compat.model_name} "
        f"block_size={lin.model_compat.block_size} "
        f"schema={lin.model_compat.schema}"
    )
    return 0


def _cmd_fork(store: SessionArchiveStore, args: argparse.Namespace) -> int:
    try:
        src_turn = store.fork(
            args.model_name,
            args.src_session_id,
            args.dst_session_id,
            at_turn=args.at_turn,
            label=args.label,
            description=args.description,
            branch_reason=args.branch_reason,
            task_tag=args.task_tag,
            overwrite=args.overwrite,
        )
    except SessionArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(
        f"forked {args.src_session_id!r}@{src_turn} -> "
        f"{args.dst_session_id!r}"
    )
    return 0


def _cmd_diff(store: SessionArchiveStore, args: argparse.Namespace) -> int:
    try:
        d = diff_sessions(
            store,
            args.model_a, args.session_a,
            args.model_b, args.session_b,
        )
    except SessionArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"session_a\t{d.session_a[0]}/{d.session_a[1]}\tturns={d.turn_count_a}")
    print(f"session_b\t{d.session_b[0]}/{d.session_b[1]}\tturns={d.turn_count_b}")
    if d.common_ancestor is not None:
        print(f"common_ancestor\t{d.common_ancestor[0]}@{d.common_ancestor[1]}")
    else:
        print("common_ancestor\t(none)")
    print(f"shared_turn_count\t{d.shared_turn_count}")
    print("index\ta_turn\tb_turn\ta_blocks\tb_blocks\tcommon_prefix\tdiverged")
    for i, t in enumerate(d.per_turn):
        print(
            f"{i}\t{t.turn_id_a or ''}\t{t.turn_id_b or ''}\t"
            f"{t.block_count_a}\t{t.block_count_b}\t"
            f"{t.common_prefix_blocks}\t{t.diverged}"
        )
    return 0 if d.shared_turn_count > 0 or d.common_ancestor is not None else 0


def _cmd_replay_check(
    store: SessionArchiveStore, ssd_cache: Any, args: argparse.Namespace
) -> int:
    # --expected-model-name short-circuits on incompatible_model without
    # touching the SSD cache, so we only require --ssd-cache-dir for the
    # actual block-presence probe.
    has_block = None
    if ssd_cache is not None and hasattr(ssd_cache, "has_block"):
        has_block = ssd_cache.has_block
    elif not args.expected_model_name:
        print(
            "error: replay-check needs --ssd-cache-dir and a readable paged SSD cache",
            file=sys.stderr,
        )
        return 2

    def _missing_block(_h: bytes) -> bool:
        return False

    rep = replay_check(
        store,
        args.model_name,
        args.session_id,
        has_block if has_block is not None else _missing_block,
        turn_id=args.turn,
        expected_model_name=args.expected_model_name,
    )
    print(f"session_id\t{rep.session_id}")
    print(f"head_turn_id\t{rep.head_turn_id}")
    print(f"total_blocks\t{rep.total_blocks}")
    print(f"present_blocks\t{rep.present_blocks}")
    print(f"missing_blocks\t{len(rep.missing_blocks)}")
    print(f"replayable\t{rep.replayable}")
    print(f"grade\t{rep.grade}")
    if rep.missing_blocks:
        for h in rep.missing_blocks[:5]:
            print(f"missing\t{h}")
    return 0 if rep.replayable else 1


def _cmd_export(
    store: SessionArchiveStore, ssd_cache: Any, args: argparse.Namespace
) -> int:
    if not args.ssd_cache_dir:
        print(
            "error: export-session needs --ssd-cache-dir",
            file=sys.stderr,
        )
        return 2
    try:
        res = export_session(
            store,
            args.model_name,
            args.session_id,
            args.ssd_cache_dir,
            args.out,
            allow_missing_blocks=args.allow_missing_blocks,
        )
        info = inspect_bundle(args.out)
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    env = info["envelope"]
    print(f"path\t{res.path}")
    print(f"block_count\t{res.block_count}")
    print(f"missing_block_count\t{res.missing_block_count}")
    print(f"grade\t{res.grade}")
    print(f"source_label\t{env.get('source_label') or ''}")
    print(f"task_tag\t{env.get('task_tag') or ''}")
    print(f"git_commit\t{env.get('git_commit') or ''}")
    return 0


def _cmd_inspect_bundle(args: argparse.Namespace) -> int:
    try:
        info = inspect_bundle(args.bundle)
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    env = info["envelope"]
    print(f"bundle_version\t{env.get('bundle_version')}")
    print(f"model_name\t{env.get('model_name')}")
    print(f"session_id\t{env.get('session_id')}")
    print(f"head_turn_id\t{env.get('head_turn_id')}")
    print(f"source_label\t{env.get('source_label') or ''}")
    print(f"source_description\t{env.get('source_description') or ''}")
    print(f"task_tag\t{env.get('task_tag') or ''}")
    compat = env.get('model_compat') or {}
    print(
        f"model_compat\t{compat.get('model_name')} "
        f"block_size={compat.get('block_size')} schema={compat.get('schema')}"
    )
    plat = env.get('platform') or {}
    print(
        f"platform\t{plat.get('system')}/{plat.get('machine')} "
        f"python={plat.get('python')}"
    )
    print(f"git_commit\t{env.get('git_commit') or ''}")
    print(f"exporter_version\t{env.get('exporter_version') or ''}")
    print(f"block_count\t{env.get('block_count')}")
    return 0


def _cmd_import(
    store: SessionArchiveStore, args: argparse.Namespace
) -> int:
    if not args.ssd_cache_dir:
        print(
            "error: import-session needs --ssd-cache-dir",
            file=sys.stderr,
        )
        return 2
    if args.rename_on_conflict and args.overwrite_session:
        print(
            "error: choose exactly one conflict policy: --rename-on-conflict or --overwrite",
            file=sys.stderr,
        )
        return 2
    if args.fail_if_exists and (args.rename_on_conflict or args.overwrite_session):
        print(
            "error: --fail-if-exists cannot be combined with --rename-on-conflict or --overwrite",
            file=sys.stderr,
        )
        return 2
    try:
        res = import_session(
            store,
            args.bundle,
            args.ssd_cache_dir,
            expected_model_name=args.expected_model_name,
            expected_block_size=args.expected_block_size,
            overwrite_session=args.overwrite_session,
            rename_on_conflict=args.rename_on_conflict,
            re_root_lineage=args.re_root_lineage,
        )
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"model_name\t{res.model_name}")
    print(f"session_id\t{res.session_id}")
    print(f"source_session_id\t{res.source_session_id}")
    print(f"manifest_path\t{res.manifest_path}")
    print(f"blocks_written\t{res.blocks_written}")
    print(f"blocks_skipped\t{res.blocks_skipped}")
    print(f"conflict_policy\t{res.conflict_policy}")
    print(f"re_rooted\t{res.re_rooted}")
    print(f"source_label\t{res.provenance.get('source_label') or ''}")
    print(f"task_tag\t{res.provenance.get('task_tag') or ''}")
    print(f"git_commit\t{res.provenance.get('git_commit') or ''}")
    return 0


# ---------------------------------------------------------------------------
# Workspace-lineage verbs: create / status / resume
# ---------------------------------------------------------------------------
def _cmd_create(
    store: SessionArchiveStore, args: argparse.Namespace
) -> int:
    try:
        store.init_workspace(
            args.model_name,
            args.session_id,
            label=args.label,
            description=args.description,
            block_size=args.block_size,
            task_tag=args.task_tag,
        )
    except SessionArchiveError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    manifest = store.manifest_path(args.model_name, args.session_id)
    print(f"created\t{args.model_name}/{args.session_id}")
    print(f"manifest\t{manifest}")
    print("turns\t0")
    return 0


def _status_lines(
    store: SessionArchiveStore,
    ssd_cache: Any,
    model_name: str,
    session_id: str,
    *,
    stale_after_seconds: Optional[float] = None,
    expected_model_name: Optional[str] = None,
) -> tuple[int, list[str]]:
    """Build the shared status block. Returns (rc, lines)."""
    lines: list[str] = []
    try:
        lin = store.lineage(model_name, session_id)
    except SessionArchiveError as exc:
        lines.append(f"error\t{exc}")
        lines.append(f"grade\t{classify_integrity(store, model_name, session_id)}")
        return 1, lines

    turns = store.list_turns(model_name, session_id)
    has_head = bool(lin.head_turn_id) and any(
        t.turn_id == lin.head_turn_id for t in turns
    )
    parent = (
        f"{lin.parent[0]}@{lin.parent[1]}" if lin.parent is not None else "(root)"
    )
    if lin.parent is None:
        parent_status = "root"
    else:
        parent_manifest = store.manifest_path(model_name, lin.parent[0])
        parent_status = "present" if parent_manifest.exists() else "dangling"

    # Grade
    grade = classify_integrity(
        store,
        model_name,
        session_id,
        expected_model_name=expected_model_name,
        stale_after_seconds=stale_after_seconds,
    )
    last_updated = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(lin.updated_at)
    ) if lin.updated_at else ""

    # Replay probe if ssd cache available and we have a head.
    replayable: Optional[bool] = None
    missing_count: Optional[int] = None
    if has_head and ssd_cache is not None and hasattr(ssd_cache, "has_block"):
        try:
            rep = replay_check(
                store, model_name, session_id, ssd_cache.has_block,
            )
            replayable = rep.replayable
            missing_count = len(rep.missing_blocks)
            # Replay check can sharpen the grade (missing_blocks).
            if rep.grade and rep.grade != grade:
                grade = rep.grade
        except Exception as exc:  # noqa: BLE001 — operator CLI
            lines.append(f"replay_error\t{exc}")

    lines.append(f"session_id\t{lin.session_id}")
    lines.append(f"label\t{lin.label if lin.label is not None else ''}")
    lines.append(f"description\t{lin.description if lin.description is not None else ''}")
    lines.append(f"task_tag\t{lin.task_tag if lin.task_tag is not None else ''}")
    lines.append(f"head_turn_id\t{lin.head_turn_id}")
    lines.append(f"turn_count\t{lin.turn_count}")
    lines.append(f"parent\t{parent}")
    lines.append(f"parent_status\t{parent_status}")
    if lin.parent is not None:
        lines.append(f"branch_origin\t{lin.parent[0]}@{lin.parent[1]}")
    lines.append(f"last_updated\t{last_updated}")
    lines.append(f"has_head\t{has_head}")
    lines.append(
        f"model_compat\t{lin.model_compat.model_name} "
        f"block_size={lin.model_compat.block_size} "
        f"schema={lin.model_compat.schema}"
    )
    if lin.parent is not None:
        try:
            chain = ancestry_chain(store, model_name, session_id)
            ancestry = " -> ".join(f"{sid}@{tid}" for sid, tid in chain)
        except SessionArchiveError as exc:
            ancestry = f"error: {exc}"
        lines.append(f"ancestry\t{ancestry}")
    if replayable is not None:
        lines.append(f"replayable\t{replayable}")
        lines.append(f"missing_blocks\t{missing_count}")
    else:
        lines.append("replayable\tnot_checked")
        lines.append("missing_blocks\tnot_checked")
    # can-export ≈ has_head and (no ssd probe done OR replayable true)
    can_export = has_head and (replayable is None or replayable)
    lines.append(f"can_export\t{can_export}")
    lines.append(f"referenced_blocks_resolvable\t{replayable if replayable is not None else 'not_checked'}")
    lines.append(f"grade\t{grade}")
    return 0, lines


def _cmd_status(
    store: SessionArchiveStore, ssd_cache: Any, args: argparse.Namespace
) -> int:
    rc, lines = _status_lines(
        store,
        ssd_cache,
        args.model_name,
        args.session_id,
        stale_after_seconds=(
            args.stale_after.total_seconds() if args.stale_after is not None else None
        ),
        expected_model_name=args.expected_model_name,
    )
    for line in lines:
        print(line)
    return rc


def _cmd_resume(
    store: SessionArchiveStore, ssd_cache: Any, args: argparse.Namespace
) -> int:
    rc, lines = _status_lines(
        store,
        ssd_cache,
        args.model_name,
        args.session_id,
        stale_after_seconds=(
            args.stale_after.total_seconds() if args.stale_after is not None else None
        ),
        expected_model_name=args.expected_model_name,
    )
    for line in lines:
        print(line)
    # Derive grade from the last printed grade line.
    grade = ""
    has_head = False
    for line in lines:
        if line.startswith("grade\t"):
            grade = line.split("\t", 1)[1]
        elif line.startswith("has_head\t"):
            has_head = line.split("\t", 1)[1] == "True"
    print("next_steps:")
    if rc != 0:
        print("  - fix the error above (check manifest path / JSON)")
        return rc
    if not has_head:
        print("  - commit your first turn to populate head_turn_id")
        print("  - then re-run: session_archive_admin resume ...")
        return 1
    if grade == "healthy":
        print("  - fork: session_archive_admin fork --model-name ... --src-session-id ... --dst-session-id ...")
        print("  - diff: session_archive_admin diff --model-a ... --session-a ... --model-b ... --session-b ...")
        print("  - export-session: session_archive_admin export-session --model-name ... --session-id ... --out ...")
    elif grade == "missing_blocks":
        print("  - blocks have been evicted; export-session with --allow-missing-blocks for a partial bundle")
        print("  - or prune and restart the task")
        return 1
    elif grade == "stale":
        print("  - workspace is stale; validate, then decide to prune or keep")
        return 0
    elif grade == "incompatible_model":
        print("  - workspace was recorded for a different model; it cannot be replayed here")
        return 1
    elif grade in ("invalid_manifest", "unreadable"):
        print("  - manifest is unreadable; consider delete")
        return 1
    return rc


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
    if args.cmd == "create":
        return _cmd_create(store, args)
    if args.cmd == "status":
        return _cmd_status(store, ssd_cache, args)
    if args.cmd == "resume":
        return _cmd_resume(store, ssd_cache, args)
    if args.cmd == "turns":
        return _cmd_turns(store, args)
    if args.cmd == "head":
        return _cmd_head(store, args)
    if args.cmd == "lineage":
        return _cmd_lineage(store, args)
    if args.cmd == "fork":
        return _cmd_fork(store, args)
    if args.cmd == "diff":
        return _cmd_diff(store, args)
    if args.cmd == "replay-check":
        return _cmd_replay_check(store, ssd_cache, args)
    if args.cmd == "export-session":
        return _cmd_export(store, ssd_cache, args)
    if args.cmd == "inspect-bundle":
        return _cmd_inspect_bundle(args)
    if args.cmd == "import-session":
        return _cmd_import(store, args)
    parser.error(f"unknown command {args.cmd!r}")
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
