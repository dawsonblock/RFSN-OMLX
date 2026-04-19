#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Canonical workspace-lineage demo.
#
# Walks the full operator loop: create → commit → commit → fork → diff
# → validate → export → import → resume. Uses only the operator CLI and
# a tiny inline Python seeder to append turns (the scheduler hook is
# internal, so for the demo we drive the store directly).
#
# Side effects: creates a temp directory under $TMPDIR and deletes it
# on exit. Safe to run many times. Expected runtime: <5s. Prints
# "DEMO OK" on success and exits 0.

set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-${REPO_ROOT}/.venv/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="$(command -v python3)"
fi
CLI="$REPO_ROOT/scripts/session_archive_admin.py"

WORK="$(mktemp -d -t omlx-workspace-demo.XXXXXXXX)"
trap 'rm -rf "$WORK"' EXIT

ARCHIVE_A="$WORK/archive-a"
ARCHIVE_B="$WORK/archive-b"
SSD="$WORK/ssd"
BUNDLE="$WORK/ws.omlx-session.tar"
MODEL="demo-model"
WS="alpha"
FORK="alpha-fork"

mkdir -p "$ARCHIVE_A" "$ARCHIVE_B" "$SSD"

echo "== 1. create empty workspace =="
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_A" create \
    --model-name "$MODEL" --session-id "$WS" \
    --label "demo" --description "workspace lineage demo" --block-size 16

echo "== 2. seed two turns + two blocks on SSD =="
"$PYTHON" - "$ARCHIVE_A" "$SSD" "$MODEL" "$WS" <<'PY'
import hashlib, sys
from pathlib import Path
from omlx.cache.session_archive import SessionArchiveStore

archive_root, ssd_root, model, session = sys.argv[1:]
archive = Path(archive_root)
ssd = Path(ssd_root)
store = SessionArchiveStore(archive)

def seed(payload_bytes: bytes) -> bytes:
    h = hashlib.sha256(payload_bytes).digest()
    p = ssd / h.hex()[0] / f"{h.hex()}.safetensors"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload_bytes)
    return h

h1 = seed(b"block-one" * 8)
h2 = seed(b"block-two" * 8)
store.commit(model, session, [h1], note="turn 1", block_size=16)
store.commit(model, session, [h1, h2], note="turn 2")
PY

echo "== 3. status (should be healthy, 2 turns) =="
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_A" status \
    --model-name "$MODEL" --session-id "$WS"

echo "== 4. fork at t-00001 =="
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_A" fork \
    --model-name "$MODEL" \
    --src-session-id "$WS" --dst-session-id "$FORK" \
    --at-turn "t-00001" --label "demo-fork"

echo "== 5. diff parent vs fork =="
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_A" diff \
    --model-a "$MODEL" --session-a "$WS" \
    --model-b "$MODEL" --session-b "$FORK"

echo "== 6. validate archive =="
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_A" validate --model-name "$MODEL"

echo "== 7. export workspace =="
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_A" --ssd-cache-dir "$SSD" \
    export-session --model-name "$MODEL" --session-id "$WS" --out "$BUNDLE"

echo "== 8. import into second archive (with block_size guard) =="
SSD_B="$WORK/ssd-b"
mkdir -p "$SSD_B"
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_B" --ssd-cache-dir "$SSD_B" \
    import-session --bundle "$BUNDLE" \
    --expected-model-name "$MODEL" --expected-block-size 16

echo "== 9. resume in second archive =="
"$PYTHON" "$CLI" --archive-root "$ARCHIVE_B" --ssd-cache-dir "$SSD_B" \
    resume --model-name "$MODEL" --session-id "$WS"

echo "DEMO OK"
