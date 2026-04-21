# SPDX-License-Identifier: Apache-2.0
"""Thin local HTTP bridge for the internal operator UI.

This package wraps the existing ``omlx.cache.session_archive*`` pure-Python
operations as a small FastAPI surface mounted under ``/ui/api``. It never
invents new behaviour; every endpoint maps 1:1 to a primitive already
exercised by ``scripts/session_archive_admin.py``.

Trust rules preserved verbatim from the primitives:

* malformed manifests surface as errors, never silent repair;
* conflict policy defaults to ``fail``;
* status/validate/resume go through a read-only SSD probe and NEVER
  instantiate ``PagedSSDCacheManager``;
* ``session_id`` slug collisions and bundle envelope mismatches are
  rejected before any bytes land on disk.
"""

from .routes import router  # noqa: F401

__all__ = ["router"]
