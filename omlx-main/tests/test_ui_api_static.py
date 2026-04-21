# SPDX-License-Identifier: Apache-2.0
"""Tests for the SPA-serving handlers in ``omlx.ui_api.static``."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omlx.ui_api import router as ui_router
from omlx.ui_api import static as ui_static


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(ui_router)
    return TestClient(app)


def test_spa_fallback_serves_placeholder_when_dist_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: TestClient
) -> None:
    # Point search paths at empty directories so no bundle is discovered.
    empty_a = tmp_path / "a"
    empty_b = tmp_path / "b"
    empty_a.mkdir()
    empty_b.mkdir()
    monkeypatch.setattr(ui_static, "_SEARCH_PATHS", (empty_a, empty_b))

    r = client.get("/ui/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "omlx operator UI" in r.text
    assert "not built" in r.text


def test_spa_fallback_serves_index_html_when_dist_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: TestClient
) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>spa</title>")
    (dist / "assets" / "app.js").write_text("console.log(1);")
    monkeypatch.setattr(ui_static, "_SEARCH_PATHS", (dist,))

    # Root: index.html.
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "<title>spa</title>" in r.text
    assert r.headers["cache-control"] == "no-store"

    # Deep client-side route: also index.html (SPA fallback).
    r2 = client.get("/ui/w/demo/alpha")
    assert r2.status_code == 200
    assert "<title>spa</title>" in r2.text

    # Asset: served with long-lived cache.
    r3 = client.get("/ui/assets/app.js")
    assert r3.status_code == 200
    assert r3.text == "console.log(1);"
    assert "immutable" in r3.headers["cache-control"]

    # Missing asset: 404.
    r4 = client.get("/ui/assets/missing.js")
    assert r4.status_code == 404


def test_ui_root_redirects_to_trailing_slash(client: TestClient) -> None:
    r = client.get("/ui", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == "/ui/"


def test_api_routes_still_win_match_order(client: TestClient, tmp_path: Path) -> None:
    # Even if the SPA fallback exists, /ui/api/* must reach the API router.
    # The env health route is unconditional and returns plain JSON.
    r = client.get("/ui/api/env")
    assert r.status_code == 200
    body = r.json()
    assert "archive_root" in body or "base_path" in body
