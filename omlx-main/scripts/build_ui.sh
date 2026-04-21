#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build the operator SPA and copy the artifact into the Python package so the
# wheel ships the bundle. Run before `python -m build`.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/.." && pwd)"

cd "$repo/ui"
npm ci
npm run build

dest="$repo/omlx/ui_static"
# Preserve the __init__.py package marker, replace everything else.
find "$dest" -mindepth 1 -not -name '__init__.py' -exec rm -rf {} +
cp -R "$repo/ui/dist/." "$dest/"

echo "UI bundle copied to $dest"
