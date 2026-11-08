#!/usr/bin/env bash
# Takes the redirect out if the proxy stops during the job, so the job never
# loses its network because of a recording tool. Two failed checks in a row
# count as stopped.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"
[ -f "$STATE_DIR/state.env" ] || exit 0
# shellcheck disable=SC1091
. "$STATE_DIR/state.env"

failures=0
ticks=0
while [ -f "$STATE_DIR/state.env" ]; do
  if curl -fs "http://127.0.0.1:$HOTLINE_ADMIN_PORT/readyz" >/dev/null 2>&1; then
    failures=0
  else
    failures=$((failures + 1))
  fi
  if [ "$failures" -ge 2 ]; then
    redirect_off
    echo "the proxy stopped; the redirect was taken out" > "$STATE_DIR/watchdog.txt"
    exit 0
  fi
  ticks=$((ticks + 1))
  # HOTLINE_CI_WATCHDOG_TICKS bounds the loop for tests; 0 means no limit
  if [ "${HOTLINE_CI_WATCHDOG_TICKS:-0}" -gt 0 ] && [ "$ticks" -ge "${HOTLINE_CI_WATCHDOG_TICKS}" ]; then exit 0; fi
  sleep 3
done
