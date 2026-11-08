#!/usr/bin/env bats
load helpers
setup() { setup_fakes; }

@test "does nothing, quietly, when setup did nothing" {
  run_teardown
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ ! -s "$LOG" ]
}

@test "stops the proxy so waiting records are sent, after taking the redirect out" {
  run_setup; : > "$LOG"
  run_teardown
  [ "$status" -eq 0 ]
  grep -q "iptables -t nat -D OUTPUT" "$LOG"
  grep -q "^docker stop -t 20 hotline-ci-proxy" "$LOG"
  [ "$(grep -n 'iptables -t nat -D OUTPUT' "$LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n '^docker stop' "$LOG" | head -1 | cut -d: -f1)" ]
  [ ! -e "$HOTLINE_CI_STATE_DIR" ]
}

@test "still removes the rule and the trusted issuer when the proxy was already gone" {
  run_setup; : > "$LOG"
  FAKE_FAIL="docker" run_teardown
  [ "$status" -eq 0 ]
  grep -q "iptables -t nat -F HOTLINE_CI" "$LOG"
  grep -q "^sudo rm -f /usr/local/share/ca-certificates/hotline-ci.crt" "$LOG"
}

@test "in env mode there is no rule to remove" {
  HOTLINE_IN_MODE=env run_setup; : > "$LOG"
  run_teardown
  ! grep -q iptables "$LOG"
  grep -q "^docker stop" "$LOG"
}

@test "writes a summary: mode, image and region, with a clear note when the image is not pinned" {
  HOTLINE_IN_IMAGE=edge HOTLINE_IN_REGION=eu-west-1 run_setup
  run_teardown
  summary="$(cat "$GITHUB_STEP_SUMMARY")"
  [[ "$summary" == *"### Hotline"* ]]
  [[ "$summary" == *"sha256:bbbb"* ]]
  [[ "$summary" == *"not pinned"* ]]
  [[ "$summary" == *"eu-west-1"* ]]
}

@test "the summary says pinned when the pin file was used" {
  run_setup
  run_teardown
  [[ "$(cat "$GITHUB_STEP_SUMMARY")" == *"(pinned)"* ]]
}

@test "the watchdog removes the redirect when the proxy stops, after two failed checks" {
  run_setup; : > "$LOG"
  drop_ready
  HOTLINE_CI_WATCHDOG_TICKS=5 run bash "$ROOT/scripts/watchdog.sh"
  [ "$status" -eq 0 ]
  grep -q "iptables -t nat -D OUTPUT" "$LOG"
  grep -q "proxy stopped" "$HOTLINE_CI_STATE_DIR/watchdog.txt"
}

@test "the watchdog leaves the redirect alone while the proxy answers" {
  run_setup; : > "$LOG"
  HOTLINE_CI_WATCHDOG_TICKS=4 run bash "$ROOT/scripts/watchdog.sh"
  ! grep -q "iptables" "$LOG"
}

@test "one failed check is not enough for the watchdog" {
  run_setup; : > "$LOG"
  drop_ready
  HOTLINE_CI_WATCHDOG_TICKS=1 run bash "$ROOT/scripts/watchdog.sh"
  ! grep -q "iptables" "$LOG"
}
