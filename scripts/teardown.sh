#!/usr/bin/env bash
# End of the job: stops the proxy so waiting records are sent, removes the
# redirect and the trusted issuer, and writes a short summary.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

# setup did nothing (or failed open): nothing to undo
[ -f "$STATE_DIR/state.env" ] || exit 0
# shellcheck disable=SC1091
. "$STATE_DIR/state.env"

# the redirect first, so the proxy's last records and the job's own last calls go straight out
# shellcheck disable=SC2153
[ "$MODE" = "firewall" ] && redirect_off
# stopping gives the proxy time to send what is waiting
docker stop -t 20 "$HOTLINE_CONTAINER" >/dev/null 2>&1 || true
exited="$(docker inspect --format '{{.State.ExitCode}}' "$HOTLINE_CONTAINER" 2>/dev/null || echo unknown)"
docker rm -f "$HOTLINE_CONTAINER" >/dev/null 2>&1 || true
untrust_ca

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### Hotline"
    echo
    echo "- Mode: \`$MODE\`, integration \`$INTEGRATION\`"
    if [ "$IMAGE_OVERRIDDEN" = 1 ]; then
      echo "- Proxy image: \`$IMAGE_DIGEST\` (**not pinned**: the \`image\` input was set to \`$IMAGE_REF\`)"
    else
      echo "- Proxy image: \`$IMAGE_DIGEST\` (pinned)"
    fi
    [ -n "$REGION" ] && echo "- Runner region: \`$REGION\`"
    echo "- The proxy stopped with exit code \`$exited\`"
    [ -f "$STATE_DIR/watchdog.txt" ] && echo "- Watchdog: $(cat "$STATE_DIR/watchdog.txt")"
    echo "- The calls themselves are in your OpenTelemetry destination."
  } >> "$GITHUB_STEP_SUMMARY"
fi
rm -rf "$STATE_DIR"
exit 0
