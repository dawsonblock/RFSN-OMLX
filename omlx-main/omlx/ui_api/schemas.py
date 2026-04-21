# SPDX-License-Identifier: Apache-2.0
"""Pydantic DTOs for the UI bridge.

These mirror the dataclasses in ``omlx.cache.session_archive*`` 1:1 so the
bridge never invents shapes. Response models are also the frontend Zod
schemas' source of truth (the frontend keeps matching Zod types by hand —
kept small and deliberately narrow).
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Primitives that map directly to the backend dataclasses.
# ---------------------------------------------------------------------------
class ModelCompat(BaseModel):
    model_name: str
    block_size: Optional[int] = None
    schema_version: str = Field(default="2", alias="schema")

    model_config = {"populate_by_name": True}


class TurnInfo(BaseModel):
    turn_id: str
    committed_at: float
    block_count: int
    note: Optional[str] = None
    branch_reason: Optional[str] = None


class LineageInfo(BaseModel):
    session_id: str
    label: Optional[str] = None
    description: Optional[str] = None
    created_at: float
    updated_at: float
    head_turn_id: str
    parent: Optional[Tuple[str, str]] = None
    model_compat: ModelCompat
    turn_count: int
    task_tag: Optional[str] = None


class ReplayReport(BaseModel):
    session_id: str
    model_name: str
    head_turn_id: str
    total_blocks: int
    present_blocks: int
    missing_blocks: List[str]
    replayable: bool
    grade: str


class TurnDiff(BaseModel):
    turn_id_a: Optional[str]
    turn_id_b: Optional[str]
    block_count_a: int
    block_count_b: int
    common_prefix_blocks: int
    diverged: bool


class SessionDiff(BaseModel):
    session_a: Tuple[str, str]
    session_b: Tuple[str, str]
    common_ancestor: Optional[Tuple[str, str]]
    turn_count_a: int
    turn_count_b: int
    shared_turn_count: int
    per_turn: List[TurnDiff]


# ---------------------------------------------------------------------------
# Composed shapes (bridge-only; no backend counterpart).
# ---------------------------------------------------------------------------
class WorkspaceSummary(BaseModel):
    """One row in ``GET /ui/api/workspaces``."""

    model_name: str
    session_id: str
    label: Optional[str] = None
    head_turn_id: str
    turn_count: int
    updated_at: float
    last_used_at: Optional[float] = None
    pinned: bool = False
    integrity_grade: str
    branch_count: int = 0
    has_parent: bool = False
    exportable: bool = False
    model_compat: ModelCompat
    task_tag: Optional[str] = None


class WorkspaceDetail(BaseModel):
    """Composed detail for ``GET /ui/api/workspaces/{model}/{id}``."""

    model_name: str
    session_id: str
    lineage: LineageInfo
    turns: List[TurnInfo]
    pinned: bool = False
    last_used_at: Optional[float] = None
    integrity_grade: str
    exportable: bool
    replay: Optional[ReplayReport] = None
    branch_reason: Optional[str] = None
    children_count: int = 0
    raw: Optional[Dict[str, Any]] = None


class LineageNode(BaseModel):
    """One node in ``GET /ui/api/workspaces/{model}/{id}/lineage``."""

    model_name: str
    session_id: str
    head_turn_id: str
    label: Optional[str] = None
    integrity_grade: str
    branch_reason: Optional[str] = None
    pinned: bool = False
    parent: Optional[Tuple[str, str]] = None
    depth: int = 0
    role: Literal["self", "ancestor", "descendant", "dangling"] = "self"


class LineageResponse(BaseModel):
    focus: Tuple[str, str]
    ancestors: List[LineageNode]
    descendants: List[LineageNode]
    dangling_parent: Optional[Tuple[str, str]] = None


class ValidationResult(BaseModel):
    """Composed validation for ``POST /ui/api/workspaces/{model}/{id}/validate``."""

    model_name: str
    session_id: str
    integrity_grade: str
    replay: ReplayReport
    manifest_schema_version: str
    schema_ok: bool
    exportable: bool
    reported_at: float


class BundleInfo(BaseModel):
    path: str
    size_bytes: int
    mtime: float
    pinned: bool
    envelope: Optional[Dict[str, Any]] = None
    manifest: Optional[Dict[str, Any]] = None


class PruneCandidate(BaseModel):
    kind: Literal["workspace", "bundle"]
    reason: str
    action: Literal["eligible", "protected"]
    model_name: str
    session_id: str
    path: str
    age_seconds: float
    last_used_at: Optional[float] = None
    integrity_grade: Optional[str] = None
    pinned: bool = False
    prune_class: Optional[str] = None


class PrunePlan(BaseModel):
    model_name: Optional[str] = None
    now: float
    include_pinned: bool
    requested_classes: List[str]
    candidates: List[PruneCandidate]
    by_reason: Dict[str, List[PruneCandidate]]
    plan_signature: str


class PruneExecuteRequest(BaseModel):
    classes: List[str]
    model_name: Optional[str] = None
    include_pinned: bool = False
    now: float
    plan_signature: str
    confirm: bool = False


class PruneReport(BaseModel):
    model_name: Optional[str] = None
    dry_run: bool
    considered: int
    deleted: List[str] = Field(default_factory=list)
    errors: List[Tuple[str, str]] = Field(default_factory=list)


class MaintenanceStats(BaseModel):
    counters: Dict[str, int]
    archive_root: str
    total_workspaces: int
    total_bytes: int
    total_bundles: int


class EnvironmentInfo(BaseModel):
    omlx_version: str
    python_version: str
    platform: Dict[str, str]
    manifest_schema_version: str
    supported_manifest_versions: List[str]
    bundle_version: str
    cache_layout: str
    archive_root: str
    ssd_cache_dir: str
    base_path: str
    bundle_export_dir: str
    bundle_import_dir: str
    mlx_lm_pinned: Optional[str] = None


class HealthCheckResult(BaseModel):
    ok: bool
    checks: Dict[str, Dict[str, Any]]
    reported_at: float


# ---------------------------------------------------------------------------
# Request bodies.
# ---------------------------------------------------------------------------
class CreateWorkspaceRequest(BaseModel):
    model_name: str
    session_id: str
    label: Optional[str] = None
    description: Optional[str] = None
    task_tag: Optional[str] = None
    block_size: Optional[int] = None


class ForkWorkspaceRequest(BaseModel):
    dst_session_id: str
    branch_reason: str = Field(min_length=4, max_length=512)
    at_turn: Optional[str] = None
    dst_model_name: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    task_tag: Optional[str] = None


class UpdateMetadataRequest(BaseModel):
    label: Optional[str] = None
    description: Optional[str] = None
    task_tag: Optional[str] = None


class ExportRequest(BaseModel):
    model_name: str
    session_id: str
    out_filename: Optional[str] = None
    allow_missing_blocks: bool = False


class ImportRequest(BaseModel):
    bundle_filename: str
    conflict_policy: Literal["fail", "rename", "overwrite"] = "fail"
    re_root_lineage: bool = False
    expected_model_name: Optional[str] = None
    expected_block_size: Optional[int] = None


class ExportResultOut(BaseModel):
    path: str
    block_count: int
    missing_block_count: int
    grade: str


class ImportResultOut(BaseModel):
    model_name: str
    session_id: str
    manifest_path: str
    blocks_written: int
    blocks_skipped: int
    source_session_id: str
    conflict_policy: str
    re_rooted: bool
    provenance: Dict[str, Any]


class PruneDryRunRequest(BaseModel):
    classes: List[str]
    model_name: Optional[str] = None
    include_pinned: bool = False


class PinRequest(BaseModel):
    pinned: bool = True


class BundlePinRequest(BaseModel):
    bundle_filename: str
    pinned: bool = True
