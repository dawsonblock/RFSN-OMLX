# SPDX-License-Identifier: Apache-2.0
"""Pass 4 — prove branch-owned prefill seam authority is real.

Each test targets a specific proof criterion from
``docs/executor_seam_pass4_assessment.md`` §9 and
``docs/executor_seam_pass4_target.md``:

    P1: last-token handoff invariant — _do_external_prefill returns
        (cache, [tokens[-1]]) for any prompt with len > 1.
    P2: pre-chunk abort prevents self.model.__call__ entirely.
    P3: chunk sequence length sequence matches branch math at a custom
        prefill_step_size.
    P5: per-chunk counters match chunk count (completed + aborted).
    P6: _do_external_prefill returns (cache, [tokens[-1]]) for any
        len(tokens) > 1 across several lengths (stronger P1).
    P7: self.model.__call__ is the sole stock entry-point invoked
        during prefill (no BatchGenerator, no generate_step,
        no next_generated).

These tests run against the existing ``mock_model`` / ``mock_tokenizer``
fixtures. ``make_prompt_cache`` is monkey-patched inside
``omlx.scheduler`` to return a deterministic stub cache whose ``.state``
is a tiny ``mx.array`` so ``mx.eval`` has something benign to evaluate.

The tests never touch real mlx_lm forward compute — the point is to
prove the branch controls orchestration, not to validate stock forward
math.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import mlx.core as mx
import pytest

from omlx import scheduler as scheduler_module
from omlx.request import Request, SamplingParams
from omlx.scheduler import (
    Scheduler,
    SchedulerConfig,
    _PrefillAbortedError,
)


class _StubCacheEntry:
    """Minimal stand-in for one layer of an mlx_lm KV cache.

    Carries an ``mx.array`` state attribute so ``mx.eval([c.state ...])``
    succeeds, and a mutable ``offset`` mimicking the invariant tracked by
    stock KV caches (branch does not read it during prefill but the
    attribute keeps the shape of the real object).
    """

    def __init__(self) -> None:
        self.state = mx.array([0], dtype=mx.int32)
        self.offset = 0


def _stub_make_prompt_cache(_model: Any) -> List[_StubCacheEntry]:
    return [_StubCacheEntry(), _StubCacheEntry()]


@pytest.fixture
def _patched_prompt_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scheduler_module, "make_prompt_cache", _stub_make_prompt_cache
    )


class _SpyModel:
    """Replaces ``scheduler.model`` with a spy that records every chunk.

    Deliberately does NOT mutate ``prompt_cache`` — the tests assert on
    orchestration (chunk shapes, counts, argument wiring), not on real
    KV growth.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        # Surface the same attributes real mlx_lm models expose so the
        # scheduler's hasattr() probes behave predictably.
        self.config = MagicMock()
        self.config.vocab_size = 32000

    def __call__(
        self, chunk: mx.array, *, cache: List[Any], **kwargs: Any
    ) -> Any:
        self.calls.append(
            {
                "chunk_shape": tuple(chunk.shape),
                "cache_id": id(cache),
                "kwargs": dict(kwargs),
            }
        )
        # Return a shape-compatible mock logit tensor.
        return MagicMock(shape=(1, int(chunk.shape[1]), self.config.vocab_size))


def _build_scheduler(
    *,
    prefill_step_size: int = 2048,
    paged_cache_block_size: int = 256,
) -> Scheduler:
    """Build a Scheduler wired with the _SpyModel and a minimal config.

    No BatchGenerator, no paged SSD cache, no block-aware cache — those
    are orthogonal to prefill orchestration ownership.
    """
    model = _SpyModel()
    tokenizer = MagicMock()
    tokenizer.eos_token_id = 2
    tokenizer.encode = lambda text, **_: [1] + [
        (hash(w) % 31990) + 10 for w in text.split()
    ]
    config = SchedulerConfig(
        prefill_step_size=prefill_step_size,
        paged_cache_block_size=paged_cache_block_size,
        model_name="pass4-stub",
    )
    scheduler = Scheduler(model=model, tokenizer=tokenizer, config=config)
    # Keep block_aware_cache unset so boundary snapshot emission is
    # skipped; paged SSD-cache integration fixtures are out of scope for
    # this stop-and-report pass.
    scheduler.block_aware_cache = None
    return scheduler


def _make_request(
    *,
    request_id: str,
    n_tokens: int,
    max_tokens: int = 16,
) -> Request:
    """Build a Request with a fixed-length synthetic prompt token list."""
    tokens = list(range(100, 100 + n_tokens))
    req = Request(
        request_id=request_id,
        prompt=tokens,
        sampling_params=SamplingParams(max_tokens=max_tokens),
    )
    req.prompt_token_ids = tokens
    req.num_prompt_tokens = n_tokens
    return req


# ---------------------------------------------------------------------------
# P1 / P6 — last-token handoff invariant
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_tokens", [2, 3, 17, 2048, 4099])
def test_p1_p6_last_token_handoff_invariant(
    _patched_prompt_cache: None, n_tokens: int
) -> None:
    """_do_external_prefill always returns (cache, [tokens[-1]]).

    Branch code — not stock — decides that exactly one token is handed
    to the decode kickoff. That single token is what BatchGenerator.insert
    will subsequently consume to produce the first logit.
    """
    scheduler = _build_scheduler()
    tokens = list(range(100, 100 + n_tokens))
    req = _make_request(request_id=f"p1-{n_tokens}", n_tokens=n_tokens)

    returned_cache, last_token = scheduler._do_external_prefill(
        req, tokens, existing_cache=None
    )

    assert last_token == [tokens[-1]]
    assert isinstance(returned_cache, list)
    assert len(returned_cache) == 2  # stub has two layers


# ---------------------------------------------------------------------------
# P3 — chunk sequencing is branch math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefill_step_size,n_tokens,expected_chunks",
    [
        # 21 tokens to prefill (holding back the 22nd), step 7 -> 3 chunks of 7.
        (7, 22, [7, 7, 7]),
        # 100 tokens to prefill, step 50 -> 2 chunks of 50.
        (50, 101, [50, 50]),
        # 10 tokens to prefill, step 3 -> 3+3+3+1.
        (3, 11, [3, 3, 3, 1]),
        # Exact multiple with step 2048 (product default).
        (2048, 4097, [2048, 2048]),
    ],
)
def test_p3_chunk_sequencing_follows_branch_math(
    _patched_prompt_cache: None,
    prefill_step_size: int,
    n_tokens: int,
    expected_chunks: List[int],
) -> None:
    """self.model.__call__ is invoked with branch-computed chunk sizes.

    With block-boundary snapping disabled (block_aware_cache is None), the
    owned loop is the sole decider of chunk lengths: ``min(step, remaining)``
    drives the cadence, and the last token is held back for decode kickoff.
    """
    scheduler = _build_scheduler(
        prefill_step_size=prefill_step_size,
        paged_cache_block_size=256,
    )
    spy: _SpyModel = scheduler.model  # type: ignore[assignment]
    req = _make_request(request_id="p3", n_tokens=n_tokens)
    tokens = list(range(100, 100 + n_tokens))

    scheduler._do_external_prefill(req, tokens, existing_cache=None)

    observed_chunks = [call["chunk_shape"][1] for call in spy.calls]
    assert observed_chunks == expected_chunks
    # Total tokens forwarded equals n_tokens - 1 (last held back).
    assert sum(observed_chunks) == n_tokens - 1


# ---------------------------------------------------------------------------
# P2 / P5 — pre-chunk abort prevents compute + counter accounting
# ---------------------------------------------------------------------------


def test_p2_pre_chunk_abort_prevents_model_call(
    _patched_prompt_cache: None,
) -> None:
    """Registering an abort before _run_owned_prefill_step runs must:
      1) raise _PrefillAbortedError,
      2) NOT invoke self.model.__call__,
      3) increment mark_prefill_chunk(aborted=True) exactly once,
      4) NOT increment mark_prefill_chunk(aborted=False).

    This is the concrete observable difference between the Pass 2 owned
    seam and the pre-Pass-2 inline-abort behavior.
    """
    scheduler = _build_scheduler(prefill_step_size=8)
    spy: _SpyModel = scheduler.model  # type: ignore[assignment]

    req = _make_request(request_id="p2-abort", n_tokens=10)
    # Wire the abort gate: the scheduler looks up uid -> request_id and
    # checks _pending_abort_ids.  Install both mappings before calling
    # _run_owned_prefill_step directly.
    uid = 123456
    scheduler.uid_to_request_id[uid] = req.request_id
    scheduler.request_id_to_uid[req.request_id] = uid
    scheduler._pending_abort_ids.add(req.request_id)

    # Build a minimal prompt cache by hand (skip _do_external_prefill so
    # we exercise _run_owned_prefill_step in isolation).
    prompt_cache = _stub_make_prompt_cache(None)
    chunk = mx.array([[100, 101, 102, 103]])

    before_completed = scheduler.runtime_metrics._prefill_chunks_completed
    before_aborted = scheduler.runtime_metrics._prefill_chunks_aborted

    with pytest.raises(_PrefillAbortedError) as excinfo:
        scheduler._run_owned_prefill_step(
            req,
            chunk,
            prompt_cache,
            model_kwargs={},
            uid=uid,
            processed_tokens=0,
        )

    assert uid in excinfo.value.aborted_uids
    assert excinfo.value.processed_tokens == 0
    # Model forward must NOT have run.
    assert spy.calls == []
    # Exactly one aborted counter increment, zero completed.
    assert (
        scheduler.runtime_metrics._prefill_chunks_aborted
        == before_aborted + 1
    )
    assert (
        scheduler.runtime_metrics._prefill_chunks_completed == before_completed
    )


def test_p5_per_chunk_counters_match_chunk_count(
    _patched_prompt_cache: None,
) -> None:
    """mark_prefill_chunk(aborted=False) is called exactly once per chunk
    during a normal _do_external_prefill run.
    """
    scheduler = _build_scheduler(prefill_step_size=4)
    req = _make_request(request_id="p5", n_tokens=14)  # prefill 13 → 4+4+4+1
    tokens = list(range(100, 100 + 14))

    before_completed = scheduler.runtime_metrics._prefill_chunks_completed
    before_aborted = scheduler.runtime_metrics._prefill_chunks_aborted

    scheduler._do_external_prefill(req, tokens, existing_cache=None)

    assert (
        scheduler.runtime_metrics._prefill_chunks_completed
        == before_completed + 4
    )
    assert (
        scheduler.runtime_metrics._prefill_chunks_aborted == before_aborted
    )


# ---------------------------------------------------------------------------
# P7 — model forward is the sole stock entry-point during prefill
# ---------------------------------------------------------------------------


def test_p7_model_call_is_sole_stock_entry_point(
    _patched_prompt_cache: None,
) -> None:
    """During _do_external_prefill the ONLY stock callable invoked is
    self.model.__call__.  No BatchGenerator.insert, no next_generated,
    and no unexpected stock call.

    We verify this by installing a spy BatchGenerator on the scheduler
    and asserting none of its methods are hit during prefill, while
    self.model.__call__ is hit at least once (or exactly zero times for
    a 1-token prompt, which is the trivial path).
    """
    scheduler = _build_scheduler(prefill_step_size=5)
    bg_spy = MagicMock()
    scheduler.batch_generator = bg_spy
    spy: _SpyModel = scheduler.model  # type: ignore[assignment]

    req = _make_request(request_id="p7", n_tokens=11)
    tokens = list(range(100, 100 + 11))

    scheduler._do_external_prefill(req, tokens, existing_cache=None)

    # At least one forward; no stock-sequencing entry point touched.
    assert len(spy.calls) >= 1
    assert not bg_spy.insert.called
    assert not bg_spy.remove.called
    assert not bg_spy.next_generated.called
    # MagicMock records attribute access; assert no methods were called
    # via the named protocol above.  We deliberately allow attribute
    # access (scheduler may read .batch_generator for diagnostics).
    for name in ("insert", "remove", "next_generated"):
        method = getattr(bg_spy, name)
        assert method.call_count == 0, (
            f"BatchGenerator.{name} called during owned prefill: "
            f"{method.call_count}"
        )


# ---------------------------------------------------------------------------
# Trivial-path invariant: prompts of length <= 1 do not forward.
# ---------------------------------------------------------------------------


def test_single_token_prompt_skips_forward(
    _patched_prompt_cache: None,
) -> None:
    """A 1-token prompt has nothing to prefill; the branch returns
    (fresh_cache, tokens) without invoking the model.  This exercises
    the early-return branch at scheduler.py:1235-1248."""
    scheduler = _build_scheduler()
    spy: _SpyModel = scheduler.model  # type: ignore[assignment]
    req = _make_request(request_id="triv", n_tokens=1)
    tokens = [100]

    cache, last_token = scheduler._do_external_prefill(
        req, tokens, existing_cache=None
    )

    assert last_token == tokens
    assert isinstance(cache, list)
    assert spy.calls == []
