# SPDX-License-Identifier: Apache-2.0
"""Lineage + diff routes."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from ..cache.session_archive import SessionArchiveError
from . import service
from .schemas import LineageResponse, SessionDiff

router = APIRouter(tags=["ui-lineage"])


@router.get(
    "/workspaces/{model_name}/{session_id}/lineage",
    response_model=LineageResponse,
)
def lineage(model_name: str, session_id: str) -> dict[str, Any]:
    store = service.get_store()
    try:
        return service.build_lineage(store, model_name, session_id)
    except SessionArchiveError as exc:
        msg = str(exc).lower()
        status = 404 if "unknown" in msg else 400
        raise HTTPException(status_code=status, detail=str(exc))


@router.get("/diff", response_model=SessionDiff)
def diff(
    left_model: str = Query(...),
    left_session: str = Query(...),
    right_model: str = Query(...),
    right_session: str = Query(...),
) -> dict[str, Any]:
    store = service.get_store()
    try:
        return service.do_diff(
            store, left_model, left_session, right_model, right_session
        )
    except SessionArchiveError as exc:
        msg = str(exc).lower()
        status = 404 if "unknown" in msg else 400
        raise HTTPException(status_code=status, detail=str(exc))
