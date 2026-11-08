# shellcheck shell=bash disable=SC2034
# Shared helpers for setup.sh, teardown.sh, watchdog.sh and pin-image.sh.
# Sourced, never run. Everything here uses only tools on GitHub's Ubuntu runners.

HOTLINE_IMAGE_REPO="ghcr.io/petercipov/hotline-proxy"
HOTLINE_CONTAINER="hotline-ci-proxy"
HOTLINE_UID=64321            # the proxy's own numeric user; the firewall leaves it out
HOTLINE_PROXY_PORT=18080
HOTLINE_ADMIN_PORT=18081
HOTLINE_INTERCEPT_PORT=18444
HOTLINE_CA_FILE="/usr/local/share/ca-certificates/hotline-ci.crt"

STATE_DIR="${HOTLINE_CI_STATE_DIR:-${RUNNER_TEMP:-/tmp}/hotline-ci}"

# Warnings show on the job's page and never fail the job.
warn() { echo "::warning title=Hotline::$*"; }
note() { echo "hotline: $*"; }

# One undo list. Each step that changes the machine adds its own undo, and
# run_undo runs them newest first, so any failure leaves the job as it was.
UNDO=()
push_undo() { UNDO+=("$*"); }
run_undo() {
  local i
  for ((i = ${#UNDO[@]} - 1; i >= 0; i--)); do eval "${UNDO[$i]}" || true; done
  UNDO=()
}

# Reads YAML on standard input and writes JSON. Runners have mikefarah's yq;
# the other yq (a wrapper of jq) already writes JSON.
yaml_to_json() {
  if yq --version 2>&1 | grep -qi mikefarah; then yq -o=json '.'; else yq '.'; fi
}

# yaml_arg TEXT DEFAULT: TEXT (YAML) as one line of JSON; DEFAULT when blank.
yaml_arg() {
  if [ -z "${1//[[:space:]]/}" ]; then printf '%s' "$2"; else printf '%s' "$1" | yaml_to_json | jq -c '.'; fi
}

fetch() { curl -fsS -m "${HOTLINE_CI_CURL_TIMEOUT:-3}" "$@"; }

# The proxy's image: the pin file, unless `image` says otherwise.
resolve_image() {
  local override="${1:-}" root="$2" line
  if [ -n "$override" ]; then
    [ "$override" = "edge" ] && override="$HOTLINE_IMAGE_REPO:edge"
    IMAGE_OVERRIDDEN=1
    IMAGE_REF="$override"
    return 0
  fi
  IMAGE_OVERRIDDEN=0
  line="$(grep -v '^[[:space:]]*#' "$root/proxy-image.txt" 2>/dev/null | grep -v '^[[:space:]]*$' | head -1 || true)"
  line="${line%%#*}"
  line="${line//[[:space:]]/}"
  [ -n "$line" ] || return 1
  IMAGE_REF="$line"
}

# region_lookup sets REGION and PUBLIC_IP (either may stay empty); never fails.
region_lookup() {
  local mode="$1" lookup="$2" env_kind="${3:-}" out token zone
  REGION=""
  PUBLIC_IP=""
  [ "$mode" = "off" ] && return 0
  if [ "$mode" != "auto" ]; then REGION="$mode"; fi
  if [ -z "$REGION" ]; then
    # 1. the cloud's own metadata address: Azure, AWS, then Google Cloud
    if out="$(fetch -H 'Metadata:true' 'http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text' 2>/dev/null)" && [ -n "$out" ]; then
      REGION="$out"
    elif token="$(fetch -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 'http://169.254.169.254/latest/api/token' 2>/dev/null)" &&
      out="$(fetch -H "X-aws-ec2-metadata-token: $token" 'http://169.254.169.254/latest/meta-data/placement/region' 2>/dev/null)" && [ -n "$out" ]; then
      REGION="$out"
    elif zone="$(fetch -H 'Metadata-Flavor: Google' 'http://metadata.google.internal/computeMetadata/v1/instance/zone' 2>/dev/null)" && [ -n "$zone" ]; then
      zone="${zone##*/}"
      REGION="${zone%-*}"
    fi
  fi
  # 2. the public address and the nearest Cloudflare site: GitHub's own
  # runners only; a self-hosted runner never sends its address to a third
  # party unless `region-lookup: public-ip` says so.
  if [ "$env_kind" = "github-hosted" ] || [ "$lookup" = "public-ip" ]; then
    if out="$(fetch 'https://1.1.1.1/cdn-cgi/trace' 2>/dev/null)"; then
      PUBLIC_IP="$(printf '%s\n' "$out" | sed -n 's/^ip=//p' | head -1)"
      if [ -z "$REGION" ] && [ "$mode" = "auto" ]; then
        REGION="$(printf '%s\n' "$out" | sed -n 's/^colo=//p' | head -1)"
      fi
    fi
  fi
  return 0
}

# The redirect. In firewall mode every outgoing TCP connection to a public
# address on a chosen port goes to the proxy, except the proxy's own.
redirect_on() {
  local port
  sudo iptables -t nat -N HOTLINE_CI
  sudo iptables -t nat -A HOTLINE_CI -m owner --uid-owner "$HOTLINE_UID" -j RETURN
  for range in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 \
    192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/3; do
    sudo iptables -t nat -A HOTLINE_CI -d "$range" -j RETURN
  done
  for port in $(printf '%s' "$TLS_PORTS" | tr ',' ' '); do
    sudo iptables -t nat -A HOTLINE_CI -p tcp --dport "$port" -j REDIRECT --to-ports "$HOTLINE_INTERCEPT_PORT"
  done
  for port in $(printf '%s' "$HTTP_PORTS" | tr ',' ' '); do
    sudo iptables -t nat -A HOTLINE_CI -p tcp --dport "$port" -j REDIRECT --to-ports "$HOTLINE_PROXY_PORT"
  done
  sudo iptables -t nat -A OUTPUT -p tcp -j HOTLINE_CI
}

redirect_off() {
  sudo iptables -t nat -D OUTPUT -p tcp -j HOTLINE_CI 2>/dev/null || true
  sudo iptables -t nat -F HOTLINE_CI 2>/dev/null || true
  sudo iptables -t nat -X HOTLINE_CI 2>/dev/null || true
}

untrust_ca() {
  sudo rm -f "$HOTLINE_CA_FILE" || true
  sudo update-ca-certificates || true
}

save_state() {
  local v
  : > "$STATE_DIR/state.env"
  for v in "$@"; do printf '%s=%q\n' "$v" "${!v}" >> "$STATE_DIR/state.env"; done
}
