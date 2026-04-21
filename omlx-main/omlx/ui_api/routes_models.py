# SPDX-License-Identifier: Apache-2.0
"""Model catalog + HuggingFace download routes for the operator UI.

Wraps the existing ``omlx.admin.hf_downloader.HFDownloader`` instance so the
operator SPA can list installed models, browse a curated catalog of
MLX-friendly models, and start/track/cancel downloads. Safe to mount
auth-less because the operator UI is a localhost surface.

Returns 503 when the server has not been initialized via ``init_server``
(e.g. pure-unit TestClient contexts that only exercise the session archive).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/models", tags=["ui-models"])


# ---- Curated catalog -------------------------------------------------------
_CATALOG: list[dict[str, Any]] = [
    {
        "id": "qwen2_5-0_5b-4bit",
        "repo_id": "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
        "display_name": "Qwen2.5 0.5B Instruct (4-bit)",
        "family": "Qwen",
        "size_label": "~400 MB",
        "params": "0.5B",
        "quantization": "4-bit",
        "description": "Tiny, fast smoke-test model. Works on any Apple Silicon Mac.",
        "tags": ["chat", "tiny", "recommended"],
    },
    {
        "id": "qwen2_5-3b-4bit",
        "repo_id": "mlx-community/Qwen2.5-3B-Instruct-4bit",
        "display_name": "Qwen2.5 3B Instruct (4-bit)",
        "family": "Qwen",
        "size_label": "~2 GB",
        "params": "3B",
        "quantization": "4-bit",
        "description": "Balanced quality/latency for everyday chat on 16 GB Macs.",
        "tags": ["chat", "recommended"],
    },
    {
        "id": "qwen2_5-7b-4bit",
        "repo_id": "mlx-community/Qwen2.5-7B-Instruct-4bit",
        "display_name": "Qwen2.5 7B Instruct (4-bit)",
        "family": "Qwen",
        "size_label": "~4.5 GB",
        "params": "7B",
        "quantization": "4-bit",
        "description": "Strong general-purpose model. 24 GB RAM recommended.",
        "tags": ["chat"],
    },
    {
        "id": "llama-3_2-1b-4bit",
        "repo_id": "mlx-community/Llama-3.2-1B-Instruct-4bit",
        "display_name": "Llama 3.2 1B Instruct (4-bit)",
        "family": "Llama",
        "size_label": "~800 MB",
        "params": "1B",
        "quantization": "4-bit",
        "description": "Small Meta model with broad coverage; good fallback.",
        "tags": ["chat", "tiny"],
    },
    {
        "id": "llama-3_2-3b-4bit",
        "repo_id": "mlx-community/Llama-3.2-3B-Instruct-4bit",
        "display_name": "Llama 3.2 3B Instruct (4-bit)",
        "family": "Llama",
        "size_label": "~2 GB",
        "params": "3B",
        "quantization": "4-bit",
        "description": "Mid-tier Meta model, solid for assistants and summarization.",
        "tags": ["chat"],
    },
    {
        "id": "phi-3_5-mini-4bit",
        "repo_id": "mlx-community/Phi-3.5-mini-instruct-4bit",
        "display_name": "Phi-3.5 mini Instruct (4-bit)",
        "family": "Phi",
        "size_label": "~2.2 GB",
        "params": "3.8B",
        "quantization": "4-bit",
        "description": "Microsoft's small-but-capable model; strong at reasoning.",
        "tags": ["chat", "reasoning"],
    },
    {
        "id": "gemma-2-2b-4bit",
        "repo_id": "mlx-community/gemma-2-2b-it-4bit",
        "display_name": "Gemma 2 2B Instruct (4-bit)",
        "family": "Gemma",
        "size_label": "~1.5 GB",
        "params": "2B",
        "quantization": "4-bit",
        "description": "Google's compact model; good multilingual coverage.",
        "tags": ["chat"],
    },
    {
        "id": "mistral-7b-instruct-4bit",
        "repo_id": "mlx-community/Mistral-7B-Instruct-v0.3-4bit",
        "display_name": "Mistral 7B Instruct v0.3 (4-bit)",
        "family": "Mistral",
        "size_label": "~4 GB",
        "params": "7B",
        "quantization": "4-bit",
        "description": "Classic Mistral chat model; reliable tool-use baseline.",
        "tags": ["chat"],
    },
]


class StartDownloadRequest(BaseModel):
    repo_id: str
    hf_token: str = ""


class RetryDownloadRequest(BaseModel):
    hf_token: str = ""


def _get_downloader():
    try:
        from ..server import get_server_state
    except Exception as exc:  # pragma: no cover
        raise HTTPException(
            status_code=503, detail=f"server module unavailable: {exc}"
        )
    state = get_server_state()
    if state.hf_downloader is None:
        raise HTTPException(
            status_code=503,
            detail="HF downloader not initialized (start the server with 'omlx serve').",
        )
    return state.hf_downloader


def _get_engine_pool_or_none():
    try:
        from ..server import get_server_state
    except Exception:
        return None
    return get_server_state().engine_pool


@router.get("/catalog")
def list_catalog() -> dict[str, Any]:
    """Return the curated MLX-friendly model catalog."""
    return {"models": _CATALOG}


@router.get("/installed")
def list_installed() -> dict[str, Any]:
    """Return models already on disk and discoverable by the engine pool."""
    pool = _get_engine_pool_or_none()
    if pool is None:
        return {"models": []}
    try:
        status = pool.get_status()
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail=f"engine pool status: {exc}")
    out: list[dict[str, Any]] = []
    for m in status.get("models", []):
        out.append(
            {
                "id": m.get("id"),
                "model_path": m.get("model_path", ""),
                "loaded": bool(m.get("loaded", False)),
                "is_loading": bool(m.get("is_loading", False)),
                "estimated_size": int(m.get("estimated_size", 0) or 0),
                "model_type": m.get("model_type", "llm"),
                "pinned": bool(m.get("pinned", False)),
            }
        )
    return {"models": out}


@router.get("/tasks")
async def list_tasks() -> dict[str, Any]:
    """List all HuggingFace download tasks."""
    dl = _get_downloader()
    return {"tasks": dl.get_tasks()}


@router.post("/download", status_code=201)
async def start_download(req: StartDownloadRequest) -> dict[str, Any]:
    """Start a new HuggingFace model download."""
    dl = _get_downloader()
    try:
        task = await dl.start_download(req.repo_id, req.hf_token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": task.to_dict()}


@router.post("/cancel/{task_id}")
async def cancel_download(task_id: str) -> dict[str, Any]:
    """Cancel an active download."""
    dl = _get_downloader()
    ok = await dl.cancel_download(task_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail="task not found or not cancellable"
        )
    return {"cancelled": True}


@router.post("/retry/{task_id}")
async def retry_download(
    task_id: str, req: RetryDownloadRequest | None = None
) -> dict[str, Any]:
    """Retry a failed/cancelled download, resuming from existing files."""
    dl = _get_downloader()
    token = req.hf_token if req is not None else ""
    try:
        task = await dl.retry_download(task_id, token)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task": task.to_dict()}


@router.delete("/task/{task_id}")
def remove_task(task_id: str) -> dict[str, Any]:
    """Remove a completed, failed, or cancelled task from the list."""
    dl = _get_downloader()
    ok = dl.remove_task(task_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail="task not found or still active"
        )
    return {"removed": True}
