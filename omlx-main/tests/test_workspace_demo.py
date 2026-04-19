# SPDX-License-Identifier: Apache-2.0
"""Run the canonical workspace demo script and pin its exit markers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "workspace_demo.sh"


def test_workspace_demo_script_succeeds():
    if not SCRIPT.exists():
        pytest.skip("demo script not present")
    env = os.environ.copy()
    env["PYTHON"] = sys.executable
    res = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"demo script exited {res.returncode}\n"
        f"STDOUT:\n{res.stdout}\n"
        f"STDERR:\n{res.stderr}"
    )
    out = res.stdout
    # Verify every phase fired and the canonical final marker printed.
    assert "== 1. create coding workspace ==" in out
    assert "== 4. fork before risky refactor ==" in out
    assert "common_ancestor\talpha@t-00001" in out
    assert "source_label\tFix parser regression" in out
    assert "task_tag\tcoding.parser" in out
    assert "grade\thealthy" in out
    assert "blocks_written\t2" in out
    assert out.strip().endswith("DEMO OK")
