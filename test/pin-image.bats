#!/usr/bin/env bats
load helpers
setup() { setup_fakes; }

pin() { run bash "$ROOT/scripts/pin-image.sh" "$1"; }
DIGEST() { printf 'c%.0s' $(seq 64); }
RELEASE="https://api.github.com/repos/petercipov/hotline/releases/latest"

@test "writes the digest of the whole multi-platform image, not one platform" {
  fixture "$RELEASE" '{"tag_name":"v1.0.0"}'
  pin 1.0.0
  [ "$status" -eq 0 ]
  grep -q "^ghcr.io/petercipov/hotline-proxy@sha256:$(DIGEST)$" "$ROOT/proxy-image.txt"
  grep -q "buildx imagetools inspect ghcr.io/petercipov/hotline-proxy:1.0.0" "$LOG"
}

@test "turns edge into its digest and marks the line prerelease while Hotline has no release" {
  pin edge
  [ "$status" -eq 0 ]
  grep -q "^ghcr.io/petercipov/hotline-proxy@sha256:$(DIGEST) *# prerelease$" "$ROOT/proxy-image.txt"
}

@test "refuses a sha- tag" {
  pin sha-abc1234
  [ "$status" -ne 0 ]
  grep -q "aaaa" "$ROOT/proxy-image.txt"
}

@test "refuses edge once Hotline has a release" {
  fixture "$RELEASE" '{"tag_name":"v1.0.0"}'
  pin edge
  [ "$status" -ne 0 ]
  [[ "$output" == *"has a release"* ]]
  grep -q "aaaa" "$ROOT/proxy-image.txt"
}

@test "refuses an image whose signature does not verify, and changes nothing" {
  FAKE_FAIL="cosign" pin edge
  [ "$status" -ne 0 ]
  [[ "$output" == *"does not verify"* ]]
  grep -q "aaaa" "$ROOT/proxy-image.txt"
}

@test "checks the signature of the digest, written by Hotline's own release workflow" {
  pin edge
  grep -q "cosign verify ghcr.io/petercipov/hotline-proxy@sha256:$(DIGEST) --certificate-identity-regexp ^https://github.com/petercipov/hotline/" "$LOG"
}
