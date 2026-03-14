#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f .runtime/browser.pid ]]; then
  kill "$(cat .runtime/browser.pid)" 2>/dev/null || true
  rm -f .runtime/browser.pid
fi

pkill -f "python -m optcbx flask" 2>/dev/null || true
pkill -f "optcbx.app_flask" 2>/dev/null || true

PORT_PIDS="$(lsof -ti tcp:1234 2>/dev/null || true)"
if [[ -n "$PORT_PIDS" ]]; then
  kill $PORT_PIDS 2>/dev/null || true
fi

echo "Stopped OPTCbx browser instances on port 1234."
