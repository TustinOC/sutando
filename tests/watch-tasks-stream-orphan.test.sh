#!/bin/bash
# Tests for watch-tasks-stream.sh orphan-path fixes (#1088).
# Validates Mode A (EPIPE exit) and Mode B (fswatch killed on parent exit).
# Uses a mock fswatch so real fswatch installation is not required.
#
# Bash 3.2 note: SIGTERM does not interrupt a blocking `read` from a FIFO
# (the syscall is restarted). To cleanly unblock the watcher for teardown,
# kill the mock fswatch (closing the FIFO write end) before killing the
# watcher itself.

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/src/watch-tasks-stream.sh"
PASS=0
FAIL=0

ok()   { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

make_workspace() {
  local d; d="$(mktemp -d)"
  mkdir -p "$d/tasks" "$d/state"
  echo "$d"
}

# Mock fswatch: writes one dummy event per second so the watcher's read loop
# is not blocked indefinitely. On SIGPIPE (write end closed by killed parent),
# the shell exits naturally — testing Mode B.
make_mock_bin() {
  local d; d="$(mktemp -d)"
  cat > "$d/fswatch" << 'MOCK'
#!/bin/bash
# Emit a dummy non-.txt event periodically (watcher filters it out harmlessly)
while true; do
  printf '/dev/null/dummy\n' || exit 0
  sleep 0.5
done
MOCK
  chmod +x "$d/fswatch"
  echo "$d"
}

# Kill the script cleanly: kill the fswatch child first to unblock the FIFO
# read, then kill the script itself.
kill_script() {
  local pid="$1" ws="$2"
  # Kill the mock fswatch child by FIFO write-end close: send SIGTERM to child
  local child; child="$(pgrep -P "$pid" 2>/dev/null | head -1 || true)"
  [ -n "$child" ] && kill "$child" 2>/dev/null
  sleep 0.3
  # Now the script's read loop should unblock (EOF) and exit; if not, force it
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

# --- Test 1: initial scan emits TASK_FILE lines for pre-existing tasks ---
test_initial_scan() {
  local ws; ws="$(make_workspace)"
  local mock; mock="$(make_mock_bin)"
  touch "$ws/tasks/task-111.txt" "$ws/tasks/task-222.txt"

  local out_file; out_file="$(mktemp)"
  PATH="$mock:$PATH" SUTANDO_WORKSPACE="$ws" bash "$SCRIPT" > "$out_file" 2>/dev/null &
  local pid=$!

  local count=0 attempt=0
  while [ "$count" -lt 2 ] && [ "$attempt" -lt 20 ]; do
    sleep 0.2; count="$(grep -c '^TASK_FILE:' "$out_file" 2>/dev/null || echo 0)"; attempt=$((attempt+1))
  done

  kill_script "$pid" "$ws"
  rm -rf "$ws" "$mock" "$out_file"

  [ "$count" -ge 2 ] \
    && ok "initial scan emits TASK_FILE lines for pre-existing files" \
    || fail "initial scan: got $count TASK_FILE lines, expected >=2"
}

# --- Test 2: FIFO created under state/ ---
test_fifo_created() {
  local ws; ws="$(make_workspace)"
  local mock; mock="$(make_mock_bin)"

  PATH="$mock:$PATH" SUTANDO_WORKSPACE="$ws" bash "$SCRIPT" > /dev/null 2>&1 &
  local pid=$!
  sleep 1.2

  local found=0
  [ -p "$ws/state/watch-tasks-stream.fifo" ] && found=1

  kill_script "$pid" "$ws"
  rm -rf "$ws" "$mock"

  [ "$found" -eq 1 ] \
    && ok "FIFO created at state/watch-tasks-stream.fifo" \
    || fail "FIFO not found at state/watch-tasks-stream.fifo"
}

# --- Test 3: PID file written ---
test_pid_file() {
  local ws; ws="$(make_workspace)"
  local mock; mock="$(make_mock_bin)"

  PATH="$mock:$PATH" SUTANDO_WORKSPACE="$ws" bash "$SCRIPT" > /dev/null 2>&1 &
  local pid=$!
  sleep 1.2

  local found=0
  [ -f "$ws/state/watch-tasks-stream.pid" ] && found=1

  kill_script "$pid" "$ws"
  rm -rf "$ws" "$mock"

  [ "$found" -eq 1 ] \
    && ok "PID file written at state/watch-tasks-stream.pid" \
    || fail "PID file not found"
}

# --- Test 4: Mode B — mock-fswatch dies when parent is SIGKILL'd (SIGPIPE) ---
test_mode_b_child_sigpipe_on_parent_sigkill() {
  local ws; ws="$(make_workspace)"
  local mock; mock="$(make_mock_bin)"

  PATH="$mock:$PATH" SUTANDO_WORKSPACE="$ws" bash "$SCRIPT" > /dev/null 2>&1 &
  local parent_pid=$!
  sleep 1.5

  # Find the mock fswatch child
  local child_pid
  child_pid="$(pgrep -P "$parent_pid" 2>/dev/null | head -1 || true)"

  if [ -z "$child_pid" ]; then
    kill -9 "$parent_pid" 2>/dev/null; wait "$parent_pid" 2>/dev/null || true
    rm -rf "$ws" "$mock"
    fail "Mode B: could not find fswatch child of parent PID $parent_pid"
    return
  fi

  # SIGKILL the parent (simulates session death — no EXIT trap, no signal mask)
  kill -9 "$parent_pid" 2>/dev/null
  wait "$parent_pid" 2>/dev/null || true

  # Mock fswatch should die within 3s: its next printf to the now-closed FIFO
  # read-end returns SIGPIPE, and `|| exit 0` exits it.
  local dead=0 attempt=0
  while [ "$attempt" -lt 15 ]; do
    sleep 0.2
    kill -0 "$child_pid" 2>/dev/null || { dead=1; break; }
    attempt=$((attempt+1))
  done

  kill "$child_pid" 2>/dev/null || true
  rm -rf "$ws" "$mock"

  [ "$dead" -eq 1 ] \
    && ok "Mode B: mock fswatch exits via SIGPIPE within 3s of parent SIGKILL" \
    || fail "Mode B: mock fswatch PID $child_pid still alive 3s after parent SIGKILLed"
}

# --- Test 5: PID file and FIFO cleaned up after graceful exit ---
test_cleanup_on_exit() {
  local ws; ws="$(make_workspace)"
  local mock; mock="$(make_mock_bin)"

  PATH="$mock:$PATH" SUTANDO_WORKSPACE="$ws" bash "$SCRIPT" > /dev/null 2>&1 &
  local pid=$!
  sleep 1.2

  # Clean shutdown: kill fswatch child first (unblocks FIFO read), script exits
  local child; child="$(pgrep -P "$pid" 2>/dev/null | head -1 || true)"
  [ -n "$child" ] && kill "$child" 2>/dev/null
  sleep 0.5
  kill "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
  sleep 0.3

  local pid_exists=0 fifo_exists=0
  [ -f "$ws/state/watch-tasks-stream.pid" ] && pid_exists=1
  [ -e "$ws/state/watch-tasks-stream.fifo" ] && fifo_exists=1

  rm -rf "$ws" "$mock"
  [ "$pid_exists" -eq 0 ] && [ "$fifo_exists" -eq 0 ] \
    && ok "cleanup: PID file and FIFO removed on exit" \
    || fail "cleanup: pid_exists=$pid_exists fifo_exists=$fifo_exists (should both be 0)"
}

# --- Test 6: source uses printf not echo for all TASK_FILE emits (Mode A) ---
test_source_uses_printf() {
  local echo_task_lines
  echo_task_lines="$(grep 'TASK_FILE:' "$SCRIPT" | grep -v printf | grep -v '^\s*#' || true)"
  [ -z "$echo_task_lines" ] \
    && ok "source: all TASK_FILE emits use printf (Mode A guard)" \
    || fail "source: found TASK_FILE emit using echo instead of printf: $echo_task_lines"
}

echo "=== watch-tasks-stream orphan-path tests ==="
test_initial_scan
test_fifo_created
test_pid_file
test_mode_b_child_sigpipe_on_parent_sigkill
test_cleanup_on_exit
test_source_uses_printf
echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
