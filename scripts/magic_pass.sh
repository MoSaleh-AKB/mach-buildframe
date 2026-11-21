#!/usr/bin/env bash
# Mac wrapper: bash scripts/magic_pass.sh [--brain FILE] [--quick]
set -u
cd "$(dirname "$0")/.."
exec python3 scripts/magic_pass.py "$@"
