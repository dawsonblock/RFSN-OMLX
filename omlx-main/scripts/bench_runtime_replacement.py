# SPDX-License-Identifier: Apache-2.0
"""Benchmark the executor-boundary seam against the legacy stock handoff.

This is intentionally blocker-first and conservative. It compares:

- ``stock`` — the previous direct scheduler -> BatchGenerator handoff
- ``owned`` — the branch-owned scheduler seam that now controls the decode-step
  boundary, cancellation suppression, and local finish normalization

It does **not** claim a fully replaced engine. The purpose is to measure
whether the new seam changes authority and what, if any, overhead it adds.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts" / "_bench_runtime_replacement_worker.py"
DEFAULT_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
SCENARIOS = ("cold", "restart_cache", "restart_restore")
PATHS = ("stock", "owned")


def _run_worker(env_overlay: dict, timeout: int = 900) -> dict:
    env = {**os.environ, **env_overlay}
    env.setdefault("PYENV_VERSION", "3.10.12")
    proc = subprocess.run(
        [sys.executable, str(WORKER)],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(
            f"runtime bench worker failed "
            f"(path={env_overlay.get('OMLX_RUNTIME_BENCH_PATH')} "
            f"scenario={env_overlay.get('OMLX_RUNTIME_BENCH_SCENARIO')})"
        )
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    sys.stderr.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    raise SystemExit("worker produced no RESULT line")


def _wipe(dirpath: Path) -> None:
    if dirpath.exists():
        shutil.rmtree(dirpath)
    dirpath.mkdir(parents=True, exist_ok=True)


def _metric_median(rows: list[dict], key: str) -> float:
    vals = sorted(float(r["turn"].get(key, 0.0) or 0.0) for r in rows)
    if not vals:
        return 0.0
    return round(vals[len(vals) // 2], 2)


def _summarize(rows: list[dict]) -> dict:
    if not rows:
        return {}
    sample = rows[-1]["turn"]
    return {
        "ttft_ms_median": _metric_median(rows, "ttft_ms"),
        "prefill_ms_median": _metric_median(rows, "prefill_ms"),
        "decode_ms_median": _metric_median(rows, "decode_ms"),
        "total_ms_median": _metric_median(rows, "total_ms"),
        "throughput_tps_median": _metric_median(rows, "throughput_tps"),
        "tail_ms_p95_median": _metric_median(rows, "tail_ms_p95"),
        "cache_hit_count": sum(1 for r in rows if r["turn"].get("cache_hit")),
        "restore_success_count": sum(1 for r in rows if r["turn"].get("restore_succeeded")),
        "peak_batch_size": max(int(r["turn"].get("peak_batch_size", 0) or 0) for r in rows),
        "executor_steps_last": int(sample.get("executor_steps", 0) or 0),
        "executor_finish_overrides_last": int(sample.get("executor_finish_overrides", 0) or 0),
        "executor_cancel_suppressed_last": int(sample.get("executor_cancel_suppressed", 0) or 0),
        "resident_blocks_last": int(sample.get("resident_blocks", 0) or 0),
        "cached_after_add_last": int(sample.get("cached_after_add", 0) or 0),
        "shared_prefix_blocks_last": int(sample.get("shared_prefix_blocks", 0) or 0),
        "archive_non_manifest_files": rows[-1].get("archive_non_manifest_files", []),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--prompt-tokens", type=int, default=2000)
    ap.add_argument("--max-tokens", type=int, default=16)
    ap.add_argument(
        "--out",
        default=str(ROOT / "scripts" / "bench_results_runtime_replacement.json"),
    )
    ap.add_argument("--keep-tmp", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="omlx-runtime-bench-"))
    try:
        all_rows: list[dict] = []
        for rep in range(args.reps):
            for path in PATHS:
                ssd = tmp / f"{path}-ssd"
                archive = tmp / f"{path}-archive"
                _wipe(ssd)
                _wipe(archive)
                common = {
                    "OMLX_BENCH_MODEL": args.model,
                    "OMLX_BENCH_PROMPT_TOKENS": str(args.prompt_tokens),
                    "OMLX_BENCH_MAX_TOKENS": str(args.max_tokens),
                    "OMLX_BENCH_SSD": str(ssd),
                    "OMLX_BENCH_ARCHIVE": str(archive),
                    "OMLX_BENCH_SESSION_ID": "runtime-bench-1",
                    "OMLX_RUNTIME_BENCH_PATH": path,
                }
                for scenario in SCENARIOS:
                    row = _run_worker({**common, "OMLX_RUNTIME_BENCH_SCENARIO": scenario})
                    row["rep"] = rep
                    all_rows.append(row)

        summary = {
            path: {
                scenario: _summarize(
                    [r for r in all_rows if r["path"] == path and r["scenario"] == scenario]
                )
                for scenario in SCENARIOS
            }
            for path in PATHS
        }
        out = {
            "model": args.model,
            "reps": args.reps,
            "prompt_tokens": args.prompt_tokens,
            "max_tokens": args.max_tokens,
            "summary": summary,
            "rows": all_rows,
        }
        Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2))
    finally:
        if not args.keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
