#!/usr/bin/env bash
# Install into this extracted bundle; never install OS packages or start the game.
set -euo pipefail

bundle_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python_exe=python3
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf 'Usage: bash install.sh [--python /absolute/path/to/python3]\n'
    exit 0
fi
if [[ $# -gt 0 ]]; then
    if [[ $# -ne 2 || $1 != --python || -z $2 ]]; then
        printf 'Usage: bash install.sh [--python /absolute/path/to/python3]\n' >&2
        exit 2
    fi
    python_exe=$2
fi

"$python_exe" -c 'import sys; raise SystemExit(0 if sys.platform == "linux" and sys.version_info >= (3, 11) else "Linux and Python 3.11 or newer are required")'
if [[ -f "$bundle_root/BUNDLE-MANIFEST.json" ]]; then
    "$python_exe" "$bundle_root/verify_bundle.py"
fi

venv_root="$bundle_root/.venv"
venv_python="$venv_root/bin/python"
if [[ -L "$venv_root" ]]; then
    printf 'The bundle .venv must be a local directory, not a symbolic link. Use a fresh extraction.\n' >&2
    exit 1
fi
if [[ ! -x "$venv_python" ]]; then
    if [[ -e "$venv_root" ]]; then
        printf 'An incomplete .venv exists. Use a fresh extraction or repair it explicitly.\n' >&2
        exit 1
    fi
    "$python_exe" -m venv "$venv_root"
fi
"$venv_python" -c 'import pathlib, sys; raise SystemExit(0 if pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve() and sys.prefix != sys.base_prefix else "The bundle interpreter does not belong to its .venv; use a fresh extraction")' "$venv_root"

"$venv_python" -m pip install --disable-pip-version-check --no-input --constraint "$bundle_root/constraints-tested.txt" "$bundle_root"
"$venv_python" "$bundle_root/check_portable.py"
printf 'Installed. Configure the MCP client with: %s -m pyrogenesis_mcp.server\n' "$venv_python"
