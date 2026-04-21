# SPDX-License-Identifier: Apache-2.0
"""Environment + health routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from . import service
from .schemas import EnvironmentInfo, HealthCheckResult

router = APIRouter(tags=["ui-env"])


@router.get("/env", response_model=EnvironmentInfo)
def env_info() -> dict[str, Any]:
    return service.environment_info()


@router.post("/env/health", response_model=HealthCheckResult)
def health() -> dict[str, Any]:
    return service.health_check()
