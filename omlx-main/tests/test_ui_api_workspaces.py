# SPDX-License-Identifier: Apache-2.0
"""Tests for omlx.ui_api: workspace + lineage + diff + validate routes."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
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
    # Provide a predictable SSD cache dir under tmp_path.
    monkeypatch.setenv("OMLX_UI_SSD_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "cache").mkdir(exist_ok=True)
    return root


@pytest.fixture()
def seeded(archive_root: Path) -> Path:
    store = SessionArchiveStore(archive_root)
    store.commit("demo-model", "alpha", [_h("a"), _h("b")])
    store.commit("demo-model", "beta", [_h("c")])
    return archive_root


@pytest.fixture()
def client(archive_root: Path) -> TestClient:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(ui_router)
    return TestClient(app)


def test_list_empty_archive_returns_empty(archive_root: Path, client: TestClient) -> None:
    r = client.get("/ui/api/workspaces")
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_list_seeded_archive(seeded: Path, client: TestClient) -> None:
    r = client.get("/ui/api/workspaces")
    assert r.status_code == 200, r.text
    rows = r.json()
    ids = sorted((row["model_name"], row["session_id"]) for row in rows)
    assert ("demo-model", "alpha") in ids
    assert ("demo-model", "beta") in ids


def test_create_workspace_success(archive_root: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/workspaces",
        json={"model_name": "demo", "session_id": "new", "label": "hello"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session_id"] == "new"
    assert body["lineage"]["label"] == "hello"


def test_create_workspace_conflict(seeded: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/workspaces",
        json={"model_name": "demo-model", "session_id": "alpha"},
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_get_workspace_detail(seeded: Path, client: TestClient) -> None:
    r = client.get("/ui/api/workspaces/demo-model/alpha")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_name"] == "demo-model"
    assert body["lineage"]["turn_count"] == 1
    assert len(body["turns"]) == 1
    assert body["turns"][0]["block_count"] == 2
    assert body["pinned"] is False
    assert body["branch_reason"] is None


def test_get_workspace_unknown_404(seeded: Path, client: TestClient) -> None:
    r = client.get("/ui/api/workspaces/demo-model/ghost")
    assert r.status_code == 404


def test_update_metadata_round_trips(seeded: Path, client: TestClient) -> None:
    r = client.put(
        "/ui/api/workspaces/demo-model/alpha/metadata",
        json={"label": "renamed", "description": "d", "task_tag": "t"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lineage"]["label"] == "renamed"
    assert body["lineage"]["description"] == "d"
    assert body["lineage"]["task_tag"] == "t"


def test_pin_and_unpin(seeded: Path, client: TestClient) -> None:
    r = client.post("/ui/api/workspaces/demo-model/alpha/pin", json={"pinned": True})
    assert r.status_code == 200
    assert r.json()["pinned"] is True
    r = client.delete("/ui/api/workspaces/demo-model/alpha/pin")
    assert r.status_code == 200
    assert r.json()["pinned"] is False


def test_fork_requires_branch_reason_min_length(seeded: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/workspaces/demo-model/alpha/fork",
        json={"dst_session_id": "alpha2", "branch_reason": "x"},
    )
    assert r.status_code == 422  # Pydantic validation


def test_fork_succeeds_and_records_branch_reason(seeded: Path, client: TestClient) -> None:
    r = client.post(
        "/ui/api/workspaces/demo-model/alpha/fork",
        json={"dst_session_id": "alpha-fork", "branch_reason": "experiment run 17"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session_id"] == "alpha-fork"
    assert body["branch_reason"] == "experiment run 17"
    # Parent link points at the original.
    assert body["lineage"]["parent"] is not None
    assert body["lineage"]["parent"][0] == "alpha"


def test_lineage_response_shape(seeded: Path, client: TestClient) -> None:
    # Fork to populate descendants.
    client.post(
        "/ui/api/workspaces/demo-model/alpha/fork",
        json={"dst_session_id": "alpha-child", "branch_reason": "child run"},
    )
    r = client.get("/ui/api/workspaces/demo-model/alpha/lineage")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["focus"] == ["demo-model", "alpha"]
    # self + optional ancestors
    roles = [n["role"] for n in body["ancestors"]]
    assert "self" in roles
    # Descendant should be present.
    desc_ids = {n["session_id"] for n in body["descendants"]}
    assert "alpha-child" in desc_ids


def test_diff_shared_prefix(seeded: Path, client: TestClient) -> None:
    r = client.get(
        "/ui/api/diff",
        params={
            "left_model": "demo-model",
            "left_session": "alpha",
            "right_model": "demo-model",
            "right_session": "alpha",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["turn_count_a"] == body["turn_count_b"]
    assert body["shared_turn_count"] == body["turn_count_a"]


def test_validate_without_blocks_missing(seeded: Path, client: TestClient) -> None:
    r = client.post("/ui/api/workspaces/demo-model/alpha/validate")
    assert r.status_code == 200, r.text
    body = r.json()
    # SSD cache is empty so all blocks are missing.
    assert body["replay"]["total_blocks"] >= 1
    assert body["replay"]["present_blocks"] == 0
    assert body["replay"]["replayable"] is False
    assert body["exportable"] is False
