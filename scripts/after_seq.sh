#!/usr/bin/env bash
# Deploy the given agents one by one after the running deploy_seq.sh (its lock makes it wait).
# Usage: bash scripts/after_seq.sh hospital
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/scripts/deploy_seq.sh" "$@"
