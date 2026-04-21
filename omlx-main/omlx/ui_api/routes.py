# SPDX-License-Identifier: Apache-2.0
"""Aggregate router for the UI bridge, mounted at ``/ui/api``."""

from __future__ import annotations

from fastapi import APIRouter

from .routes_env import router as _env_router
from .routes_lineage import router as _lineage_router
from .routes_maintenance import router as _maintenance_router
from .routes_transfer import router as _transfer_router
from .routes_workspace import router as _workspace_router

router = APIRouter(prefix="/ui/api")
router.include_router(_workspace_router)
router.include_router(_lineage_router)
router.include_router(_transfer_router)
router.include_router(_maintenance_router)
router.include_router(_env_router)
