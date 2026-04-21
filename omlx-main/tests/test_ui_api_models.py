# SPDX-License-Identifier: Apache-2.0
"""Tests for the UI model catalog + download routes."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from omlx.ui_api import router as ui_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(ui_router)
    return TestClient(app)


def test_catalog_returns_curated_list() -> None:
    r = _client().get("/ui/api/models/catalog")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "models" in body
    assert len(body["models"]) >= 6
    sample = body["models"][0]
    for key in ("id", "repo_id", "display_name", "family", "size_label", "params"):
        assert key in sample
    # All catalog entries target mlx-community to avoid accidental torch repos.
    assert all(m["repo_id"].startswith("mlx-community/") for m in body["models"])


def test_installed_returns_empty_when_engine_pool_absent() -> None:
    # Without init_server, the engine pool is None and the route degrades
    # gracefully instead of 500-ing.
    r = _client().get("/ui/api/models/installed")
    assert r.status_code == 200, r.text
    assert r.json() == {"models": []}


def test_download_503_when_downloader_uninitialized() -> None:
    r = _client().post(
        "/ui/api/models/download",
        json={"repo_id": "mlx-community/Qwen2.5-0.5B-Instruct-4bit"},
    )
    assert r.status_code == 503, r.text
    assert "not initialized" in r.json()["detail"]


def test_tasks_503_when_downloader_uninitialized() -> None:
    r = _client().get("/ui/api/models/tasks")
    assert r.status_code == 503, r.text


def test_download_with_active_downloader(monkeypatch) -> None:
    """Stub the global server state so the download route succeeds end-to-end."""

    class _StubTask:
        def __init__(self, repo_id: str) -> None:
            self.repo_id = repo_id
            self.task_id = "tid-1"

        def to_dict(self) -> dict:
            return {
                "task_id": self.task_id,
                "repo_id": self.repo_id,
                "status": "pending",
                "progress": 0.0,
            }

    class _StubDownloader:
        def __init__(self) -> None:
            self.started: list[tuple[str, str]] = []

        async def start_download(self, repo_id: str, token: str) -> _StubTask:
            self.started.append((repo_id, token))
            return _StubTask(repo_id)

        def get_tasks(self) -> list[dict]:
            return [t.to_dict() for (_, t) in [(0, _StubTask("x"))]]

    class _StubState:
        def __init__(self) -> None:
            self.hf_downloader = _StubDownloader()
            self.engine_pool = None

    stub = _StubState()
    from omlx import server as srv

    monkeypatch.setattr(srv, "_server_state", stub)

    r = _client().post(
        "/ui/api/models/download",
        json={"repo_id": "mlx-community/Qwen2.5-0.5B-Instruct-4bit"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["task"]["repo_id"] == "mlx-community/Qwen2.5-0.5B-Instruct-4bit"
    assert stub.hf_downloader.started == [
        ("mlx-community/Qwen2.5-0.5B-Instruct-4bit", "")
    ]

    r2 = _client().get("/ui/api/models/tasks")
    assert r2.status_code == 200
    assert "tasks" in r2.json()
