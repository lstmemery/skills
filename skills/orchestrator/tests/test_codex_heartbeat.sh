#!/bin/bash
# Standalone offline shell harness; works from any current directory.
set -euo pipefail
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 -m unittest discover -s "$HERE" -p test_codex_heartbeat.py -v
