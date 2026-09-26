#!/usr/bin/env bash
# Check active Bun sources, anti-markers, and adhoc patches through ccctl.
set -euo pipefail
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$TOOLS/ccctl_compat.py" status "$@"
