# SPDX-License-Identifier: Apache-2.0
"""Benchmark worker for the executor-boundary ownership pass.

This worker compares two paths on identical scheduler/model/workload input:

- ``stock`` — the legacy direct handoff to BatchGenerator
- ``owned`` — the scheduler-owned executor seam

The goal is not to market speed. The goal is to prove whether the branch now
owns a real slice of the request-to-execution flow and what cost that adds.
"""

from __future__ import annotations

import gc
import json
import os
import sys
import time
import uuid
from pathlib import Path


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, 'to_dict') and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if hasattr(value, '__dict__'):
        return {str(k): _jsonable(v) for k, v in vars(value).items()}
    return str(value)


def _make_prompt(tokenizer, target_tokens: int) -> str:
    stub = "The quick brown fox jumps over the lazy dog. "
    text = stub
    for _ in range(20000):
        if len(tokenizer.encode(text)) >= target_tokens:
            break
        text += stub
    return text


def _make_scheduler(model, tokenizer, ssd_dir: Path, archive_dir: Path, model_name: str):
    from omlx.cache.session_archive import SessionArchiveStore
    from omlx.scheduler import Scheduler, SchedulerConfig

    cfg = SchedulerConfig(
        max_num_seqs=1,
        max_num_batched_tokens=8192,
        paged_cache_block_size=128,
        paged_ssd_cache_dir=str(ssd_dir),
        paged_ssd_cache_max_size=8 * 1024 * 1024 * 1024,
        model_name=model_name,
        initial_cache_blocks=256,
    )
    sch = Scheduler(model=model, tokenizer=tokenizer, config=cfg)
    sch.session_archive_store = SessionArchiveStore(archive_dir)
    return sch


def _drive_turn(sch, prompt: str, *, session_id, restore: bool, max_tokens: int) -> dict:
    from omlx.request import Request, SamplingParams

    req = Request(
        request_id=str(uuid.uuid4()),
        prompt=prompt,
        sampling_params=SamplingParams(max_tokens=max_tokens, temperature=0.0),
        session_id=session_id,
        restore=restore,
    )

    t0 = time.perf_counter()
    sch.add_request(req)

    num_prompt = int(getattr(req, "num_prompt_tokens", 0) or 0)
    cached_after_add = int(getattr(req, "cached_tokens", 0) or 0)
    shared_prefix_blocks = int(getattr(req, "shared_prefix_blocks", 0) or 0)
    block_ids_after_add = 0
    bt = getattr(req, "block_table", None)
    if bt is not None:
        block_ids_after_add = len(list(getattr(bt, "block_ids", []) or []))

    seen_completion = 0
    finished = False
    ttft_ms = 0.0
    prefill_ms = 0.0
    steps = 0
    max_steps = max(32, num_prompt // 256 + max_tokens + 32)
    while sch.has_requests() and not finished and steps < max_steps:
        out = sch.step()
        steps += 1
        now = time.perf_counter()
        for o in out.outputs:
            if o.request_id != req.request_id:
                continue
            completion = int(getattr(o, "completion_tokens", 0) or 0)
            if ttft_ms == 0.0 and completion > seen_completion:
                ttft_ms = round((now - t0) * 1000.0, 2)
            seen_completion = completion
            if getattr(o, "finished", False):
                finished = True
        if prefill_ms == 0.0 and num_prompt > 0:
            if int(getattr(req, "num_computed_tokens", 0) or 0) >= num_prompt:
                prefill_ms = round((now - t0) * 1000.0, 2)

    t_end = time.perf_counter()
    try:
        sch.remove_finished_request(req.request_id)
    except Exception:
        pass

    snap = sch.get_runtime_metrics_snapshot()
    req_snap = snap.get("requests", {}).get(req.request_id, {})
    total_ms = round((t_end - t0) * 1000.0, 2)
    completion_tokens = int(req_snap.get("completion_tokens", seen_completion) or 0)
    if completion_tokens <= 0:
        completion_tokens = seen_completion

    return {
        "num_prompt_tokens": num_prompt,
        "cached_after_add": cached_after_add,
        "shared_prefix_blocks": shared_prefix_blocks,
        "block_ids_after_add": block_ids_after_add,
        "ttft_ms": float(req_snap.get("ttft_ms", ttft_ms) or ttft_ms),
        "prefill_ms": float(req_snap.get("prefill_ms", prefill_ms) or prefill_ms),
        "decode_ms": float(req_snap.get("decode_ms", 0.0) or 0.0),
        "total_ms": float(req_snap.get("total_ms", total_ms) or total_ms),
        "completion_tokens": completion_tokens,
        "throughput_tps": float(req_snap.get("throughput_tps", 0.0) or 0.0),
        "finished": finished,
        "steps": steps,
        "cache_hit": bool(req_snap.get("cache_hit", cached_after_add > 0 or shared_prefix_blocks > 0)),
        "restore_requested": bool(req_snap.get("restore_requested", restore)),
        "restore_succeeded": bool(req_snap.get("restore_succeeded", False)),
        "restore_ms": float(req_snap.get("restore_ms", 0.0) or 0.0),
        "resident_blocks": int(req_snap.get("resident_blocks", block_ids_after_add) or 0),
        "avg_batch_size": float(req_snap.get("avg_batch_size", snap.get("avg_batch_size", 1.0)) or 0.0),
        "peak_batch_size": int(req_snap.get("peak_batch_size", snap.get("peak_batch_size", 1)) or 0),
        "tail_ms_p95": float(snap.get("tail_ms_p95", total_ms) or total_ms),
        "executor_boundary_mode": str(snap.get("executor_boundary_mode", "unknown") or "unknown"),
        "executor_steps": int(snap.get("executor_steps", 0) or 0),
        "executor_finish_overrides": int(snap.get("executor_finish_overrides", 0) or 0),
        "executor_cancel_suppressed": int(snap.get("executor_cancel_suppressed", 0) or 0),
        "cache_stats": _jsonable(sch.get_cache_stats() or {}),
        "ssd_stats": _jsonable(sch.get_ssd_cache_stats() or {}),
    }


def main() -> None:
    path = os.environ["OMLX_RUNTIME_BENCH_PATH"]
    scenario = os.environ["OMLX_RUNTIME_BENCH_SCENARIO"]
    ssd = Path(os.environ["OMLX_BENCH_SSD"])
    archive = Path(os.environ["OMLX_BENCH_ARCHIVE"])
    model_name = os.environ["OMLX_BENCH_MODEL"]
    prompt_tokens = int(os.environ.get("OMLX_BENCH_PROMPT_TOKENS", "2000"))
    max_tokens = int(os.environ.get("OMLX_BENCH_MAX_TOKENS", "16"))
    session_id = os.environ.get("OMLX_BENCH_SESSION_ID", "runtime-bench-1")

    from omlx.utils.model_loading import load_text_model

    model, tokenizer = load_text_model(model_name)
    os.environ["OMLX_EXECUTOR_BOUNDARY_MODE"] = path
    sch = _make_scheduler(model, tokenizer, ssd, archive, model_name)
    sch.runtime_metrics.enabled = True

    prompt = _make_prompt(tokenizer, prompt_tokens)
    if scenario == "cold":
        turn = _drive_turn(sch, prompt, session_id=session_id, restore=False, max_tokens=max_tokens)
    elif scenario == "restart_cache":
        turn = _drive_turn(sch, prompt, session_id=None, restore=False, max_tokens=max_tokens)
    elif scenario == "restart_restore":
        turn = _drive_turn(sch, prompt, session_id=session_id, restore=True, max_tokens=max_tokens)
    else:
        raise SystemExit(f"unknown scenario={scenario!r}")

    bad = []
    for pat in ("*.safetensors", "*.bin", "*.npy", "*.pt"):
        bad.extend(str(p.relative_to(archive)) for p in archive.rglob(pat))

    result = {
        "path": path,
        "scenario": scenario,
        "turn": turn,
        "archive_non_manifest_files": bad,
    }

    try:
        pssd = getattr(sch, "paged_ssd_cache_manager", None)
        if pssd is not None:
            pssd.close()
    except Exception:
        pass
    del sch
    gc.collect()

    sys.stdout.write("RESULT:" + json.dumps(result) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
