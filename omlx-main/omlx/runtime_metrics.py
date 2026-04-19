# SPDX-License-Identifier: Apache-2.0
"""Internal runtime lifecycle and benchmark metrics.

This module is intentionally small and stdlib-only. It gives the scheduler
one authoritative place to record request lifecycle truth:

- request admission
- cache reuse at intake
- restore attempt / success / failure
- prefill start / completion
- first-token latency (TTFT)
- decode completion
- batch-size behavior

It does **not** change decode policy or batching semantics. The goal of this
first pass is ownership of measurement and request lifecycle observability so a
future runtime replacement can be judged from evidence rather than guesswork.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


__all__ = [
    "RuntimeMetricsRecorder",
    "RequestRuntimeTrace",
]


def _now() -> float:
    return time.perf_counter()


def _ms(start: Optional[float], end: Optional[float]) -> Optional[float]:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start) * 1000.0), 2)


def _quantile(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = int(round((len(vals) - 1) * q))
    idx = max(0, min(idx, len(vals) - 1))
    return round(vals[idx], 2)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if hasattr(value, "__dict__"):
        return {str(k): _jsonable(v) for k, v in vars(value).items()}
    return str(value)


@dataclass
class RequestRuntimeTrace:
    request_id: str
    admitted_at: float
    prompt_tokens: int = 0
    restore_requested: bool = False
    restore_succeeded: bool = False
    restore_reason: Optional[str] = None
    restore_ms: float = 0.0
    cached_tokens: int = 0
    shared_prefix_blocks: int = 0
    resident_blocks: int = 0
    completion_tokens: int = 0
    prefill_started_at: Optional[float] = None
    prefill_completed_at: Optional[float] = None
    first_token_at: Optional[float] = None
    finished_at: Optional[float] = None
    finish_reason: Optional[str] = None
    batch_sizes: List[int] = field(default_factory=list)

    @property
    def cache_hit(self) -> bool:
        return self.cached_tokens > 0 or self.shared_prefix_blocks > 0

    def to_dict(self) -> Dict[str, Any]:
        ttft_ms = _ms(self.admitted_at, self.first_token_at)
        prefill_ms = _ms(self.prefill_started_at, self.prefill_completed_at)
        total_ms = _ms(self.admitted_at, self.finished_at)
        decode_ms: Optional[float] = None
        if self.prefill_completed_at is not None and self.finished_at is not None:
            decode_ms = _ms(self.prefill_completed_at, self.finished_at)
        avg_batch = (
            round(sum(self.batch_sizes) / len(self.batch_sizes), 2)
            if self.batch_sizes
            else 0.0
        )
        peak_batch = max(self.batch_sizes) if self.batch_sizes else 0
        throughput_tps = 0.0
        if total_ms and total_ms > 0 and self.completion_tokens > 0:
            throughput_tps = round(self.completion_tokens / (total_ms / 1000.0), 2)
        return {
            "request_id": self.request_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cached_tokens": self.cached_tokens,
            "shared_prefix_blocks": self.shared_prefix_blocks,
            "resident_blocks": self.resident_blocks,
            "cache_hit": self.cache_hit,
            "restore_requested": self.restore_requested,
            "restore_succeeded": self.restore_succeeded,
            "restore_reason": self.restore_reason,
            "restore_ms": round(self.restore_ms, 2),
            "ttft_ms": ttft_ms or 0.0,
            "prefill_ms": prefill_ms or 0.0,
            "decode_ms": decode_ms or 0.0,
            "total_ms": total_ms or 0.0,
            "avg_batch_size": avg_batch,
            "peak_batch_size": peak_batch,
            "finish_reason": self.finish_reason,
            "throughput_tps": throughput_tps,
        }


class RuntimeMetricsRecorder:
    """Internal request lifecycle recorder for the scheduler."""

    def __init__(self, *, enabled: Optional[bool] = None, max_completed: int = 512):
        if enabled is None:
            env = os.environ.get("OMLX_RUNTIME_METRICS", "1").strip().lower()
            enabled = env not in {"0", "false", "off", "no"}
        self.enabled = bool(enabled)
        self.max_completed = max_completed
        self._active: Dict[str, RequestRuntimeTrace] = {}
        self._completed: List[RequestRuntimeTrace] = []
        self._batch_samples: List[int] = []
        self._peak_batch_size: int = 0

    def admit_request(
        self,
        request_id: str,
        *,
        prompt_tokens: int,
        restore_requested: bool = False,
    ) -> None:
        if not self.enabled:
            return
        self._active[request_id] = RequestRuntimeTrace(
            request_id=request_id,
            admitted_at=_now(),
            prompt_tokens=int(prompt_tokens or 0),
            restore_requested=bool(restore_requested),
        )

    def note_cache_state(
        self,
        request_id: str,
        *,
        cached_tokens: int,
        shared_prefix_blocks: int,
        resident_blocks: int,
    ) -> None:
        if not self.enabled:
            return
        trace = self._active.get(request_id)
        if trace is None:
            return
        trace.cached_tokens = int(cached_tokens or 0)
        trace.shared_prefix_blocks = int(shared_prefix_blocks or 0)
        trace.resident_blocks = int(resident_blocks or 0)

    def mark_restore(
        self,
        request_id: str,
        *,
        succeeded: bool,
        duration_ms: float,
        reason: Optional[str] = None,
    ) -> None:
        if not self.enabled:
            return
        trace = self._active.get(request_id)
        if trace is None:
            return
        trace.restore_succeeded = bool(succeeded)
        trace.restore_ms = float(duration_ms or 0.0)
        trace.restore_reason = reason

    def mark_prefill_start(self, request_id: str) -> None:
        if not self.enabled:
            return
        trace = self._active.get(request_id)
        if trace is None:
            return
        if trace.prefill_started_at is None:
            trace.prefill_started_at = _now()

    def mark_prefill_end(self, request_id: str) -> None:
        if not self.enabled:
            return
        trace = self._active.get(request_id)
        if trace is None:
            return
        if trace.prefill_started_at is None:
            trace.prefill_started_at = _now()
        if trace.prefill_completed_at is None:
            trace.prefill_completed_at = _now()

    def mark_batch(self, batch_size: int) -> None:
        if not self.enabled:
            return
        size = int(batch_size or 0)
        self._batch_samples.append(size)
        self._peak_batch_size = max(self._peak_batch_size, size)

    def mark_token(
        self,
        request_id: str,
        *,
        completion_tokens: int,
        batch_size: int,
    ) -> None:
        if not self.enabled:
            return
        trace = self._active.get(request_id)
        if trace is None:
            return
        if trace.prefill_started_at is not None and trace.prefill_completed_at is None:
            trace.prefill_completed_at = _now()
        if trace.first_token_at is None and int(completion_tokens or 0) > 0:
            trace.first_token_at = _now()
        trace.completion_tokens = int(completion_tokens or 0)
        size = int(batch_size or 0)
        trace.batch_sizes.append(size)
        self._batch_samples.append(size)
        self._peak_batch_size = max(self._peak_batch_size, size)

    def mark_finished(
        self,
        request_id: str,
        *,
        finish_reason: Optional[str],
        completion_tokens: int,
    ) -> None:
        if not self.enabled:
            return
        trace = self._active.pop(request_id, None)
        if trace is None:
            return
        if trace.prefill_started_at is not None and trace.prefill_completed_at is None:
            trace.prefill_completed_at = _now()
        trace.finished_at = _now()
        trace.finish_reason = finish_reason
        trace.completion_tokens = int(completion_tokens or 0)
        self._completed.append(trace)
        if len(self._completed) > self.max_completed:
            self._completed = self._completed[-self.max_completed :]

    def get_request_snapshot(self, request_id: str) -> Optional[Dict[str, Any]]:
        trace = self._active.get(request_id)
        if trace is not None:
            return trace.to_dict()
        for completed in reversed(self._completed):
            if completed.request_id == request_id:
                return completed.to_dict()
        return None

    def snapshot(
        self,
        *,
        cache_stats: Optional[Dict[str, Any]] = None,
        ssd_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        request_rows = [t.to_dict() for t in self._completed]
        ttft_vals = [r["ttft_ms"] for r in request_rows if r["ttft_ms"] > 0]
        prefill_vals = [r["prefill_ms"] for r in request_rows if r["prefill_ms"] > 0]
        decode_vals = [r["decode_ms"] for r in request_rows if r["decode_ms"] > 0]
        total_vals = [r["total_ms"] for r in request_rows if r["total_ms"] > 0]
        return {
            "enabled": self.enabled,
            "requests_tracked": len(self._active) + len(self._completed),
            "completed_requests": len(self._completed),
            "peak_batch_size": self._peak_batch_size,
            "avg_batch_size": round(sum(self._batch_samples) / len(self._batch_samples), 2)
            if self._batch_samples
            else 0.0,
            "ttft_ms_p50": _quantile(ttft_vals, 0.50),
            "ttft_ms_p95": _quantile(ttft_vals, 0.95),
            "prefill_ms_p50": _quantile(prefill_vals, 0.50),
            "prefill_ms_p95": _quantile(prefill_vals, 0.95),
            "decode_ms_p50": _quantile(decode_vals, 0.50),
            "decode_ms_p95": _quantile(decode_vals, 0.95),
            "tail_ms_p95": _quantile(total_vals, 0.95),
            "cache_hits": sum(1 for r in request_rows if r["cache_hit"]),
            "cache_misses": sum(1 for r in request_rows if not r["cache_hit"]),
            "restore_attempts": sum(1 for r in request_rows if r["restore_requested"]),
            "restore_successes": sum(1 for r in request_rows if r["restore_succeeded"]),
            "restore_failures": sum(
                1
                for r in request_rows
                if r["restore_requested"] and not r["restore_succeeded"]
            ),
            "cache": _jsonable(cache_stats or {}),
            "ssd": _jsonable(ssd_stats or {}),
            "requests": {r["request_id"]: r for r in request_rows},
        }

    def reset(self) -> None:
        self._active.clear()
        self._completed.clear()
        self._batch_samples.clear()
        self._peak_batch_size = 0
