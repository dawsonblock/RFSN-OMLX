# SPDX-License-Identifier: Apache-2.0
"""
Request validation contract for session restore.

Pin the invariant that restore intent is *explicit*: a Request may carry
an optional session identity, and if and only if the caller sets the
``restore`` flag must they also provide a ``session_id``. Ordinary
requests with no session fields remain valid and backward-compatible.
"""

from __future__ import annotations

import pytest

from omlx.request import Request, SamplingParams


def _params() -> SamplingParams:
    return SamplingParams(max_tokens=16)


def test_request_accepts_optional_session_id() -> None:
    req = Request(
        request_id="r1",
        prompt="hello",
        sampling_params=_params(),
        session_id="sess-A",
    )
    assert req.session_id == "sess-A"
    assert req.restore is False


def test_request_accepts_explicit_restore_flag() -> None:
    req = Request(
        request_id="r2",
        prompt="hello",
        sampling_params=_params(),
        session_id="sess-A",
        restore=True,
    )
    assert req.session_id == "sess-A"
    assert req.restore is True


def test_restore_flag_without_session_id_is_rejected() -> None:
    with pytest.raises(ValueError) as exc_info:
        Request(
            request_id="r3",
            prompt="hello",
            sampling_params=_params(),
            restore=True,
        )
    # Operator-facing error must name the missing field.
    assert "session_id" in str(exc_info.value), (
        f"ValueError must reference 'session_id': got {exc_info.value!r}"
    )


def test_ordinary_request_without_session_fields_remains_valid() -> None:
    req = Request(
        request_id="r4",
        prompt="hello",
        sampling_params=_params(),
    )
    # Both fields default to the non-session path.
    assert getattr(req, "session_id", None) is None
    assert getattr(req, "restore", False) is False
    # Existing fields are unaffected.
    assert req.num_prompt_tokens == 0
    assert req.output_token_ids == []
