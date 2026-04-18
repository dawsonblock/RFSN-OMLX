# SPDX-License-Identifier: Apache-2.0
"""
Lightweight in-process counters for the session archive feature.

Status: **experimental / internal**. These counters exist so operators
can observe session restore / commit activity without standing up a
metrics platform. They are intentionally process-local and stdlib-only
(no Prometheus, no statsd); the ``scripts/session_archive_admin.py``
``stats`` subcommand reads them via :func:`snapshot`.

Event taxonomy (stable string keys — tests match on these exactly):

* ``restore_attempted``              — ``Scheduler.restore_session`` called.
* ``restore_succeeded``              — block table rebuilt.
* ``restore_rejected``               — restore raised; reason is added in
                                       its own counter ``restore_rejected:<reason>``.
* ``manifest_committed``             — ``SessionArchiveStore.commit`` completed.
* ``manifest_commit_failed``         — commit path raised.
* ``session_archive_invalid``        — ``SessionArchiveStore.load`` raised;
                                       reason-tagged via ``session_archive_invalid:<reason>``.
* ``session_archive_missing_blocks`` — restore found referenced blocks
                                       absent from the paged SSD cache.
* ``ssd_block_quarantined``          — paged SSD cache moved a bad file
                                       to ``quarantine/``.
"""

from __future__ import annotations

import threading
from collections import Counter
from typing import Dict, Optional

__all__ = [
    "bump",
    "snapshot",
    "reset",
    "EVENT_RESTORE_ATTEMPTED",
    "EVENT_RESTORE_SUCCEEDED",
    "EVENT_RESTORE_REJECTED",
    "EVENT_MANIFEST_COMMITTED",
    "EVENT_MANIFEST_COMMIT_FAILED",
    "EVENT_SESSION_ARCHIVE_INVALID",
    "EVENT_SESSION_ARCHIVE_MISSING_BLOCKS",
    "EVENT_SSD_BLOCK_QUARANTINED",
]

EVENT_RESTORE_ATTEMPTED = "restore_attempted"
EVENT_RESTORE_SUCCEEDED = "restore_succeeded"
EVENT_RESTORE_REJECTED = "restore_rejected"
EVENT_MANIFEST_COMMITTED = "manifest_committed"
EVENT_MANIFEST_COMMIT_FAILED = "manifest_commit_failed"
EVENT_SESSION_ARCHIVE_INVALID = "session_archive_invalid"
EVENT_SESSION_ARCHIVE_MISSING_BLOCKS = "session_archive_missing_blocks"
EVENT_SSD_BLOCK_QUARANTINED = "ssd_block_quarantined"

_lock = threading.Lock()
_counters: "Counter[str]" = Counter()


def bump(event: str, amount: int = 1, *, reason: Optional[str] = None) -> None:
    """Increment ``event`` (and optionally ``event:reason``) by ``amount``.

    Cheap, thread-safe, never raises. Unknown event names are accepted;
    the canonical set lives at the top of this module.
    """
    if amount <= 0 or not event:
        return
    with _lock:
        _counters[event] += amount
        if reason:
            _counters[f"{event}:{reason}"] += amount


def snapshot() -> Dict[str, int]:
    """Return a copy of the current counter values."""
    with _lock:
        return dict(_counters)


def reset() -> None:
    """Zero out every counter. Intended for tests."""
    with _lock:
        _counters.clear()
