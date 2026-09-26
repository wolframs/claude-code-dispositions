#!/usr/bin/env bash
# Compatibility entrypoint: the staged, verified ccctl engine owns all writes.
set -euo pipefail
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$TOOLS/ccctl_compat.py" apply --no-pull "$@"
