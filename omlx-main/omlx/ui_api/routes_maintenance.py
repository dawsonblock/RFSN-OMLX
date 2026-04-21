# SPDX-License-Identifier: Apache-2.0
"""Maintenance routes: prune dry-run / execute + stats."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..cache.session_archive import SessionArchiveError
from . import service
from .schemas import (
    MaintenanceStats,
    PruneDryRunRequest,
    PruneExecuteRequest,
    PrunePlan,
    PruneReport,
)

router = APIRouter(prefix="/maintenance", tags=["ui-maintenance"])


@router.post("/prune/dry-run", response_model=PrunePlan)
def prune_dry_run(body: PruneDryRunRequest) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        return service.prune_dry_run(
            store,
            probe,
            classes=list(body.classes),
            model_name=body.model_name,
            include_pinned=bool(body.include_pinned),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SessionArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/prune/execute", response_model=PruneReport)
def prune_execute(body: PruneExecuteRequest) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        return service.execute_from_request(
            store,
            probe,
            classes=list(body.classes),
            model_name=body.model_name,
            include_pinned=bool(body.include_pinned),
            now=float(body.now),
            expected_signature=body.plan_signature,
            confirm=bool(body.confirm),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except SessionArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/stats", response_model=MaintenanceStats)
def maintenance_stats() -> dict[str, Any]:
    store = service.get_store()
    return service.maintenance_stats(store)
