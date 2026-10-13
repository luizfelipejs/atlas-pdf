#!/usr/bin/env bash
set -euo pipefail
atlas_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export ATLAS_RESOURCE_DIR="$atlas_dir"
export ATLAS_PYTHON=${ATLAS_PYTHON:-/usr/bin/python3}
if [[ "${1:-}" == '--backend' ]]; then
    shift
    exec "$ATLAS_PYTHON" "$atlas_dir/backend/indexer.py" "$@"
fi
if [[ ! -f "$atlas_dir/build/web/index.html" || ! -x "$atlas_dir/node_modules/electron/dist/electron" ]]; then bash "$atlas_dir/build.sh"; fi
exec node "$atlas_dir/tools/electron-runtime.cjs" "$@"
