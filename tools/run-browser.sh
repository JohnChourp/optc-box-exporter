#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="$ROOT_DIR/.runtime"
PID_FILE="$RUNTIME_DIR/browser.pid"
LOG_FILE="$RUNTIME_DIR/browser.log"
URL="${OPTCBX_URL:-http://127.0.0.1:1234}"

pick_python() {
    if [ -x /usr/bin/python3 ]; then
        echo /usr/bin/python3
        return
    fi

    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi

    echo "python3 not found" >&2
    exit 1
}

cd "$ROOT_DIR"
mkdir -p "$RUNTIME_DIR"

PYTHON_BIN="${PYTHON_BIN:-$(pick_python)}"
VENV_PATH="${VENV_PATH:-$ROOT_DIR/.venv39}"

if [ ! -x "$VENV_PATH/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$VENV_PATH"
fi

# shellcheck disable=SC1090
. "$VENV_PATH/bin/activate"

python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt

VALID_PORTRAITS="$(
python - <<'PY'
from pathlib import Path

count = 0
for path in Path('data/Portraits').glob('*.png'):
    with open(path, 'rb') as handle:
        if handle.read(8) == b'\x89PNG\r\n\x1a\n':
            count += 1
print(count)
PY
)"

if [ "${VALID_PORTRAITS:-0}" = "0" ]; then
    python -m optcbx download-portraits \
        --units data/units.json \
        --output data/Portraits
fi

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Server already running at $URL"
else
    rm -f "$PID_FILE"
    gunicorn \
        --daemon \
        --bind 127.0.0.1:1234 \
        --chdir "$ROOT_DIR" \
        --pid "$PID_FILE" \
        --access-logfile "$LOG_FILE" \
        --error-logfile "$LOG_FILE" \
        wsgi:application
fi

for _ in $(seq 1 60); do
    if curl -fsS "$URL/runtime-status" >/dev/null 2>&1; then
        if command -v open >/dev/null 2>&1; then
            open "$URL"
        fi
        echo "Browser UI ready at $URL"
        exit 0
    fi
    sleep 1
done

echo "Server did not become ready. Check $LOG_FILE" >&2
exit 1
