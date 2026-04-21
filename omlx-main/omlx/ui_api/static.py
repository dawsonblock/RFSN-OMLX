# SPDX-License-Identifier: Apache-2.0
"""Serve the operator SPA bundle from the FastAPI process.

Mounts two route families under the existing ``/ui`` namespace:

* ``GET /ui/assets/{path:path}`` — immutable hashed assets from the Vite build.
* ``GET /ui/{full_path:path}`` — SPA fallback, always returning ``index.html``
  so client-side routing (``react-router``) resolves on hard reloads.

The handler looks for the bundle in two locations, in order:

1. ``omlx/ui_static/`` — the wheel-shipped copy, populated by
   ``scripts/build_ui.sh`` during release builds.
2. ``ui/dist/`` relative to the repo root — the dev tree output of
   ``npm --prefix ui run build``.

When neither is present the ``/ui/`` root returns a friendly HTML
placeholder explaining how to build the SPA, and asset routes return 404.
This keeps ``omlx serve`` usable in source checkouts that have not yet
run the frontend build.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

router = APIRouter()

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # omlx/
_REPO_ROOT = _PACKAGE_ROOT.parent  # omlx-main/

# Preferred (wheel-shipped) location, then dev source tree.
_SEARCH_PATHS = (
    _PACKAGE_ROOT / "ui_static",
    _REPO_ROOT / "ui" / "dist",
)

_PLACEHOLDER_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>omlx operator UI</title>
<style>body{font-family:ui-sans-serif,system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#111}code{background:#f4f4f5;padding:.15rem .35rem;border-radius:.25rem}pre{background:#f4f4f5;padding:.75rem;border-radius:.35rem;overflow:auto}</style>
</head><body>
<h1>omlx operator UI &mdash; not built</h1>
<p>The SPA bundle was not found in <code>omlx/ui_static/</code> or <code>ui/dist/</code>.</p>
<p>From a source checkout:</p>
<pre><code>cd ui
npm ci
npm run build</code></pre>
<p>For a release wheel, run <code>scripts/build_ui.sh</code> before packaging.</p>
<p>The API surface at <code>/ui/api/&hellip;</code> is unaffected and still served.</p>
</body></html>
"""


def _find_dist() -> Path | None:
    """Return the first existing dist directory that contains ``index.html``."""
    for candidate in _SEARCH_PATHS:
        if (candidate / "index.html").is_file():
            return candidate
    return None


@router.get("/ui", include_in_schema=False)
def _ui_root_redirect() -> RedirectResponse:
    """Redirect bare ``/ui`` to ``/ui/`` so relative asset URLs resolve."""
    return RedirectResponse(url="/ui/", status_code=308)


@router.get("/ui/assets/{path:path}", include_in_schema=False)
def ui_asset(path: str) -> FileResponse:
    """Serve a hashed asset from the built SPA bundle."""
    dist = _find_dist()
    if dist is None:
        raise HTTPException(status_code=404, detail="UI bundle not built")
    target = (dist / "assets" / path).resolve()
    assets_root = (dist / "assets").resolve()
    try:
        target.relative_to(assets_root)
    except ValueError as exc:  # path traversal
        raise HTTPException(status_code=404, detail="not found") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(
        target,
        headers={"cache-control": "public, max-age=604800, immutable"},
    )


@router.get("/ui/{full_path:path}", include_in_schema=False)
def ui_spa_fallback(full_path: str) -> Response:
    """Serve ``index.html`` for any non-asset path so the SPA can route it."""
    # Defense in depth: the ``/ui/api`` router is registered earlier and wins
    # match order, but reject anything starting with ``api/`` just in case.
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="not found")
    dist = _find_dist()
    if dist is None:
        # Both root and unknown paths render the placeholder so operators
        # see actionable guidance rather than a silent 404.
        return HTMLResponse(_PLACEHOLDER_HTML, status_code=200)
    return FileResponse(
        dist / "index.html",
        media_type="text/html",
        headers={"cache-control": "no-store"},
    )
