#!/usr/bin/env bash
# Render the production matrix and, when present, the isolated VCACHE experiment.
set -euo pipefail

[ $# -eq 3 ] || { echo "usage: render-pages.sh <production-state> <experiment-state> <site-dir>" >&2; exit 2; }
PRODUCTION_STATE=$1
EXPERIMENT_STATE=$2
SITE_DIR=$3
REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)

mkdir -p "$SITE_DIR"
python3 "$REPO_ROOT/tools/matrix.py" render --state-file "$PRODUCTION_STATE" --out "$SITE_DIR/index.html"
if [ -f "$EXPERIMENT_STATE" ]; then
  python3 "$REPO_ROOT/tools/matrix.py" render \
    --state-file "$EXPERIMENT_STATE" \
    --out "$SITE_DIR/vcache-api-experiment/index.html" \
    --page-context "Upstream VCACHE API experiment"
fi
