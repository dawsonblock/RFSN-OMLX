# SPDX-License-Identifier: Apache-2.0
"""Benchmark worker: drives one scenario in a fresh Python process.

Scenarios (selected via ``OMLX_BENCH_MODE``):

* ``AB`` — cold turn A (session_id set, restore=False → commits a manifest)
  followed immediately by warm turn B (no session; shared prefix cache
  should hit). Same process, no teardown between A and B.
* ``D`` — fresh process on the same ssd + archive dirs, no session fields.
  Measures the restart path relying only on the paged SSD prefix cache.
* ``C`` — fresh process on the same ssd + archive dirs, session_id set and
  ``restore=True``. Measures the restart path with the explicit session
  archive restore.

Emits a single ``RESULT:<json>`` line on stdout with per-turn metrics.
"""

from __future__ import annotations

import gc
import json
import os
import sys
import time
import uuid
from pathlib import Path


def _make_prompt(tokenizer, target_tokens: int) -> str:
    stub = "The quick brown fox jumps over the lazy dog. "
    text = stub
    # Grow until we clear the target. Overshoot is fine; we record the
    # exact token count per turn.
    for _ in range(20000):
        if len(tokenizer.encode(text)) >= target_tokens:
            break
        text += stub
    return text


def _make_scheduler(model, tokenizer, ssd_dir: Path, archive_dir: Path, model_name: str):
    from omlx.scheduler import Scheduler, SchedulerConfig
    from omlx.cache.session_archive import SessionArchiveStore

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


def _drive_turn(sch, prompt: str, *, session_id, restore: bool, max_tokens: int = 4) -> dict:
    """Drive one turn through the real scheduler; return wall-clock metrics."""
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
    t_after_add = time.perf_counter()

    num_prompt = int(getattr(req, "num_prompt_tokens", 0) or 0)
    cached_after_add = int(getattr(req, "num_computed_tokens", 0) or 0)
    block_ids_after_add = 0
    bt = getattr(req, "block_table", None)
    if bt is not None:
        block_ids_after_add = len(list(getattr(bt, "block_ids", []) or []))

    ttft_ms: float | None = None
    prefill_ms: float | None = None
    seen_completion = 0
    finished = False

    # Safety bound: prompt / prefill step size + decode headroom.
    max_steps = max(32, num_prompt // 256 + max_tokens + 32)
    steps = 0
    while sch.has_requests() and not finished and steps < max_steps:
        out = sch.step()
        steps += 1
        now = time.perf_counter()
        for o in out.outputs:
            if o.request_id != req.request_id:
                continue
            completion = int(getattr(o, "completion_tokens", 0) or 0)
            if ttft_ms is None and completion > seen_completion:
                ttft_ms = (now - t0) * 1000.0
            seen_completion = completion
            if getattr(o, "finished", False):
                finished = True

        # Prefill is "done" the first step after num_computed_tokens
        # reaches num_prompt. This is a stable structural signal.
        if prefill_ms is None and num_prompt > 0:
            if int(getattr(req, "num_computed_tokens", 0) or 0) >= num_prompt:
                prefill_ms = (now - t0) * 1000.0

    t_end = time.perf_counter()

    try:
        sch.remove_finished_request(req.request_id)
    except Exception:
        pass

    return {
        "num_prompt_tokens": num_prompt,
        "cached_after_add": cached_after_add,
        "block_ids_after_add": block_ids_after_add,
        "ttft_ms": round(ttft_ms or 0.0, 2),
        "prefill_ms": round(prefill_ms or 0.0, 2),
        "total_ms": round((t_end - t0) * 1000.0, 2),
        "completion_tokens": seen_completion,
        "finished": finished,
        "steps": steps,
    }


def main() -> None:
    mode = os.environ["OMLX_BENCH_MODE"]
    ssd = Path(os.environ["OMLX_BENCH_SSD"])
    archive = Path(os.environ["OMLX_BENCH_ARCHIVE"])
    model_name = os.environ["OMLX_BENCH_MODEL"]
    prompt_tokens = int(os.environ.get("OMLX_BENCH_PROMPT_TOKENS", "4000"))
    session_id = os.environ.get("OMLX_BENCH_SESSION_ID", "bench-sess-1")

    from omlx.utils.model_loading import load_text_model

    model, tokenizer = load_text_model(model_name)

    result: dict = {"mode": mode, "model": model_name}
    sch = _make_scheduler(model, tokenizer, ssd, archive, model_name)
    prompt = _make_prompt(tokenizer, prompt_tokens)

    if mode == "AB":
        result["A"] = _drive_turn(
            sch, prompt, session_id=session_id, restore=False
        )
        result["B"] = _drive_turn(
            sch, prompt, session_id=None, restore=False
        )
    elif mode == "D":
        result["D"] = _drive_turn(
            sch, prompt, session_id=None, restore=False
        )
    elif mode == "C":
        result["C"] = _drive_turn(
            sch, prompt, session_id=session_id, restore=True
        )
    else:
        raise SystemExit(f"unknown OMLX_BENCH_MODE={mode!r}")

    # Runtime invariant check: archive must be metadata-only (no tensor
    # blobs written under the session archive root).
    bad = []
    for pat in ("*.safetensors", "*.bin", "*.npy", "*.pt"):
        bad.extend(str(p.relative_to(archive)) for p in archive.rglob(pat))
    result["archive_non_manifest_files"] = bad

    # Best-effort teardown so the SSD writer flushes before process exit.
    try:
        pssd = getattr(sch, "paged_ssd_cache_manager", None)
        if pssd is not None:
            for name in ("shutdown", "close", "flush"):
                fn = getattr(pssd, name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
    except Exception:
        pass
    del sch
    gc.collect()

    sys.stdout.write("RESULT:" + json.dumps(result) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
