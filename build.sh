#!/usr/bin/env bash
set -euo pipefail
atlas_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$atlas_dir"
mkdir -p build
/usr/bin/python3 tools/prepare_semantic.py
if [[ ! -d node_modules ]]; then npm ci --no-audit --no-fund; fi
if [[ ! -x node_modules/electron/dist/electron ]]; then node node_modules/electron/install.js; fi
npm run build
atlas_version=$(/usr/bin/python3 -c 'import json; print(json.load(open("package.json"))["version"])')
"${CXX:-g++}" -std=c++17 -O2 -Wall -Wextra -Wpedantic "-DATLAS_VERSION=\"$atlas_version\"" tools/launcher.cpp -o build/launcher
printf '%s\n' 'Atlas PDF desktop compilado (React + Electron).'
if [[ "${1:-}" == '--package' ]]; then
    /usr/bin/python3 "$atlas_dir/tools/package.py"
fi
