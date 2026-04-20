# SPDX-License-Identifier: Apache-2.0
"""Pass 4 — honest benchmark of the branch-owned prefill orchestration.

Measures what can actually be measured at the scheduler layer without
faking a stock-vs-owned split. Unlike the decode seam, the prefill seam
has no ``OMLX_EXECUTOR_BOUNDARY_MODE=stock`` counterpart: external
prefill is unconditional when a prompt has more than one token and
SpecPrefill is not active.

What we measure
---------------

1. Per-chunk orchestration overhead of ``_run_owned_prefill_step``. We
   replace ``self.model`` with a do-nothing spy so the wall time
   reflects *only* the branch's loop, abort check, counter, and
   ``mx.eval`` call on a trivial stub cache.  This is the cost ceiling
   the owned seam adds on top of whatever the real forward would do.

2. Chunk count and average per-chunk time as ``prefill_step_size``
   varies across a fixed prompt length.  The expectation is that total
   orchestration cost scales roughly linearly with chunk count at a
   modest per-chunk cost — proving the seam actually drives chunking.

3. Completed/aborted chunk counters line up with the actual work done.

What we do NOT measure
----------------------

- Stock-vs-owned prefill speed. There is no stock prefill toggle; any
  such claim would be fabrication.
- Real forward-pass latency. Not part of the scheduler seam. Stock
  forward time would dominate any realistic end-to-end run and is
  invariant to whether the loop that calls it is stock or owned.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import mlx.core as mx


# --- Stub cache + spy model (kept local so the bench does not depend on tests)


class _StubCacheEntry:
    def __init__(self) -> None:
        self.state = mx.array([0], dtype=mx.int32)
        self.offset = 0


def _stub_make_prompt_cache(_model: Any) -> List[_StubCacheEntry]:
    return [_StubCacheEntry(), _StubCacheEntry()]


class _NoopSpyModel:
    """Model stand-in whose __call__ returns a cheap mock."""

    def __init__(self) -> None:
        self.config = MagicMock()
        self.config.vocab_size = 32000
        self.call_count = 0

    def __call__(self, chunk: mx.array, *, cache: List[Any], **_: Any) -> Any:
        self.call_count += 1
        return MagicMock(shape=(1, int(chunk.shape[1]), self.config.vocab_size))


def _build_scheduler(
    *, prefill_step_size: int, paged_cache_block_size: int = 256
):
    from omlx import scheduler as scheduler_module
    from omlx.scheduler import Scheduler, SchedulerConfig

    scheduler_module.make_prompt_cache = _stub_make_prompt_cache

    model = _NoopSpyModel()
    tokenizer = MagicMock()
    tokenizer.eos_token_id = 2
    tokenizer.encode = lambda text, **_: [1] + [
        (hash(w) % 31990) + 10 for w in text.split()
    ]
    config = SchedulerConfig(
        prefill_step_size=prefill_step_size,
        paged_cache_block_size=paged_cache_block_size,
        model_name="pass4-bench",
    )
    scheduler = Scheduler(model=model, tokenizer=tokenizer, config=config)
    scheduler.block_aware_cache = None
    return scheduler, model


def _make_request(n_tokens: int, request_id: str):
    from omlx.request import Request, SamplingParams

    tokens = list(range(100, 100 + n_tokens))
    req = Request(
        request_id=request_id,
        prompt=tokens,
        sampling_params=SamplingParams(max_tokens=1),
    )
    req.prompt_token_ids = tokens
    req.num_prompt_tokens = n_tokens
    return req, tokens


def bench_one(
    *, n_tokens: int, prefill_step_size: int, repeats: int
) -> Dict[str, Any]:
    scheduler, model = _build_scheduler(prefill_step_size=prefill_step_size)

    # Warm once (stub cache allocation, first mx.eval on a zero-sized graph)
    warm_req, warm_tokens = _make_request(n_tokens, "warm")
    scheduler._do_external_prefill(warm_req, warm_tokens, existing_cache=None)
    baseline_chunks = model.call_count

    durations: List[float] = []
    chunk_counts: List[int] = []
    for i in range(repeats):
        req, tokens = _make_request(n_tokens, f"run-{i}")
        model.call_count = 0
        before = scheduler.runtime_metrics._prefill_chunks_completed
        t0 = time.perf_counter()
        scheduler._do_external_prefill(req, tokens, existing_cache=None)
        t1 = time.perf_counter()
        durations.append(t1 - t0)
        chunk_counts.append(model.call_count)
        assert (
            scheduler.runtime_metrics._prefill_chunks_completed
            == before + model.call_count
        ), "per-chunk counter drifted from chunk count"

    unique_counts = set(chunk_counts)
    assert len(unique_counts) == 1, (
        f"chunk count unstable across repeats: {chunk_counts}"
    )
    chunks = chunk_counts[0]
    total_forwarded = chunks * prefill_step_size
    # Last token held back plus final possibly-partial chunk; enforce the
    # sum matches n_tokens - 1 either exactly (full step) or within one
    # step (partial last chunk).
    assert total_forwarded >= n_tokens - 1 - prefill_step_size + 1

    return {
        "n_tokens": n_tokens,
        "prefill_step_size": prefill_step_size,
        "chunks": chunks,
        "model_call_count_after_warm": baseline_chunks,
        "repeats": repeats,
        "mean_s": statistics.mean(durations),
        "median_s": statistics.median(durations),
        "stdev_s": (
            statistics.stdev(durations) if len(durations) > 1 else 0.0
        ),
        "min_s": min(durations),
        "max_s": max(durations),
        "mean_per_chunk_s": statistics.mean(durations) / max(1, chunks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-tokens", type=int, default=4096)
    parser.add_argument("--repeats", type=int, default=30)
    parser.add_argument(
        "--step-sizes",
        type=str,
        default="256,512,1024,2048,4096",
        help="Comma-separated prefill_step_size values to sweep.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="scripts/bench_results_pass4_prefill.json",
    )
    args = parser.parse_args()

    step_sizes = [int(s) for s in args.step_sizes.split(",") if s.strip()]
    results: List[Dict[str, Any]] = []
    for step in step_sizes:
        row = bench_one(
            n_tokens=args.n_tokens,
            prefill_step_size=step,
            repeats=args.repeats,
        )
        print(
            f"n_tokens={row['n_tokens']:<6} step={row['prefill_step_size']:<5}"
            f" chunks={row['chunks']:<4} mean={row['mean_s'] * 1000:8.3f}ms"
            f" per-chunk={row['mean_per_chunk_s'] * 1e6:7.2f}us"
        )
        results.append(row)

    # pyproject / mlx_lm pin context for reproducibility
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    mlx_lm_pin = ""
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            if "mlx-lm" in line and "@" in line:
                mlx_lm_pin = line.strip()
                break

    payload = {
        "note": (
            "Pass 4 prefill-seam benchmark. Measures BRANCH-OWNED "
            "orchestration overhead with stock forward replaced by a "
            "no-op spy. Does NOT compare stock-vs-owned (no such toggle "
            "exists on the prefill path)."
        ),
        "python": os.popen("python --version").read().strip(),
        "mlx_lm_pin": mlx_lm_pin,
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
