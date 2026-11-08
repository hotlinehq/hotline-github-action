# Test helpers: fake commands go first on the path and log how they were
# called, so tests run on any machine with no Docker, firewall or network.

REPO="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"

setup_fakes() {
  T="$BATS_TEST_TMPDIR"
  FAKES="$T/bin"
  FIXTURES="$T/fixtures"
  LOG="$T/calls.log"
  mkdir -p "$FAKES" "$FIXTURES" "$T/runner"
  : > "$LOG"
  export FAKE_LOG="$LOG" FAKE_FIXTURES="$FIXTURES" FAKE_BIN="$FAKES"

  export HOTLINE_CI_STATE_DIR="$T/state"
  export GITHUB_ENV="$T/github_env" GITHUB_OUTPUT="$T/github_output" GITHUB_STEP_SUMMARY="$T/summary"
  : > "$GITHUB_ENV"; : > "$GITHUB_OUTPUT"; : > "$GITHUB_STEP_SUMMARY"
  export GITHUB_REPOSITORY="acme/shop" GITHUB_REPOSITORY_OWNER="acme" GITHUB_WORKFLOW="CI build" GITHUB_JOB="lint"
  export GITHUB_RUN_ID="42" GITHUB_RUN_ATTEMPT="1" GITHUB_EVENT_NAME="push" GITHUB_REF_NAME="main"
  export RUNNER_NAME="runner-1" RUNNER_ENVIRONMENT="github-hosted" GITHUB_WORKSPACE="$T/workspace"
  export HOTLINE_IN_EXPORTERS='- otlp: { endpoint: "https://otel.example.com:4318", headers: { Authorization: { env: OTLP_TOKEN } } }'
  export HOTLINE_IN_APIS='{}'
  unset HOTLINE_IN_MODE HOTLINE_IN_IMAGE HOTLINE_IN_REGION HOTLINE_IN_REGION_LOOKUP HOTLINE_IN_LABELS \
    HOTLINE_IN_PASSTHROUGH HOTLINE_IN_ECH_RELAY HOTLINE_IN_TLS_PORTS HOTLINE_IN_HTTP_PORTS \
    HOTLINE_IN_SIGNING_KEY HOTLINE_IN_INTEGRATION HOTLINE_CI_WATCHDOG_TICKS FAKE_FAIL FAKE_UNAME

  # a copy of the repository with a pin file, so tests never touch the real one
  ROOT="$T/root"
  mkdir -p "$ROOT"
  cp -r "$REPO/scripts" "$ROOT/scripts"
  echo "ghcr.io/petercipov/hotline-proxy@sha256:$(printf 'a%.0s' $(seq 64))" > "$ROOT/proxy-image.txt"

  # generic fake: logs its call, fails when FAKE_FAIL names it (name or "name subcommand")
  for cmd in docker iptables sudo update-ca-certificates sleep setsid nohup cosign; do
    cat > "$FAKES/$cmd" <<'FAKE'
#!/usr/bin/env bash
name="$(basename "$0")"
echo "$name $*" >> "$FAKE_LOG"
case "$name" in
  sleep) exit 0 ;;
  setsid | nohup) exit 0 ;;   # the watchdog is not started by tests
  sudo)
    # only these two are passed on, so a test can make them fail; chown,
    # chmod, cp and rm do nothing here
    case "$1" in iptables | update-ca-certificates) exec "$FAKE_BIN/$1" "${@:2}" ;; esac
    exit 0
    ;;
esac
for f in ${FAKE_FAIL:-}; do
  case "$f" in "$name") exit 1 ;; "$name:"*) case "$*" in "${f#*:}"*) exit 1 ;; esac ;; esac
done
if [ "$name" = docker ]; then
  case "$1 $2" in
    "image inspect") echo "ghcr.io/petercipov/hotline-proxy@sha256:$(printf 'b%.0s' $(seq 64))" ;;
    "buildx imagetools") echo "{\"digest\":\"sha256:$(printf 'c%.0s' $(seq 64))\"}" ;;
  esac
  case "$1" in inspect) echo 0 ;; esac
fi
exit 0
FAKE
    chmod +x "$FAKES/$cmd"
  done

  cat > "$FAKES/uname" <<'FAKE'
#!/usr/bin/env bash
echo "${FAKE_UNAME:-Linux}"
FAKE
  cat > "$FAKES/curl" <<'FAKE'
#!/usr/bin/env bash
# answers from a fixture file named after the address, else fails like curl -f
url=""
for a in "$@"; do case "$a" in http://* | https://*) url="$a" ;; esac; done
echo "curl $*" >> "$FAKE_LOG"
file="$FAKE_FIXTURES/$(printf '%s' "$url" | sed 's/[^A-Za-z0-9]/_/g')"
if [ -f "$file" ]; then cat "$file"; exit 0; fi
exit 22
FAKE
  chmod +x "$FAKES/uname" "$FAKES/curl"
  export PATH="$FAKES:$PATH"
  fixture "http://127.0.0.1:18081/readyz" "ok"
}

# fixture ADDRESS TEXT: what the fake curl answers for ADDRESS
fixture() { printf '%s\n' "$2" > "$FIXTURES/$(printf '%s' "$1" | sed 's/[^A-Za-z0-9]/_/g')"; }

run_setup() { run bash "$ROOT/scripts/setup.sh"; }
run_teardown() { run bash "$ROOT/scripts/teardown.sh"; }

calls() { cat "$LOG"; }
config() { jq -c "$1" "$HOTLINE_CI_STATE_DIR/config/hotline-proxy.yaml"; }

# a PATH that lacks one real tool, for the missing tool test
path_without() {
  local dir="$T/limited-$1" t
  mkdir -p "$dir"
  for t in bash env cat sed grep head tr sort uniq seq rm mkdir chmod dirname basename cp wc uname sudo update-ca-certificates sleep setsid nohup jq yq openssl curl docker iptables; do
    [ "$t" = "$1" ] && continue
    command -v "$t" >/dev/null 2>&1 && ln -sf "$(command -v "$t")" "$dir/$t"
  done
  echo "$dir"
}

# drop_ready: the proxy stops answering /readyz
drop_ready() { rm -f "${FIXTURES:?}/http___127_0_0_1_18081_readyz"; }
