# SPDX-License-Identifier: Apache-2.0
"""Workspace routes: list, detail, create, fork, pin, metadata, validate."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from ..cache.session_archive import SessionArchiveError
from . import service
from .schemas import (
    CreateWorkspaceRequest,
    ForkWorkspaceRequest,
    PinRequest,
    UpdateMetadataRequest,
    ValidationResult,
    WorkspaceDetail,
    WorkspaceSummary,
)

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/workspaces", tags=["ui-workspaces"])


def _archive_error(exc: SessionArchiveError) -> HTTPException:
    msg = str(exc).lower()
    if "unknown" in msg:
        return HTTPException(status_code=404, detail=str(exc))
    if "already exists" in msg or "compatibility" in msg:
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("", response_model=list[WorkspaceSummary])
def list_workspaces(
    status: Optional[str] = Query(default=None),
    pinned: Optional[bool] = Query(default=None),
    model_family: Optional[str] = Query(default=None),
    exportable: Optional[bool] = Query(default=None),
    probe_exportable: bool = Query(default=False),
) -> list[dict[str, Any]]:
    store = service.get_store()
    probe = service.get_probe()
    rows = service.list_workspaces(
        store,
        probe,
        status_filter=status,
        pinned_filter=pinned,
        model_filter=model_family,
        exportable_filter=exportable,
        include_exportable_probe=probe_exportable or exportable is not None,
    )
    return rows


@router.post("", response_model=WorkspaceDetail, status_code=201)
def create_workspace(body: CreateWorkspaceRequest) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        store.init_workspace(
            body.model_name,
            body.session_id,
            label=body.label,
            description=body.description,
            task_tag=body.task_tag,
            block_size=body.block_size,
        )
    except SessionArchiveError as exc:
        raise _archive_error(exc)
    # Freshly-created workspace has no turns; skip validate probe.
    return service.get_workspace_detail(
        store, probe, body.model_name, body.session_id, validate=False
    )


@router.get("/{model_name}/{session_id}", response_model=WorkspaceDetail)
def get_workspace(
    model_name: str,
    session_id: str,
    validate: bool = Query(default=False),
    include_raw: bool = Query(default=False),
) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        return service.get_workspace_detail(
            store,
            probe,
            model_name,
            session_id,
            validate=validate,
            include_raw=include_raw,
        )
    except SessionArchiveError as exc:
        raise _archive_error(exc)


@router.put("/{model_name}/{session_id}/metadata", response_model=WorkspaceDetail)
def update_metadata(
    model_name: str, session_id: str, body: UpdateMetadataRequest
) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        store.set_label(
            model_name,
            session_id,
            label=body.label,
            description=body.description,
            task_tag=body.task_tag,
        )
        return service.get_workspace_detail(
            store, probe, model_name, session_id, validate=False
        )
    except SessionArchiveError as exc:
        raise _archive_error(exc)


@router.post("/{model_name}/{session_id}/fork", response_model=WorkspaceDetail, status_code=201)
def fork_workspace(
    model_name: str, session_id: str, body: ForkWorkspaceRequest
) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        store.fork(
            model_name,
            session_id,
            body.dst_session_id,
            at_turn=body.at_turn,
            dst_model_name=body.dst_model_name,
            label=body.label,
            description=body.description,
            branch_reason=body.branch_reason,
            task_tag=body.task_tag,
            overwrite=False,
        )
    except SessionArchiveError as exc:
        raise _archive_error(exc)
    dst_model = body.dst_model_name or model_name
    return service.get_workspace_detail(
        store, probe, dst_model, body.dst_session_id, validate=False
    )


@router.post("/{model_name}/{session_id}/pin", response_model=WorkspaceDetail)
def pin_workspace(
    model_name: str, session_id: str, body: PinRequest
) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        store.set_pinned(model_name, session_id, bool(body.pinned))
        return service.get_workspace_detail(
            store, probe, model_name, session_id, validate=False
        )
    except SessionArchiveError as exc:
        raise _archive_error(exc)


@router.delete("/{model_name}/{session_id}/pin", response_model=WorkspaceDetail)
def unpin_workspace(model_name: str, session_id: str) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        store.set_pinned(model_name, session_id, False)
        return service.get_workspace_detail(
            store, probe, model_name, session_id, validate=False
        )
    except SessionArchiveError as exc:
        raise _archive_error(exc)


@router.post("/{model_name}/{session_id}/validate", response_model=ValidationResult)
def validate_workspace(model_name: str, session_id: str) -> dict[str, Any]:
    store = service.get_store()
    probe = service.get_probe()
    try:
        return service.do_validate(store, probe, model_name, session_id)
    except SessionArchiveError as exc:
        raise _archive_error(exc)
