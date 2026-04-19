# SPDX-License-Identifier: Apache-2.0
"""Tests for the runtime lifecycle metrics recorder."""

from __future__ import annotations

from omlx.runtime_metrics import RuntimeMetricsRecorder


def test_runtime_metrics_capture_request_lifecycle() -> None:
    rec = RuntimeMetricsRecorder(enabled=True)
    rec.admit_request("r1", prompt_tokens=128, restore_requested=True)
    rec.note_cache_state(
        "r1",
        cached_tokens=64,
        shared_prefix_blocks=2,
        resident_blocks=3,
    )
    rec.mark_restore("r1", succeeded=True, duration_ms=4.25)
    rec.mark_prefill_start("r1")
    rec.mark_batch(1)
    rec.mark_prefill_end("r1")
    rec.mark_token("r1", completion_tokens=1, batch_size=1)
    rec.mark_token("r1", completion_tokens=3, batch_size=1)
    rec.mark_finished("r1", finish_reason="stop", completion_tokens=3)

    snap = rec.snapshot(cache_stats={"kind": "cache"}, ssd_stats={"kind": "ssd"})
    req = snap["requests"]["r1"]

    assert snap["completed_requests"] == 1
    assert snap["cache_hits"] == 1
    assert snap["restore_attempts"] == 1
    assert snap["restore_successes"] == 1
    assert req["prompt_tokens"] == 128
    assert req["completion_tokens"] == 3
    assert req["cache_hit"] is True
    assert req["restore_requested"] is True
    assert req["restore_succeeded"] is True
    assert req["restore_ms"] == 4.25
    assert req["peak_batch_size"] == 1
    assert req["resident_blocks"] == 3
    assert req["throughput_tps"] >= 0.0
    assert req["ttft_ms"] >= 0.0
    assert req["prefill_ms"] >= 0.0
    assert req["total_ms"] >= 0.0


def test_runtime_metrics_can_be_disabled() -> None:
    rec = RuntimeMetricsRecorder(enabled=False)
    rec.admit_request("r2", prompt_tokens=32, restore_requested=False)
    rec.mark_prefill_start("r2")
    rec.mark_prefill_end("r2")
    rec.mark_token("r2", completion_tokens=1, batch_size=1)
    rec.mark_finished("r2", finish_reason="stop", completion_tokens=1)

    snap = rec.snapshot()
    assert snap["enabled"] is False
    assert snap["completed_requests"] == 0
    assert snap["requests"] == {}
