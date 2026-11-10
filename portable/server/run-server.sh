#!/usr/bin/env bash
# Keep stdout exclusively for MCP. exec preserves signals and the server lifetime.
set -euo pipefail
bundle_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
venv_python="$bundle_root/.venv/bin/python"
if [[ ! -x "$venv_python" ]]; then
    printf 'Run bash install.sh before starting this server.\n' >&2
    exit 1
fi
exec "$venv_python" -m pyrogenesis_mcp.server
