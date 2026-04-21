# SPDX-License-Identifier: Apache-2.0
"""Transfer routes: export, list bundles, inspect, import, bundle pin."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..cache.session_archive import SessionArchiveError
from ..cache.session_archive_portable import BundleError
from . import service
from .schemas import (
    BundleInfo,
    BundlePinRequest,
    ExportRequest,
    ExportResultOut,
    ImportRequest,
    ImportResultOut,
)

router = APIRouter(prefix="/transfers", tags=["ui-transfers"])


def _bundle_error(exc: BundleError) -> HTTPException:
    msg = str(exc)
    status = 400
    low = msg.lower()
    if "already exists" in low:
        status = 409
    if "not found" in low:
        status = 404
    return HTTPException(status_code=status, detail=msg)


@router.post("/export", response_model=ExportResultOut)
def export_workspace(body: ExportRequest) -> dict[str, Any]:
    store = service.get_store()
    try:
        res = service.export_workspace(
            store,
            body.model_name,
            body.session_id,
            out_filename=body.out_filename,
            allow_missing_blocks=body.allow_missing_blocks,
        )
    except SessionArchiveError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except BundleError as exc:
        raise _bundle_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "path": str(res.path),
        "block_count": res.block_count,
        "missing_block_count": res.missing_block_count,
        "grade": res.grade,
    }


@router.get("/bundles", response_model=list[BundleInfo])
def list_bundles() -> list[dict[str, Any]]:
    return service.list_bundles()


@router.post("/inspect", response_model=BundleInfo)
def inspect_bundle_route(body: dict[str, Any]) -> dict[str, Any]:
    name = body.get("bundle_filename") if isinstance(body, dict) else None
    if not isinstance(name, str) or not name:
        raise HTTPException(status_code=400, detail="bundle_filename required")
    try:
        return service.inspect_uploaded_bundle(name)
    except BundleError as exc:
        raise _bundle_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/import", response_model=ImportResultOut)
def import_bundle(body: ImportRequest) -> dict[str, Any]:
    store = service.get_store()
    try:
        res = service.import_uploaded_bundle(
            store,
            body.bundle_filename,
            conflict_policy=body.conflict_policy,
            re_root_lineage=body.re_root_lineage,
            expected_model_name=body.expected_model_name,
            expected_block_size=body.expected_block_size,
        )
    except BundleError as exc:
        raise _bundle_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "model_name": res.model_name,
        "session_id": res.session_id,
        "manifest_path": str(res.manifest_path),
        "blocks_written": int(res.blocks_written),
        "blocks_skipped": int(res.blocks_skipped),
        "source_session_id": res.source_session_id,
        "conflict_policy": res.conflict_policy,
        "re_rooted": bool(res.re_rooted),
        "provenance": dict(res.provenance),
    }


@router.post("/bundles/pin")
def pin_bundle(body: BundlePinRequest) -> dict[str, Any]:
    try:
        state = service.pin_bundle(body.bundle_filename, bool(body.pinned))
    except BundleError as exc:
        raise _bundle_error(exc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"bundle_filename": body.bundle_filename, "pinned": bool(state)}
