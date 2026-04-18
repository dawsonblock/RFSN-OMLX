# SPDX-License-Identifier: Apache-2.0
"""Benchmark: does the session-archive feature earn its complexity?

Spawns three subprocess runs per rep (AB, D, C) using the worker at
``scripts/_bench_session_archive_worker.py``. See that file for the
scenario semantics. Takes the min across reps to neutralize MLX JIT
warmup noise.

Outputs ``scripts/bench_results_session_archive.json`` and prints a
compact summary to stdout.

Usage::

    PYENV_VERSION=3.10.12 python scripts/bench_session_archive.py \\
        --model mlx-community/Qwen2.5-0.5B-Instruct-4bit \\
        --prompt-tokens 4000 --reps 3
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
WORKER = ROOT / "scripts" / "_bench_session_archive_worker.py"
DEFAULT_MODEL = "mlx-community/Qwen2.5-0.5B-Instruct-4bit"


def _run_worker(env_overlay: dict, timeout: int = 600) -> dict:
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
        raise SystemExit(f"worker failed (mode={env_overlay.get('OMLX_BENCH_MODE')})")
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


def _aggregate(cycles: list[dict]) -> dict:
    agg: dict = {}
    for key in ("A", "B", "D", "C"):
        m: dict = {}
        time_fields = ("ttft_ms", "prefill_ms", "total_ms")
        info_fields = (
            "num_prompt_tokens",
            "cached_after_add",
            "block_ids_after_add",
            "completion_tokens",
            "steps",
        )
        if not any(key in c for c in cycles):
            continue
        present = [c[key] for c in cycles if key in c]
        for f in time_fields:
            vals = [p.get(f, 0.0) for p in present]
            m[f + "_min"] = round(min(vals), 2)
            m[f + "_median"] = round(sorted(vals)[len(vals) // 2], 2)
        for f in info_fields:
            # Informational fields: take the latest rep's value (stable
            # across reps in practice since the prompt is deterministic).
            m[f] = present[-1].get(f, 0)
        agg[key] = m
    return agg


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--prompt-tokens", type=int, default=4000)
    ap.add_argument(
        "--out",
        default=str(ROOT / "scripts" / "bench_results_session_archive.json"),
    )
    ap.add_argument("--keep-tmp", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="omlx-bench-"))
    ssd = tmp / "ssd"
    archive = tmp / "archive"
    try:
        cycles: list[dict] = []
        for rep in range(args.reps):
            _wipe(ssd)
            _wipe(archive)
            common = {
                "OMLX_BENCH_MODEL": args.model,
                "OMLX_BENCH_PROMPT_TOKENS": str(args.prompt_tokens),
                "OMLX_BENCH_SSD": str(ssd),
                "OMLX_BENCH_ARCHIVE": str(archive),
            }
            ab = _run_worker({**common, "OMLX_BENCH_MODE": "AB"})
            d = _run_worker({**common, "OMLX_BENCH_MODE": "D"})
            c = _run_worker({**common, "OMLX_BENCH_MODE": "C"})
            cycles.append(
                {
                    "rep": rep,
                    "A": ab["A"],
                    "B": ab["B"],
                    "D": d["D"],
                    "C": c["C"],
                    "archive_non_manifest_files": (
                        ab.get("archive_non_manifest_files", [])
                        + d.get("archive_non_manifest_files", [])
                        + c.get("archive_non_manifest_files", [])
                    ),
                }
            )

        agg = _aggregate(cycles)
        out = {
            "model": args.model,
            "prompt_tokens_target": args.prompt_tokens,
            "reps": args.reps,
            "aggregate": agg,
            "cycles": cycles,
        }
        Path(args.out).write_text(json.dumps(out, indent=2))
        print(json.dumps(agg, indent=2))
    finally:
        if not args.keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
