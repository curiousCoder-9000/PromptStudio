#!/usr/bin/env bash
# Run the browser UI suites against a throwaway archive.
#
# Boots a PromptStudio server, launches headless Chrome with a CDP endpoint,
# runs each suite, then tears everything down. Requires Node 22+ (built-in
# WebSocket) and a Chrome/Chromium binary. No npm install needed.
#
# Ports are auto-picked from what is free, so two runs can go at once.
#
#   tests/ui/run.sh                  # every suite
#   tests/ui/run.sh test_escaping.js # one suite
#   TEST_PORT=5099 tests/ui/run.sh   # pin a port (fails if it is taken)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PHOTO_COUNT="${PHOTO_COUNT:-12}"

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

# Ports must be OURS, not just answering.
#
# These used to be hardcoded to 5099/9222. When anything already held them --
# a leftover from a killed run, or a second agent running this same script,
# which happens routinely in this repo -- our server and Chrome failed to bind,
# wait_for's curl was satisfied by the *stranger's* processes, and every suite
# then drove someone else's browser against someone else's archive. That
# reported 20 failures across 4 suites that had nothing wrong with them, and it
# can just as easily report a false pass.
#
# So: an unset port is auto-picked from what is actually free, and an explicitly
# requested one that is taken is a hard error rather than a silent adoption.
free_port() {
  "$PYTHON" - "$1" <<'PY'
import socket, sys
start = int(sys.argv[1])
for port in range(start, start + 400):
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            continue
        print(port)
        break
else:
    sys.exit(f"no free port in {start}..{start + 400}")
PY
}

require_free_port() {
  "$PYTHON" - "$1" "$2" <<'PY'
import socket, sys
port, name = int(sys.argv[1]), sys.argv[2]
with socket.socket() as sock:
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sys.exit(
            f"FATAL: {name}={port} is already in use.\n"
            "  Something else is listening there -- probably a leftover run or a\n"
            "  concurrent session. Free it, or unset the variable to auto-pick."
        )
PY
}

if [ -n "${TEST_PORT:-}" ]; then require_free_port "$TEST_PORT" TEST_PORT || exit 1
else TEST_PORT="$(free_port 5099)" || exit 1; fi
if [ -n "${CDP_PORT:-}" ]; then require_free_port "$CDP_PORT" CDP_PORT || exit 1
else CDP_PORT="$(free_port 9222)" || exit 1; fi

find_chrome() {
  if [ -n "${CHROME_BIN:-}" ]; then echo "$CHROME_BIN"; return; fi
  for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium" \
    "$(command -v google-chrome-stable)" \
    "$(command -v google-chrome)" \
    "$(command -v chromium)" \
    "$(command -v chromium-browser)"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] && echo "$candidate" && return
  done
}

CHROME="$(find_chrome)"
if [ -z "$CHROME" ]; then
  echo "SKIP: no Chrome/Chromium found (set CHROME_BIN to run the UI suites)" >&2
  exit 0
fi

WORKDIR="$(mktemp -d -t promptstudio-ui)"
ARCHIVE="$WORKDIR/archive"
mkdir -p "$ARCHIVE/test_creator"

cleanup() {
  [ -n "${SERVER_PID:-}" ] && kill "$SERVER_PID" 2>/dev/null
  [ -n "${CHROME_PID:-}" ] && kill "$CHROME_PID" 2>/dev/null
  wait 2>/dev/null
  rm -rf "$WORKDIR"
}
trap cleanup EXIT INT TERM

echo "seeding $PHOTO_COUNT photos into $ARCHIVE"
"$PYTHON" - "$ARCHIVE" "$PHOTO_COUNT" <<'PY'
import sys
from PIL import Image
archive, count = sys.argv[1], int(sys.argv[2])
for n in range(1, count + 1):
    Image.new("RGB", (400, 500), (20 * n % 256, 60, 150)).save(
        f"{archive}/test_creator/photo_{n:02d}.jpg", "JPEG"
    )
PY

echo "starting server on :$TEST_PORT"
PROMPTSTUDIO_ARCHIVE="$ARCHIVE" \
PROMPTSTUDIO_PORT="$TEST_PORT" \
IG_AUTO_DRAIN_ON_START=0 \
INSTAGRAM_SESSION_USER="" \
  "$PYTHON" "$REPO_ROOT/server.py" > "$WORKDIR/server.log" 2>&1 &
SERVER_PID=$!

echo "starting headless Chrome (CDP :$CDP_PORT)"
"$CHROME" --headless=new --remote-debugging-port="$CDP_PORT" \
  --user-data-dir="$WORKDIR/chrome" --no-first-run --no-default-browser-check \
  --disable-gpu --no-sandbox about:blank > "$WORKDIR/chrome.log" 2>&1 &
CHROME_PID=$!

# Takes the PID so a dead child fails fast and loudly. The port pre-check above
# cannot close the gap between releasing the probe socket and the real bind, so
# if something wins that race our process exits on EADDRINUSE -- and a curl that
# only asks "is anything answering?" would happily adopt the squatter.
wait_for() {
  local url="$1" name="$2" pid="$3"
  for _ in $(seq 1 80); do
    curl -sf "$url" > /dev/null 2>&1 && return 0
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "FATAL: $name exited before it was ready (port taken?)" >&2
      return 1
    fi
    sleep 0.25
  done
  echo "FATAL: $name never became ready" >&2
  return 1
}
wait_for "http://localhost:$TEST_PORT/api/stats" "server" "$SERVER_PID" \
  || { tail -20 "$WORKDIR/server.log" >&2; exit 1; }
wait_for "http://127.0.0.1:$CDP_PORT/json/version" "chrome" "$CHROME_PID" \
  || { tail -20 "$WORKDIR/chrome.log" >&2; exit 1; }

SUITES=("$@")
if [ ${#SUITES[@]} -eq 0 ]; then
  SUITES=(test_delete_flow.js test_escaping.js test_jobs_and_prefs.js test_classify_review.js
          test_insights_and_pollers.js test_distribution_guard.js test_browse_and_paging.js
          test_source_filter.js test_scrape_lanes.js test_generation_rating.js
          test_outputs_gallery.js test_batch_generate.js)
fi

STATUS=0
for suite in "${SUITES[@]}"; do
  echo ""
  echo "──────── $suite ────────"
  # Each suite assumes a freshly seeded archive, so reset between runs.
  rm -rf "$ARCHIVE"/_trash "$ARCHIVE"/_thumbs
  # Classify verdicts live in SQLite and there is no API to write one without
  # running the vision model, so this suite gets its fixture seeded directly.
  if [ "$suite" = "test_classify_review.js" ] || [ "$suite" = "test_insights_and_pollers.js" ] \
     || [ "$suite" = "test_distribution_guard.js" ]; then
    PROMPTSTUDIO_ARCHIVE="$ARCHIVE" "$PYTHON" "$REPO_ROOT/tests/ui/seed_verdicts.py" "$ARCHIVE" \
      || { echo "FATAL: verdict seeding failed" >&2; STATUS=1; continue; }
  fi
  # Non-Instagram media can only be produced by a real gallery-dl scrape, so
  # the multi-source fixture is written straight into the index.
  if [ "$suite" = "test_source_filter.js" ]; then
    PROMPTSTUDIO_ARCHIVE="$ARCHIVE" "$PYTHON" "$REPO_ROOT/tests/ui/seed_sources.py" "$ARCHIVE" \
      || { echo "FATAL: source seeding failed" >&2; STATUS=1; continue; }
  fi
  APP_URL="http://localhost:$TEST_PORT/" CDP_PORT="$CDP_PORT" \
    node "$REPO_ROOT/tests/ui/$suite" || STATUS=1
done

if [ "$STATUS" -ne 0 ]; then
  echo ""
  echo "──── server log tail ────" >&2
  tail -20 "$WORKDIR/server.log" >&2
fi
exit "$STATUS"
