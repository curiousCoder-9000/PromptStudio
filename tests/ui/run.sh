#!/usr/bin/env bash
# Run the browser UI suites against a throwaway archive.
#
# Boots a PromptStudio server on TEST_PORT, launches headless Chrome with a CDP
# endpoint, runs each suite, then tears everything down. Requires Node 22+
# (built-in WebSocket) and a Chrome/Chromium binary. No npm install needed.
#
#   tests/ui/run.sh                  # both suites
#   tests/ui/run.sh test_escaping.js # one suite
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_PORT="${TEST_PORT:-5099}"
CDP_PORT="${CDP_PORT:-9222}"
PHOTO_COUNT="${PHOTO_COUNT:-12}"

PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

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

wait_for() {
  local url="$1" name="$2"
  for _ in $(seq 1 80); do
    curl -sf "$url" > /dev/null 2>&1 && return 0
    sleep 0.25
  done
  echo "FATAL: $name never became ready" >&2
  return 1
}
wait_for "http://localhost:$TEST_PORT/api/stats" "server" || { tail -20 "$WORKDIR/server.log" >&2; exit 1; }
wait_for "http://127.0.0.1:$CDP_PORT/json/version" "chrome" || { tail -20 "$WORKDIR/chrome.log" >&2; exit 1; }

SUITES=("$@")
if [ ${#SUITES[@]} -eq 0 ]; then
  SUITES=(test_delete_flow.js test_escaping.js test_jobs_and_prefs.js test_classify_review.js
          test_insights_and_pollers.js test_browse_and_paging.js test_source_filter.js
          test_scrape_lanes.js)
fi

STATUS=0
for suite in "${SUITES[@]}"; do
  echo ""
  echo "──────── $suite ────────"
  # Each suite assumes a freshly seeded archive, so reset between runs.
  rm -rf "$ARCHIVE"/_trash "$ARCHIVE"/_thumbs
  # Classify verdicts live in SQLite and there is no API to write one without
  # running the vision model, so this suite gets its fixture seeded directly.
  if [ "$suite" = "test_classify_review.js" ] || [ "$suite" = "test_insights_and_pollers.js" ]; then
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
