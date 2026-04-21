# SPDX-License-Identifier: Apache-2.0
"""Tests for omlx.ui_api maintenance + env/health + transfers routes."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omlx.cache.session_archive import SessionArchiveStore
from omlx.ui_api import router as ui_router


def _h(tag: str) -> bytes:
    return hashlib.sha256(tag.encode()).digest()


@pytest.fixture()
def archive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setenv("OMLX_UI_ARCHIVE_ROOT", str(root))
    monkeypatch.setenv("OMLX_UI_BASE_PATH", str(tmp_path))
    monkeypatch.setenv("OMLX_UI_SSD_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir(exist_ok=True)
    return root


@pytest.fixture()
def seeded(archive_root: Path) -> Path:
    store = SessionArchiveStore(archive_root)
    store.commit("demo-model", "alpha", [_h("a")])
    return archive_root


@pytest.fixture()
def client(archive_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(ui_router)
    return TestClient(app)


def test_env_info_exposes_paths(archive_root: Path, client: TestClient) -> None:
    r = client.get("/ui/api/env")
    assert r.status_code == 200, r.text
    body = r.json()
    assert Path(body["archive_root"]) == archive_root
    assert "omlx_version" in body
    assert body["manifest_schema_version"] in body["supported_manifest_versions"]


def test_health_check_passes_when_archive_writable(archive_root: Path, client: TestClient) -> None:
    r = client.post("/ui/api/env/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["checks"]["archive_root_writable"]["ok"] is True


def test_maintenance_stats_empty(archive_root: Path, client: TestClient) -> None:
    r = client.get("/ui/api/maintenance/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_workspaces"] == 0
    assert body["total_bundles"] == 0


def test_maintenance_stats_seeded(seeded: Path, client: TestClient) -> None:
    r = client.get("/ui/api/maintenance/stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_workspaces"] == 1


def test_prune_dry_run_unknown_class_rejected(archive_root: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/maintenance/prune/dry-run",
        json={"classes": ["bogus"]},
    )
    assert r.status_code == 400
    assert "unknown prune class" in r.json()["detail"]


def test_prune_dry_run_empty_plan_on_empty_archive(archive_root: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/maintenance/prune/dry-run",
        json={"classes": ["stale"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["candidates"] == []
    assert body["plan_signature"]


def test_prune_execute_signature_drift_rejected(seeded: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/maintenance/prune/dry-run",
        json={"classes": ["stale"]},
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    r = client.post(
        "/ui/api/maintenance/prune/execute",
        json={
            "classes": plan["requested_classes"],
            "model_name": plan["model_name"],
            "include_pinned": plan["include_pinned"],
            "now": plan["now"],
            "plan_signature": "deadbeef" * 8,  # wrong signature
            "confirm": True,
        },
    )
    assert r.status_code == 409
    assert "signature drift" in r.json()["detail"]


def test_prune_execute_confirms_matching_signature(seeded: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/maintenance/prune/dry-run",
        json={"classes": ["stale"]},
    )
    plan = r.json()
    r = client.post(
        "/ui/api/maintenance/prune/execute",
        json={
            "classes": plan["requested_classes"],
            "model_name": plan["model_name"],
            "include_pinned": plan["include_pinned"],
            "now": plan["now"],
            "plan_signature": plan["plan_signature"],
            "confirm": True,
        },
    )
    # Signature matches; execute returns a report (may be empty since no missing-block data).
    assert r.status_code == 200, r.text


def test_list_bundles_empty(archive_root: Path, client: TestClient) -> None:
    r = client.get("/ui/api/transfers/bundles")
    assert r.status_code == 200
    assert r.json() == []


def test_export_requires_valid_workspace(archive_root: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/transfers/export",
        json={"model_name": "nope", "session_id": "nope"},
    )
    assert r.status_code == 400


def test_inspect_rejects_missing_bundle(archive_root: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/transfers/inspect",
        json={"bundle_filename": "does-not-exist.tar"},
    )
    # Not found → BundleError mapped to 404.
    assert r.status_code in (400, 404)


def test_inspect_rejects_unsafe_filename(archive_root: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/transfers/inspect",
        json={"bundle_filename": "../etc/passwd"},
    )
    assert r.status_code == 400
    assert "bundle filename" in r.json()["detail"]
