# Internal Install Guide

Install OMLX `0.3.6` on a single Apple-Silicon host for internal
use.

Related:

* [`release_internal_v1.md`](./release_internal_v1.md)
* [`operator_quickstart.md`](./operator_quickstart.md)
* [`final_branch_identity.md`](./final_branch_identity.md)

---

## Requirements

* **macOS on Apple Silicon (arm64).** No other platform is
  supported.
* **Python exactly `3.10.12`.** Managed via `pyenv`. The bootstrap
  script will refuse a wrong interpreter.
* **`pyenv`** installed and on `PATH`.
* **`git`** installed. The mlx-lm pin resolves via a git URL.
* Local disk space for `$ARCHIVE` (workspaces + bundles) and
  `$SSD` (paged SSD cache) roots.

## One-command install

From a fresh clone:

```bash
cd omlx-main
./scripts/bootstrap_dev_env.sh
```

The script is idempotent. It will:

1. Verify `pyenv` and the `3.10.12` interpreter (installing the
   Python version via pyenv if missing).
2. Create `.venv` using that interpreter.
3. Install OMLX editable with `pip install -c constraints/dev.txt
   -e .`.
4. Resolve and install the **exact** pinned mlx-lm commit
   (`dcbf6e33d135a1b7c6767ca0fe7ebbd23df814a7`) from the git URL
   in [`pyproject.toml`](../pyproject.toml).
5. Validate the installed mlx-lm commit via
   `direct_url.json` → `vcs_info.commit_id`.

If the commit check fails the script aborts. That is the intended
failure mode — the pin is an API requirement (Gemma 4 tool-call
parser and multi-token think/tool paths), not environmental drift.

## Activate

```bash
cd omlx-main
source .venv/bin/activate
```

## Verify the environment

```bash
python -c "import sys; assert sys.version_info[:3] == (3,10,12), sys.version"
python -c "import omlx; print(omlx.__version__)"   # expect 0.3.6
```

Confirm the mlx-lm pin:

```bash
python - <<'PY'
import json, pathlib
import mlx_lm
dist = pathlib.Path(mlx_lm.__file__).resolve().parents[1]
for p in dist.glob("mlx_lm*.dist-info/direct_url.json"):
    info = json.loads(p.read_text())
    print(info["vcs_info"]["commit_id"])
    break
PY
```

Expected output: `dcbf6e33d135a1b7c6767ca0fe7ebbd23df814a7`.

## Run the focused validation suite

This is the canonical test set that proves the stabilization claims
in this branch. It must be green on any host before the install is
considered complete.

```bash
.venv/bin/python -m pytest \
  tests/test_session_archive_schema_contract.py \
  tests/test_session_archive_failure_modes.py \
  tests/test_session_archive_hardening.py \
  tests/test_session_archive_lineage.py \
  tests/test_session_archive_admin_cli.py \
  tests/test_session_archive_store.py \
  tests/test_workspace_lineage.py \
  tests/test_workspace_demo.py \
  tests/test_paged_ssd_cache.py \
  tests/test_ssd_cache_integrity.py \
  tests/test_scheduler.py \
  tests/test_prefill_seam_pass4.py \
  tests/test_runtime_metrics.py \
  tests/test_non_regression_shared_cache.py \
  tests/test_pruning_policy.py \
  -x
```

Expected: all green. Pass 6 verified 245/245.

There is one **pre-existing unrelated** failure outside the focused
set (`tests/test_admin_api_key.py::TestListModelsSettings::test_list_models_includes_all_model_settings_fields`,
missing `turboquant_skip_last`) that is not caused by this branch
and is not in the canonical command above.

## Uninstall / reset

```bash
cd omlx-main
rm -rf .venv
```

To also clear operator state:

```bash
rm -rf "$ARCHIVE" "$SSD"
```

`$ARCHIVE` and `$SSD` are operator-chosen paths — see
[`operator_quickstart.md`](./operator_quickstart.md).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Bootstrap refuses to start | `pyenv` not on `PATH`, or `3.10.12` not installed | Install `pyenv`, then `pyenv install 3.10.12`, rerun |
| `direct_url.json` commit mismatch | mlx-lm reinstalled without the pin | Re-run `./scripts/bootstrap_dev_env.sh` — it reinstalls with `-c constraints/dev.txt` |
| `ImportError: mlx_lm` | `.venv` not activated | `source .venv/bin/activate` |
| Focused suite red | Wrong Python, wrong mlx-lm commit, or filesystem too small for `$SSD` | Verify `sys.version_info` and `vcs_info.commit_id`; free space on `$SSD` |
| `test_admin_api_key.py` red | Pre-existing unrelated failure | Not in focused set; ignore for this branch |
| Running on Linux / Intel Mac | Platform not supported | Use an Apple-Silicon host |
| Workspace shows `last_used_at` but never `updated_at` moves | Expected: `last_used_at` does not move `updated_at` | See schema §2.7 |

## Cross-references

* Release note: [`release_internal_v1.md`](./release_internal_v1.md)
* Operator workflow: [`operator_quickstart.md`](./operator_quickstart.md)
* Identity + scope: [`final_branch_identity.md`](./final_branch_identity.md)
* Ownership table: [`runtime_ownership_table.md`](./runtime_ownership_table.md)
* Schema: [`workspace_schema.md`](./workspace_schema.md)
* Retention: [`pruning_policy.md`](./pruning_policy.md)
