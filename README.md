<div align="center">

# RFSN-OMLX

**A monorepo for Apple Silicon LLM inference — from a first-principles MLX engine to a full menu-bar serving stack.**

[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](omlx-main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-green.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Apple%20Silicon-black?logo=apple)](https://www.apple.com/mac/)
[![MLX](https://img.shields.io/badge/runtime-MLX-ff6f00.svg)](https://github.com/ml-explore/mlx)

</div>

---

## Overview

`RFSN-OMLX` pairs two complementary projects that both target LLM inference on Apple Silicon via MLX:

| Project | Role | Status |
| --- | --- | --- |
| [**`omlx-main`**](omlx-main) | Production-grade OpenAI-compatible server with continuous batching, tiered KV cache, VLM/OCR/embedding support, and a native macOS menubar app. | Actively released (Homebrew, `.dmg`). |
| [**`rfsn-MLX-main`**](rfsn-MLX-main) | Minimal, first-principles MLX transformer engine used as an inference-systems playground — exact segmented attention, block-managed KV archive, session-scoped restart restore. | Research / reference implementation. |

The two share a common design philosophy: **make KV state explicit, durable, and restartable**, so long-context conversations survive process boundaries without recomputation.

This monorepo is the place where cross-cutting work — session archive contracts, SSD-cache integrity invariants, Apple Silicon benchmarking — is authored and tested against both runtimes.

---

## Repository Layout

```text
RFSN-OMLX/
├── omlx-main/          # Production serving stack (see omlx-main/README.md)
│   ├── omlx/           # Python package: scheduler, cache, API, admin UI
│   ├── tests/          # ~3,700 pytest tests (see "Development" below)
│   ├── packaging/      # venvstacks + py2app packaging for the .dmg
│   ├── Formula/        # Homebrew formula
│   └── pyproject.toml
├── rfsn-MLX-main/      # Minimal MLX inference engine (see its README)
│   ├── rfsn_v10_5/     # Core runtime: layers, cache, launcher
│   └── tests/
└── README.md           # This file
```

---

## Quick Start

### Use `omlx` as a server

The fastest path is the native macOS app or Homebrew:

```bash
brew tap jundot/omlx https://github.com/jundot/omlx
brew install omlx
brew services start omlx
# → OpenAI-compatible API at http://localhost:8000/v1
# → Admin dashboard at   http://localhost:8000/admin
```

Or run it from this checkout:

```bash
cd omlx-main
python3.10 -m venv .venv && source .venv/bin/activate
pip install -e .
omlx serve --model-dir ~/models
```

See [`omlx-main/README.md`](omlx-main/README.md) for the full feature tour (admin dashboard, VLMs, Claude Code integration, per-model settings, benchmarking, menubar app).

### Drive the research engine directly

```bash
cd rfsn-MLX-main
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m rfsn_v10_5.launcher check      # smoke test
python -m rfsn_v10_5.launcher bench ...  # prefill/decode timing
python -m rfsn_v10_5.launcher serve ...  # thin FastAPI wrapper
```

See [`rfsn-MLX-main/README.md`](rfsn-MLX-main/README.md) for architecture diagrams and hardening notes.

---

## What's Inside

### `omlx-main` — the serving stack

- **Continuous batching** via `mlx-lm`'s `BatchGenerator`, with a vLLM-style scheduler (waiting queue, running set, preemption).
- **Tiered KV cache** — hot RAM tier + cold SSD tier stored as `safetensors` blocks. Prefix sharing, Copy-on-Write, and cold restore across restarts.
- **Multi-modal** — text LLMs, vision-language models (VLM), OCR (DeepSeek-OCR, DOTS-OCR, GLM-OCR), embeddings, rerankers, all in one server.
- **OpenAI-compatible** — `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, plus an Anthropic-style adapter.
- **Admin UI** at `/admin` — model management, chat, benchmark, download from HuggingFace, integrations for OpenClaw / OpenCode / Codex / Pi.
- **Native PyObjC menubar app** (not Electron) with auto-update, persistent stats, and process-memory enforcement.

### `rfsn-MLX-main` — the exact runtime

- **Exact segmented attention** over archived KV blocks plus a preallocated hot ring buffer — no lossy reconstruction on the decode path.
- **Block-managed archive** with a residency manager and page table, not a monolithic reconstructed tensor.
- **Corruption-safe persistence** — archived blocks are `.npz` payloads with checksummed manifests; bad blocks fail isolated, never cascade.
- **Session-scoped restoration** — persisted blocks rebuild only for an explicit `(model_identity, session_id)` pair.
- **HuggingFace checkpoint loading**, chunked prefill, weight-tied LM head, grouped-query attention.

---

## Session Archive & SSD Integrity (April 2026)

Recent work on the `session-archive-integrity` branch pins a shared contract across both runtimes: **a session's conversation state is a metadata-only manifest of block hashes, not a duplicated KV payload**, and the SSD cache must survive single-block corruption without poisoning siblings.

Key invariants, codified as tests in [`omlx-main/tests/`](omlx-main/tests):

| Contract | Test file | Enforces |
| --- | --- | --- |
| Request carries explicit session identity + restore intent | `test_request_session_contract.py` | `restore=True` without `session_id` is rejected at construction. |
| Session manifests are metadata-only, atomic, namespaced | `test_session_archive_store.py` | No `.safetensors` under the session root; `(model, session_id)` isolation; stable error substrings (`unknown session`, `malformed manifest`, `empty session archive`, `compatibility mismatch`). |
| Scheduler restore is all-or-nothing | `test_scheduler_session_restore.py` | Any missing block → `SessionArchiveError("gapped ...")`; no partial block tables. |
| Restart continuity | `test_restore_continuity.py` | Restored requests consume restored cached state rather than re-prefilling. |
| SSD-cache corruption isolation | `test_ssd_cache_integrity.py` | One corrupt / truncated / identity-mismatched block never poisons the index; bad blocks are quarantined. |
| No regression on shared prefix cache | `test_non_regression_shared_cache.py` | Non-session traffic keeps its existing shared-prefix reuse. |

Implementation surface (`omlx-main/omlx/`):

- `request.py` — adds `session_id: Optional[str]` + `restore: bool` with `__post_init__` validation.
- `cache/session_archive.py` — `SessionArchiveStore` with atomic (`tempfile.mkstemp` + `os.replace`) JSON manifests, plus `SessionArchiveError` with stable error vocabulary.
- `cache/paged_ssd_cache.py` — `_read_file_metadata` now rejects files whose stored `block_hash` metadata disagrees with the filename-derived hash; `_scan_existing_files` quarantines bad files under `<cache_dir>/quarantine/` instead of crashing the scan.
- `scheduler.py` — narrow `Scheduler.restore_session()` / `Scheduler.commit_session()` hooks. No per-session KV duplication, no deep refactor of the hot path.

---

## Development

### Requirements

- macOS 15.0+ (Sequoia) on Apple Silicon (M1 / M2 / M3 / M4)
- Python **3.10+** (the `mlx-lm` pinned commit requires `mlx >= 0.30.4`, which is not available on 3.9)
- `pyenv` recommended for multi-version workflows

### Set up an `omlx-main` dev environment

```bash
cd omlx-main
pyenv install -s 3.10.12
PYENV_VERSION=3.10.12 python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

> **Tip:** If you rely on a system-wide `pyenv` default of 3.9, every invocation below can be prefixed with `PYENV_VERSION=3.10.12` instead of activating a venv.

### Run the test suite

```bash
# Full collection (should report ~3,700 collected, 0 errors)
python -m pytest --collect-only -q

# Targeted runs — session-archive contract
python -m pytest \
  tests/test_request_session_contract.py \
  tests/test_session_archive_store.py \
  tests/test_ssd_cache_integrity.py -q

python -m pytest \
  tests/test_scheduler_session_restore.py \
  tests/test_restore_continuity.py \
  tests/test_non_regression_shared_cache.py -q
```

### Lint / format

```bash
ruff check omlx/
black --check omlx/
mypy omlx/
```

### Run the research engine's smoke tests

```bash
cd rfsn-MLX-main
python -m rfsn_v10_5.launcher check
pytest tests/
```

---

## Design Notes

### Why two projects?

`rfsn-MLX-main` exists to answer *"how small can an exact, restartable KV archive be?"* — it is a reference implementation unconstrained by backward compatibility. `omlx-main` takes the validated primitives and grows them into a batched, multi-model, production-hosted server. Pinning a contract here (see the Session Archive section) keeps both implementations honest.

### Why MLX?

MLX's unified memory model and lazy evaluation map cleanly onto paged KV caching: there is no host↔device transfer to schedule around, and tensors can be materialized on demand when a block is promoted from SSD. The trade-off is Apple-only; CUDA is out of scope for both projects.

### Why not `llama.cpp` / `vLLM`?

- `llama.cpp` does not expose the block-level KV introspection needed for explicit session archives.
- `vLLM`'s architecture is the template for `omlx-main`'s scheduler, but it targets CUDA/ROCm; MLX requires a native port rather than a backend plug-in.

---

## Contributing

Issues and pull requests are welcome on the upstream projects:

- `omlx-main`: see [`omlx-main/docs/CONTRIBUTING.md`](omlx-main/docs/CONTRIBUTING.md)
- `rfsn-MLX-main`: see [`rfsn-MLX-main/HARDENING_NOTES.md`](rfsn-MLX-main/HARDENING_NOTES.md)

When submitting work that touches the session-archive or SSD-cache contracts, please include a test under `omlx-main/tests/` that pins the invariant — even if the production-code change is trivial. The goal is that behavior is always specified by a test before it ships.

---

## License

- `omlx-main/` — Apache 2.0 (see [`omlx-main/LICENSE`](omlx-main/LICENSE))
- `rfsn-MLX-main/` — see project-level license in that subtree

---

## Acknowledgments

- [Apple MLX](https://github.com/ml-explore/mlx) for the array framework and Metal runtime.
- [`mlx-lm`](https://github.com/ml-explore/mlx-lm) for `BatchGenerator`, sampling utilities, and the LLM model zoo.
- [vLLM](https://github.com/vllm-project/vllm) for the paged KV cache and continuous batching designs that `omlx-main` adapts.
- The broader Apple Silicon LLM community for benchmarks, bug reports, and model conversions.
