#!/usr/bin/env bash
# Run eval/restore_matrix.py against all four gateway configurations.
#
# The two switches are read by the server at startup, so each configuration
# needs its own gateway process. This starts one, waits for /healthz, measures,
# kills it, and moves on.
#
# Works in Git Bash on Windows and in any POSIX shell.
#
#   export OPENAI_API_KEY=sk-...                     # your key, never stored
#   export PRIVAPARSE_GATEWAY_UPSTREAM=https://api.openai.com
#   bash eval/restore_matrix.sh gpt-5-codex
#
# The key is passed to the gateway's *clients*, not to the gateway: it travels
# as the Authorization header on each request and is forwarded untouched.
set -u

MODEL="${1:-qwen}"
PORT="${PRIVAPARSE_MATRIX_PORT:-8799}"
PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
[ -x "$PYTHON" ] || PYTHON="${PYTHON_FALLBACK:-.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="python"

DB="$(mktemp -u)/matrix.db"
mkdir -p "$(dirname "$DB")"

cleanup() { [ -n "${GW_PID:-}" ] && kill "$GW_PID" 2>/dev/null; }
trap cleanup EXIT

run_one() {
  fuzzy="$1"; hint="$2"
  rm -f "$DB"

  PRIVAPARSE_DETECTOR="${PRIVAPARSE_DETECTOR:-regex}" \
  PRIVAPARSE_DB_PATH="$DB" \
  PRIVAPARSE_LOG_LEVEL=WARNING \
  PRIVAPARSE_GATEWAY_FUZZY="$fuzzy" \
  PRIVAPARSE_GATEWAY_HINT="$hint" \
  "$PYTHON" -m privaparse.app.main serve --port "$PORT" > "/tmp/pp-matrix-$fuzzy-$hint.log" 2>&1 &
  GW_PID=$!

  for _ in $(seq 1 40); do
    curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break
    sleep 1
  done
  if ! curl -sf -m 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    echo "gateway did not start for fuzzy=$fuzzy hint=$hint; see /tmp/pp-matrix-$fuzzy-$hint.log"
    kill "$GW_PID" 2>/dev/null; GW_PID=""
    return 1
  fi

  "$PYTHON" eval/restore_matrix.py \
    --url "http://127.0.0.1:$PORT/v1" --model "$MODEL" \
    --label "fuzzy=$fuzzy hint=$hint" ${VERBOSE:+--verbose}

  kill "$GW_PID" 2>/dev/null; wait "$GW_PID" 2>/dev/null; GW_PID=""
}

echo "model:    $MODEL"
echo "upstream: ${PRIVAPARSE_GATEWAY_UPSTREAM:-https://api.openai.com (default)}"
echo

run_one false false
run_one true  false
run_one false true
run_one true  true
