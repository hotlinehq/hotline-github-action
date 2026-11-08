#!/usr/bin/env bash
# Writes proxy-image.txt from a Hotline image tag: the digest of the whole
# multi-platform image, only if its signature verifies. Until Hotline has a
# release, `edge` is allowed and the line is marked prerelease.
set -Eeuo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

tag="${1:?usage: pin-image.sh <hotline image tag>}"
prerelease=""

case "$tag" in
  sha-*) echo "refusing a sha- tag; pin a release, or edge before the first release" >&2; exit 1 ;;
  edge)
    if curl -fs "https://api.github.com/repos/petercipov/hotline/releases/latest" >/dev/null 2>&1; then
      echo "refusing edge: Hotline has a release now; pin a release tag" >&2
      exit 1
    fi
    prerelease="  # prerelease"
    ;;
esac

digest="$(docker buildx imagetools inspect "$HOTLINE_IMAGE_REPO:$tag" --format '{{json .Manifest}}' | jq -r '.digest')"
case "$digest" in sha256:????????????????????????????????????????????????????????????????) ;; *) echo "no digest found for $tag" >&2; exit 1 ;; esac

if ! cosign verify "$HOTLINE_IMAGE_REPO@$digest" \
  --certificate-identity-regexp '^https://github.com/petercipov/hotline/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com >/dev/null; then
  echo "the signature of $HOTLINE_IMAGE_REPO:$tag does not verify; nothing was changed" >&2
  exit 1
fi

{
  echo "# The Hotline image the action pulls, by digest. Written by the 'pin image' workflow."
  echo "$HOTLINE_IMAGE_REPO@$digest$prerelease"
} > "$ROOT/proxy-image.txt"
echo "pinned $HOTLINE_IMAGE_REPO@$digest"
