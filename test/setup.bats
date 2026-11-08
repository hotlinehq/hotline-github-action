#!/usr/bin/env bats
load helpers
setup() { setup_fakes; }

# ---- guards ------------------------------------------------------------
@test "does nothing, with one warning, on a system that is not Linux" {
  FAKE_UNAME=Darwin run_setup
  [ "$status" -eq 0 ]
  [[ "$output" == *"Linux runners only"* ]]
  ! grep -q '^docker\|^iptables' "$LOG"
  [ ! -e "$HOTLINE_CI_STATE_DIR" ]
}

@test "does nothing, with one warning, when there are no exporters (fork pull request)" {
  HOTLINE_IN_EXPORTERS="" run_setup
  [ "$status" -eq 0 ]
  [[ "$output" == *"forks"* ]]
  ! grep -q '^docker' "$LOG"
}

@test "warns naming the tool, and does nothing, when a needed tool is missing" {
  for tool in jq yq openssl curl docker iptables; do
    PATH="$(path_without "$tool")" run_setup
    [ "$status" -eq 0 ]
    [[ "$output" == *"'$tool' is not installed"* ]]
  done
  ! grep -q '^docker' "$LOG"
}

# ---- the image ---------------------------------------------------------
@test "uses the digest in proxy-image.txt when image is empty" {
  run_setup
  [ "$status" -eq 0 ]
  grep -q "^docker pull ghcr.io/petercipov/hotline-proxy@sha256:a" "$LOG"
  [[ "$output" != *"not pinned"* ]]
}

@test "ignores comments and a prerelease note in proxy-image.txt" {
  printf '# a comment\nghcr.io/petercipov/hotline-proxy@sha256:%s  # prerelease\n' "$(printf 'd%.0s' $(seq 64))" > "$ROOT/proxy-image.txt"
  run_setup
  grep -q "^docker pull ghcr.io/petercipov/hotline-proxy@sha256:d" "$LOG"
}

@test "reads the word edge as the edge tag of the Hotline image" {
  HOTLINE_IN_IMAGE=edge run_setup
  grep -q "^docker pull ghcr.io/petercipov/hotline-proxy:edge$" "$LOG"
}

@test "uses a full image reference when image is set, and warns once that it is not pinned" {
  HOTLINE_IN_IMAGE=ghcr.io/petercipov/hotline-proxy:sha-abc run_setup
  grep -q "^docker pull ghcr.io/petercipov/hotline-proxy:sha-abc$" "$LOG"
  [ "$(grep -c 'not pinned' <<<"$output")" -eq 1 ]
}

@test "records the digest actually pulled as a label on every exporter" {
  HOTLINE_IN_IMAGE=edge run_setup
  [ "$(config '.exporters[0].otlp.resource["hotline.proxy.image"]')" = "\"ghcr.io/petercipov/hotline-proxy@sha256:$(printf 'b%.0s' $(seq 64))\"" ]
}

@test "does not fall back to the pin when the override pull fails: fails open" {
  FAKE_FAIL="docker:pull" HOTLINE_IN_IMAGE=edge run_setup
  [ "$status" -eq 0 ]
  [[ "$output" == *"pulling the image"* ]]
  [ "$(grep -c '^docker pull' "$LOG")" -eq 1 ]
  ! grep -q "iptables" "$LOG"
}

@test "fails open with a clear warning when nothing is pinned and no image is set" {
  printf '# nothing\n' > "$ROOT/proxy-image.txt"
  run_setup
  [ "$status" -eq 0 ]
  [[ "$output" == *"choosing the image"* ]]
}

# ---- the config --------------------------------------------------------
@test "gives an api with no checks the list [], and leaves an api with a list alone" {
  HOTLINE_IN_APIS=$'stripe:\n  hosts: [api.stripe.com]\n  spec: ./specs/stripe.yaml\ntalon:\n  hosts: [api.talon.one]\n  spec: /abs/talon.yaml\n  checks: [path, body]' run_setup
  [ "$status" -eq 0 ]
  [ "$(config '.apis.stripe.checks')" = "[]" ]
  [ "$(config '.apis.talon.checks')" = '["path","body"]' ]
}

@test "reads spec files from the workspace mounted in the container" {
  HOTLINE_IN_APIS=$'stripe:\n  hosts: [api.stripe.com]\n  spec: ./specs/stripe.yaml' run_setup
  [ "$(config '.apis.stripe.spec')" = '"/workspace/specs/stripe.yaml"' ]
}

@test "writes the default integration from owner, repository and workflow" {
  run_setup
  [ "$(config '.integrations[0].id')" = '"ci-acme_shop__CI_build"' ]
  [ "$(config '.listen.intercept.integration')" = '"ci-acme_shop__CI_build"' ]
}

@test "uses the integration input when given" {
  HOTLINE_IN_INTEGRATION=my-ci run_setup
  [ "$(config '.integrations[0].id')" = '"my-ci"' ]
}

@test "lists the apis on the integration, and sends plain calls out as plain" {
  HOTLINE_IN_APIS=$'stripe:\n  hosts: [api.stripe.com]\n  spec: ./s.yaml' run_setup
  [ "$(config '.integrations[0].apis')" = '["stripe"]' ]
  [ "$(config '.integrations[] | select(.id=="not-passed") | .tlsOrigination')" = "false" ]
}

@test "keeps secrets as { env: NAME }, passes the name to the container, never the value" {
  OTLP_TOKEN=s3cret run_setup
  [ "$(config '.exporters[0].otlp.headers.Authorization')" = '{"env":"OTLP_TOKEN"}' ]
  grep -q -- "-e OTLP_TOKEN" "$LOG"
  ! grep -q "s3cret" "$LOG"
  ! grep -q "s3cret" "$HOTLINE_CI_STATE_DIR/config/hotline-proxy.yaml"
}

@test "signs records with the signing key by name, and leaves it out when there is none" {
  HOTLINE_IN_SIGNING_KEY=k run_setup
  [ "$(config '.integrations[0].fingerprintKey')" = '{"env":"HOTLINE_SIGNING_KEY"}' ]
  grep -q -- "-e HOTLINE_SIGNING_KEY" "$LOG"
  run_setup
  [ "$(config '.integrations[0].fingerprintKey')" = "null" ]
}

@test "puts the labels in every exporter's resource, extra labels and the exporter's own winning" {
  HOTLINE_IN_EXPORTERS=$'- otlp: { endpoint: "http://a:4318" }\n- otlp: { endpoint: "http://b:4318", resource: { service.name: mine } }' \
    HOTLINE_IN_LABELS='{team: infra, vcs.ref.head.name: custom}' run_setup
  for i in 0 1; do
    [ "$(config ".exporters[$i].otlp.resource[\"cicd.pipeline.name\"]")" = '"CI build"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"cicd.pipeline.task.name\"]")" = '"lint"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"cicd.pipeline.run.id\"]")" = '"42"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"vcs.repository.name\"]")" = '"acme/shop"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"hotline.ci.run_attempt\"]")" = '"1"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"hotline.ci.event\"]")" = '"push"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"host.name\"]")" = '"runner-1"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"hotline.runner.environment\"]")" = '"github-hosted"' ]
    [ "$(config ".exporters[$i].otlp.resource.team")" = '"infra"' ]
    [ "$(config ".exporters[$i].otlp.resource[\"vcs.ref.head.name\"]")" = '"custom"' ]
  done
  [ "$(config '.exporters[1].otlp.resource["service.name"]')" = '"mine"' ]
}

@test "passes passthrough and ech-relay to the intercept listener, and leaves them out when empty" {
  run_setup
  [ "$(config '.listen.intercept | has("passthrough")')" = "false" ]
  HOTLINE_IN_PASSTHROUGH='[pinned.example.com]' HOTLINE_IN_ECH_RELAY='[outer.example.net]' run_setup
  [ "$(config '.listen.intercept.passthrough')" = '["pinned.example.com"]' ]
  [ "$(config '.listen.intercept.ech.relay')" = '["outer.example.net"]' ]
}

# ---- the issuer --------------------------------------------------------
@test "makes an ECDSA P-256 certificate named Hotline CI issuer, valid for one day" {
  run_setup
  crt="$HOTLINE_CI_STATE_DIR/ca/ca.crt"
  text="$(openssl x509 -in "$crt" -noout -text)"
  [[ "$text" == *"prime256v1"* ]]
  [[ "$text" == *"CN = Hotline CI issuer"* || "$text" == *"CN=Hotline CI issuer"* ]]
  [[ "$text" == *"CA:TRUE"* ]]
  openssl x509 -in "$crt" -noout -checkend 80000
  ! openssl x509 -in "$crt" -noout -checkend 90000
}

@test "keeps the key private: only the proxy's user, mode 400, and never in the environment file" {
  run_setup
  grep -q "^sudo chown 64321 .*ca.key" "$LOG"
  grep -q "^sudo chmod 400 .*ca.key" "$LOG"
  ! grep -q "ca.key" "$GITHUB_ENV" "$GITHUB_OUTPUT"
}

@test "hands the certificate over by path: output ca-cert and HOTLINE_CA_CERT" {
  run_setup
  grep -q "^ca-cert=$HOTLINE_CI_STATE_DIR/ca/ca.crt" "$GITHUB_OUTPUT"
  grep -q "^HOTLINE_CA_CERT=$HOTLINE_CI_STATE_DIR/ca/ca.crt" "$GITHUB_ENV"
  grep -q "^NODE_EXTRA_CA_CERTS=" "$GITHUB_ENV"
}

# ---- region and public address ----------------------------------------
@test "uses a fixed region before any lookup" {
  HOTLINE_IN_REGION=eu-west-1 run_setup
  [ "$(config '.exporters[0].otlp.resource["cloud.region"]')" = '"eu-west-1"' ]
  ! grep -q "169.254.169.254" "$LOG"
}

@test "tries Azure, then AWS, then Google metadata, and stops at the first answer" {
  fixture "http://169.254.169.254/latest/api/token" "tok"
  fixture "http://169.254.169.254/latest/meta-data/placement/region" "eu-central-1"
  fixture "http://metadata.google.internal/computeMetadata/v1/instance/zone" "projects/1/zones/europe-west1-b"
  run_setup
  [ "$(config '.exporters[0].otlp.resource["cloud.region"]')" = '"eu-central-1"' ]
  ! grep -q "metadata.google.internal" "$LOG"
  fixture "http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text" "westeurope"
  run_setup
  [ "$(config '.exporters[0].otlp.resource["cloud.region"]')" = '"westeurope"' ]
}

@test "turns a Google zone into a region" {
  fixture "http://metadata.google.internal/computeMetadata/v1/instance/zone" "projects/1/zones/europe-west1-b"
  run_setup
  [ "$(config '.exporters[0].otlp.resource["cloud.region"]')" = '"europe-west1"' ]
}

@test "on GitHub's runners the public address and the Cloudflare site come from the trace page" {
  fixture "https://1.1.1.1/cdn-cgi/trace" $'fl=1\nip=203.0.113.9\ncolo=AMS'
  run_setup
  [ "$(config '.exporters[0].otlp.resource["hotline.runner.public_ip"]')" = '"203.0.113.9"' ]
  [ "$(config '.exporters[0].otlp.resource["cloud.region"]')" = '"AMS"' ]
}

@test "metadata wins over the Cloudflare site for the region, but the address is still labelled" {
  fixture "http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text" "westeurope"
  fixture "https://1.1.1.1/cdn-cgi/trace" $'ip=203.0.113.9\ncolo=AMS'
  run_setup
  [ "$(config '.exporters[0].otlp.resource["cloud.region"]')" = '"westeurope"' ]
  [ "$(config '.exporters[0].otlp.resource["hotline.runner.public_ip"]')" = '"203.0.113.9"' ]
}

@test "does no public address lookup on a self-hosted runner unless region-lookup is public-ip" {
  fixture "https://1.1.1.1/cdn-cgi/trace" $'ip=203.0.113.9\ncolo=AMS'
  RUNNER_ENVIRONMENT=self-hosted run_setup
  ! grep -q "1.1.1.1" "$LOG"
  [ "$(config '.exporters[0].otlp.resource | has("hotline.runner.public_ip")')" = "false" ]
  RUNNER_ENVIRONMENT=self-hosted HOTLINE_IN_REGION_LOOKUP=public-ip run_setup
  grep -q "1.1.1.1" "$LOG"
}

@test "region off does no lookup, and no region means no label while setup still succeeds" {
  HOTLINE_IN_REGION=off run_setup
  [ "$status" -eq 0 ]
  ! grep -q "169.254.169.254\|1.1.1.1" "$LOG"
  HOTLINE_IN_REGION=auto RUNNER_ENVIRONMENT=self-hosted run_setup
  [ "$status" -eq 0 ]
  [ "$(config '.exporters[0].otlp.resource | has("cloud.region")')" = "false" ]
}

# ---- order and modes ---------------------------------------------------
@test "switches the redirect on last, after the proxy is ready and the issuer is trusted" {
  run_setup
  [ "$status" -eq 0 ]
  run_line="$(grep -n '^docker run' "$LOG" | head -1 | cut -d: -f1)"
  ready_line="$(grep -n 'readyz' "$LOG" | head -1 | cut -d: -f1)"
  trust_line="$(grep -n 'update-ca-certificates' "$LOG" | head -1 | cut -d: -f1)"
  nat_line="$(grep -n 'iptables -t nat -A OUTPUT' "$LOG" | head -1 | cut -d: -f1)"
  [ "$run_line" -lt "$ready_line" ]
  [ "$ready_line" -lt "$trust_line" ]
  [ "$trust_line" -lt "$nat_line" ]
}

@test "does every lookup before the redirect" {
  fixture "https://1.1.1.1/cdn-cgi/trace" $'ip=203.0.113.9\ncolo=AMS'
  run_setup
  last_curl="$(grep -n '^curl' "$LOG" | tail -1 | cut -d: -f1)"
  nat_line="$(grep -n 'iptables -t nat -A OUTPUT' "$LOG" | head -1 | cut -d: -f1)"
  [ "$last_curl" -lt "$nat_line" ]
}

@test "runs the proxy on the host network, under its own numeric user, read only" {
  run_setup
  line="$(grep '^docker run' "$LOG")"
  [[ "$line" == *"--network host"* ]]
  [[ "$line" == *"--user 64321:64321"* ]]
  [[ "$line" == *"--read-only"* ]]
  [[ "$line" == *"--cap-drop ALL"* ]]
}

@test "firewall mode sends tls ports to the intercept port and http ports to the proxy port" {
  HOTLINE_IN_TLS_PORTS='[443, 8443]' HOTLINE_IN_HTTP_PORTS='[80]' run_setup
  grep -q -- "--dport 443 -j REDIRECT --to-ports 18444" "$LOG"
  grep -q -- "--dport 8443 -j REDIRECT --to-ports 18444" "$LOG"
  grep -q -- "--dport 80 -j REDIRECT --to-ports 18080" "$LOG"
  ! grep -q -- "--dport 22 " "$LOG"
}

@test "firewall mode leaves the proxy's own user and non-public addresses out" {
  run_setup
  grep -q -- "--uid-owner 64321 -j RETURN" "$LOG"
  for range in 10.0.0.0/8 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16; do
    grep -q -- "-d $range -j RETURN" "$LOG"
  done
  [ "$(grep -n 'uid-owner' "$LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n -- '--dport' "$LOG" | head -1 | cut -d: -f1)" ]
}

@test "env mode sets HTTPS_PROXY and HTTP_PROXY, names the issuer for CONNECT, and adds no firewall rule" {
  HOTLINE_IN_MODE=env run_setup
  [ "$status" -eq 0 ]
  ! grep -q "iptables" "$LOG"
  grep -q "^HTTPS_PROXY=http://ci-acme_shop__CI_build:x@127.0.0.1:18080" "$GITHUB_ENV"
  grep -q "^HTTP_PROXY=" "$GITHUB_ENV"
  [ "$(config '.listen.connect.issuer')" = '"ci"' ]
}

@test "env mode needs no iptables" {
  HOTLINE_IN_MODE=env PATH="$(path_without iptables)" run_setup
  [ "$status" -eq 0 ]
  grep -q "^HTTPS_PROXY=" "$GITHUB_ENV"
}

@test "a mode that is neither firewall nor env fails open" {
  HOTLINE_IN_MODE=banana run_setup
  [ "$status" -eq 0 ]
  ! grep -q '^docker run' "$LOG"
}

@test "changes nothing the job can see until every step has worked" {
  FAKE_FAIL="iptables" run_setup
  [ "$status" -eq 0 ]
  [ ! -s "$GITHUB_ENV" ]
  [ ! -s "$GITHUB_OUTPUT" ]
}

# ---- fail open, one test per step -------------------------------------
fail_open_check() {
  [ "$status" -eq 0 ]
  [[ "$output" == *"::warning"* ]]
  [[ "$output" == *"$1"* ]]
  [ ! -s "$GITHUB_ENV" ]
  [ ! -e "$HOTLINE_CI_STATE_DIR" ]
}

@test "fail open: the pull fails" {
  FAKE_FAIL="docker:pull" run_setup
  fail_open_check "pulling the image"
}

@test "fail open: the inputs cannot be read (bad exporters)" {
  HOTLINE_IN_EXPORTERS="- [unclosed" run_setup
  fail_open_check "reading the inputs"
}

@test "fail open: the proxy does not start, and no rule is added" {
  FAKE_FAIL="docker:run" run_setup
  fail_open_check "starting the proxy"
  ! grep -q "iptables -t nat -A" "$LOG"
}

@test "fail open: the proxy never becomes ready, and its container is removed" {
  drop_ready
  run_setup
  fail_open_check "waiting for the proxy to be ready"
  grep -q "^docker rm -f hotline-ci-proxy" "$LOG"
  ! grep -q "iptables -t nat -A" "$LOG"
}

@test "fail open: trusting the issuer fails, the container is removed, no rule is added" {
  FAKE_FAIL="update-ca-certificates" run_setup
  fail_open_check "trusting the issuer"
  ! grep -q "iptables -t nat -A" "$LOG"
  grep -q "^docker rm -f hotline-ci-proxy" "$LOG"
  grep -q "^sudo rm -f /usr/local/share/ca-certificates/hotline-ci.crt" "$LOG"
}

@test "fail open: the redirect fails, and everything done is undone, newest first" {
  FAKE_FAIL="iptables:-t nat -A OUTPUT" run_setup
  fail_open_check "switching on the redirect"
  grep -q "iptables -t nat -D OUTPUT" "$LOG"
  rm_ca="$(grep -n 'sudo rm -f /usr/local' "$LOG" | head -1 | cut -d: -f1)"
  rm_ct="$(grep -n 'docker rm -f hotline-ci-proxy' "$LOG" | tail -1 | cut -d: -f1)"
  [ "$rm_ca" -lt "$rm_ct" ]
}
