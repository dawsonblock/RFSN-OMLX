#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Reproducible dev-environment bootstrap for oMLX.
#
# Status: experimental / internal. This script is idempotent: it verifies
# pyenv + Python 3.10.12, creates/reuses a .venv, installs the project
# with the constraints/dev.txt pin set, and fails loudly if the installed
# mlx-lm does not match the required git commit.
#
# This exists so future contributors do not rediscover the
# GenerationBatch API drift between mlx-lm 0.31.1 (tagged) and the
# post-tag commit we actually depend on.
#
# Usage:
#   bash scripts/bootstrap_dev_env.sh

set -euo pipefail

REQUIRED_MLX_LM_COMMIT="dcbf6e33d135a1b7c6767ca0fe7ebbd23df814a7"
REQUIRED_MLX_LM_SHORT="${REQUIRED_MLX_LM_COMMIT:0:7}"
PYTHON_VERSION="3.10.12"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

log() { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[bootstrap]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Locate a Python 3.10.12 interpreter.
# ---------------------------------------------------------------------------
python_bin=""
if command -v pyenv >/dev/null 2>&1; then
    if ! pyenv versions --bare | grep -qx "$PYTHON_VERSION"; then
        log "installing Python $PYTHON_VERSION via pyenv..."
        pyenv install -s "$PYTHON_VERSION"
    fi
    python_bin="$(PYENV_VERSION=$PYTHON_VERSION pyenv which python)"
else
    for candidate in python3.10 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            ver="$("$candidate" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
            if [[ "$ver" == "$PYTHON_VERSION" ]]; then
                python_bin="$(command -v "$candidate")"
                break
            fi
        fi
    done
fi

if [[ -z "$python_bin" ]]; then
    die "Python $PYTHON_VERSION not found. Install pyenv and run 'pyenv install $PYTHON_VERSION'."
fi
log "using python: $python_bin"

# ---------------------------------------------------------------------------
# 2. Create / reuse .venv at repo root.
# ---------------------------------------------------------------------------
venv_dir="$repo_root/.venv"
if [[ ! -x "$venv_dir/bin/python" ]]; then
    log "creating .venv at $venv_dir"
    "$python_bin" -m venv "$venv_dir"
fi

venv_python="$venv_dir/bin/python"
venv_pip="$venv_dir/bin/pip"

log "upgrading pip"
"$venv_pip" install --upgrade --quiet pip

# ---------------------------------------------------------------------------
# 3. Install project with pinned constraints.
# ---------------------------------------------------------------------------
log "installing project + constraints/dev.txt"
"$venv_pip" install --quiet -c "$repo_root/constraints/dev.txt" -e "$repo_root"

# ---------------------------------------------------------------------------
# 4. Verify the mlx-lm commit is exactly the one we expect.
# ---------------------------------------------------------------------------
installed_commit="$("$venv_python" - <<'PY'
import importlib.metadata as md
try:
    dist = md.distribution("mlx-lm")
except md.PackageNotFoundError:
    print("MISSING")
    raise SystemExit(1)

direct_url = None
for f in (dist.files or []):
    if f.name == "direct_url.json":
        try:
            import json
            direct_url = json.loads(f.locate().read_text())
        except Exception:
            direct_url = None
        break

if direct_url and "vcs_info" in direct_url:
    print(direct_url["vcs_info"].get("commit_id", ""))
else:
    print("")
PY
)"

if [[ "$installed_commit" != "$REQUIRED_MLX_LM_COMMIT" ]]; then
    die "mlx-lm commit mismatch: installed=$installed_commit required=$REQUIRED_MLX_LM_COMMIT ($REQUIRED_MLX_LM_SHORT). Re-run with a clean .venv."
fi

log "mlx-lm commit verified: $REQUIRED_MLX_LM_SHORT"
log "dev environment ready. activate with: source $venv_dir/bin/activate"
