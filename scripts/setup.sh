#!/usr/bin/env bash
# Start of the job: runs the Hotline proxy and sends this job's outgoing
# connections through it. Any problem leaves the job exactly as it was.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

# Inputs arrive as HOTLINE_IN_* variables, set by action.yml.
EXPORTERS="${HOTLINE_IN_EXPORTERS:-}"
APIS="${HOTLINE_IN_APIS:-}"
MODE="${HOTLINE_IN_MODE:-firewall}"
REGION_INPUT="${HOTLINE_IN_REGION:-auto}"

# ---- guards: say so once and do nothing -------------------------------
if [ "$(uname -s)" != "Linux" ]; then
  warn "Hotline works on Linux runners only; nothing was changed."
  exit 0
fi
if [ -z "${EXPORTERS//[[:space:]]/}" ]; then
  warn "No exporters were given (secrets are not available to pull requests from forks); nothing was changed."
  exit 0
fi
need="jq yq openssl curl docker"
[ "$MODE" = "firewall" ] && need="$need iptables"
for tool in $need; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    warn "The tool '$tool' is not installed on this runner; nothing was changed."
    exit 0
  fi
done

STEP="start"
MAIN_PID=$$
on_error() {
  # a failure inside $( ) or ( ) only ends that subshell with its status;
  # the main shell then fails on it and does the cleanup once
  [ "$BASHPID" = "$MAIN_PID" ] || exit 1
  warn "Setup stopped at the step '$STEP'; the job carries on without Hotline."
  run_undo
  rm -rf "$STATE_DIR"
  exit 0
}
trap on_error ERR

rm -rf "$STATE_DIR"
mkdir -p "$STATE_DIR/ca" "$STATE_DIR/config"
chmod 755 "$STATE_DIR" "$STATE_DIR/ca" "$STATE_DIR/config"
# shellcheck disable=SC2016
push_undo 'rm -rf "$STATE_DIR"'

# ---- inputs -----------------------------------------------------------
STEP="reading the inputs"
EXPORTERS_JSON="$(yaml_arg "$EXPORTERS" '[]')"
APIS_JSON="$(yaml_arg "$APIS" '{}')"
LABELS_JSON="$(yaml_arg "${HOTLINE_IN_LABELS:-}" '{}')"
PASSTHROUGH_JSON="$(yaml_arg "${HOTLINE_IN_PASSTHROUGH:-}" '[]')"
ECH_JSON="$(yaml_arg "${HOTLINE_IN_ECH_RELAY:-}" '[]')"
export TLS_PORTS HTTP_PORTS
TLS_PORTS="$(yaml_arg "${HOTLINE_IN_TLS_PORTS:-}" '[443]' | jq -r 'join(",")')"
HTTP_PORTS="$(yaml_arg "${HOTLINE_IN_HTTP_PORTS:-}" '[80]' | jq -r 'join(",")')"
case "$MODE" in firewall | env) ;; *) echo "mode must be firewall or env" >&2; false ;; esac

INTEGRATION="${HOTLINE_IN_INTEGRATION:-}"
if [ -z "$INTEGRATION" ]; then
  INTEGRATION="ci-${GITHUB_REPOSITORY_OWNER:-owner}_${GITHUB_REPOSITORY#*/}__${GITHUB_WORKFLOW:-workflow}"
fi
INTEGRATION="$(printf '%s' "$INTEGRATION" | tr -c 'A-Za-z0-9_.-' '_')"

# ---- the image --------------------------------------------------------
STEP="choosing the image"
if ! resolve_image "${HOTLINE_IN_IMAGE:-}" "$ROOT"; then
  echo "No image is pinned yet: run the 'pin image' workflow, or set image: edge." >&2
  false
fi
if [ "$IMAGE_OVERRIDDEN" = 1 ]; then
  warn "The 'image' input is set ($IMAGE_REF): this image is not pinned by digest and its signature is not checked."
fi
STEP="pulling the image"
docker pull "$IMAGE_REF" >/dev/null
IMAGE_DIGEST="$(docker image inspect --format '{{index .RepoDigests 0}}' "$IMAGE_REF")"

# ---- where the runner is (before the redirect, so none of it is recorded) -
STEP="looking up the region"
region_lookup "$REGION_INPUT" "${HOTLINE_IN_REGION_LOOKUP:-}" "${RUNNER_ENVIRONMENT:-}"

# ---- the issuer: a new one for this job, valid one day ----------------
STEP="making the issuer"
(
  umask 077
  openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 1 \
    -subj "/CN=Hotline CI issuer" \
    -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout "$STATE_DIR/ca/ca.key" -out "$STATE_DIR/ca/ca.crt" 2>/dev/null
)
chmod 644 "$STATE_DIR/ca/ca.crt"
# only the proxy's own user can read the key; the job's steps cannot
sudo chown "$HOTLINE_UID" "$STATE_DIR/ca/ca.key"
sudo chmod 400 "$STATE_DIR/ca/ca.key"

# ---- the config -------------------------------------------------------
STEP="writing the config"
SIGNING=0
[ -n "${HOTLINE_IN_SIGNING_KEY:-}" ] && SIGNING=1
jq -n \
  --argjson exporters "$EXPORTERS_JSON" --argjson apis "$APIS_JSON" \
  --argjson labels "$LABELS_JSON" --argjson passthrough "$PASSTHROUGH_JSON" --argjson ech "$ECH_JSON" \
  --arg integration "$INTEGRATION" --arg mode "$MODE" --argjson signing "$SIGNING" \
  --arg region "$REGION" --arg publicip "$PUBLIC_IP" --arg image "$IMAGE_DIGEST" \
  --arg proxyport "$HOTLINE_PROXY_PORT" --arg adminport "$HOTLINE_ADMIN_PORT" --arg interceptport "$HOTLINE_INTERCEPT_PORT" \
  --arg repo "${GITHUB_REPOSITORY:-}" --arg workflow "${GITHUB_WORKFLOW:-}" --arg job "${GITHUB_JOB:-}" \
  --arg runid "${GITHUB_RUN_ID:-}" --arg attempt "${GITHUB_RUN_ATTEMPT:-}" --arg event "${GITHUB_EVENT_NAME:-}" \
  --arg branch "${GITHUB_REF_NAME:-}" --arg runner "${RUNNER_NAME:-}" --arg runnerenv "${RUNNER_ENVIRONMENT:-}" \
  '
  def abspath: if type == "string" and (startswith("/") | not) then "/workspace/" + sub("^\\./"; "") else . end;
  def minimal: if has("checks") then . else . + {checks: []} end;
  def prune: with_entries(select(.value != ""));
  ({
    "cicd.pipeline.name": $workflow,
    "cicd.pipeline.task.name": $job,
    "cicd.pipeline.run.id": $runid,
    "vcs.repository.name": $repo,
    "vcs.ref.head.name": $branch,
    "hotline.ci.run_attempt": $attempt,
    "hotline.ci.event": $event,
    "os.type": "linux",
    "host.name": $runner,
    "hotline.runner.environment": $runnerenv,
    "cloud.region": $region,
    "hotline.runner.public_ip": $publicip,
    "hotline.proxy.image": $image
  } | prune) as $base
  | {
    listen: ({
      proxy: ("127.0.0.1:" + $proxyport),
      admin: ("127.0.0.1:" + $adminport),
      intercept: ({address: ("127.0.0.1:" + $interceptport), issuer: "ci", integration: $integration}
        + (if ($passthrough | length) > 0 then {passthrough: $passthrough} else {} end)
        + (if ($ech | length) > 0 then {ech: {relay: $ech}} else {} end))
    } + (if $mode == "env" then {connect: {issuer: "ci"}} else {} end)),
    issuers: {ci: {cert: "/etc/hotline/ci/ca.crt", key: "/etc/hotline/ci/ca.key"}},
    exporters: ($exporters | map(if has("otlp") then .otlp.resource = ($base + $labels + (.otlp.resource // {})) else . end)),
    apis: ($apis | map_values(minimal
      | (if has("spec") then .spec |= abspath else . end)
      | (if has("overlays") then .overlays |= map(abspath) else . end))),
    integrations: [
      ({id: $integration}
        + (if ($apis | length) > 0 then {apis: ($apis | keys)} else {} end)
        + (if $signing == 1 then {fingerprintKey: {env: "HOTLINE_SIGNING_KEY"}} else {} end)),
      {id: "not-passed", tlsOrigination: false}
    ]
  }' > "$STATE_DIR/config/hotline-proxy.yaml"   # JSON is valid YAML

# ---- run the proxy ----------------------------------------------------
STEP="starting the proxy"
env_args=()
for name in $(jq -r '[.. | objects | select(has("env")) | .env] | unique[]' "$STATE_DIR/config/hotline-proxy.yaml"); do
  env_args+=(-e "$name")
done
export HOTLINE_SIGNING_KEY="${HOTLINE_IN_SIGNING_KEY:-}"
docker rm -f "$HOTLINE_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$HOTLINE_CONTAINER" --network host \
  --user "$HOTLINE_UID:$HOTLINE_UID" --read-only --cap-drop ALL \
  -v "$STATE_DIR/config:/etc/hotline/config:ro" -v "$STATE_DIR/ca:/etc/hotline/ci:ro" \
  -v "${GITHUB_WORKSPACE:-$PWD}:/workspace:ro" \
  "${env_args[@]}" "$IMAGE_REF" run --config /etc/hotline/config/hotline-proxy.yaml >/dev/null
push_undo "docker rm -f $HOTLINE_CONTAINER >/dev/null 2>&1"

STEP="waiting for the proxy to be ready"
ready=0
for _ in $(seq 1 30); do
  if curl -fs "http://127.0.0.1:$HOTLINE_ADMIN_PORT/readyz" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[ "$ready" = 1 ]

# ---- trust the issuer -------------------------------------------------
STEP="trusting the issuer"
sudo cp "$STATE_DIR/ca/ca.crt" "$HOTLINE_CA_FILE"
push_undo 'untrust_ca'
sudo update-ca-certificates >/dev/null

# ---- the redirect, last -----------------------------------------------
STEP="switching on the redirect"
if [ "$MODE" = "firewall" ]; then
  push_undo 'redirect_off'
  redirect_on
fi

# ---- done: only now does the rest of the job see any change -----------
STEP="finishing"
save_state MODE TLS_PORTS HTTP_PORTS INTEGRATION IMAGE_REF IMAGE_DIGEST IMAGE_OVERRIDDEN REGION PUBLIC_IP
if [ -n "${GITHUB_ENV:-}" ]; then
  {
    echo "HOTLINE_CA_CERT=$STATE_DIR/ca/ca.crt"
    echo "NODE_EXTRA_CA_CERTS=$STATE_DIR/ca/ca.crt"
    if [ "$MODE" = "env" ]; then
      echo "HTTPS_PROXY=http://$INTEGRATION:x@127.0.0.1:$HOTLINE_PROXY_PORT"
      echo "HTTP_PROXY=http://$INTEGRATION:x@127.0.0.1:$HOTLINE_PROXY_PORT"
      echo "NO_PROXY=localhost,127.0.0.1"
    fi
  } >> "$GITHUB_ENV"
fi
[ -n "${GITHUB_OUTPUT:-}" ] && echo "ca-cert=$STATE_DIR/ca/ca.crt" >> "$GITHUB_OUTPUT"

if [ "$MODE" = "firewall" ]; then
  # the watchdog must outlive this step; the runner keeps processes that
  # carry another tracking id
  RUNNER_TRACKING_ID="" setsid nohup bash "$HERE/watchdog.sh" >/dev/null 2>&1 < /dev/null &
fi
note "recording this job's calls with $IMAGE_DIGEST"
trap - ERR
