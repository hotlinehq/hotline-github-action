"""Writes proxy-image.txt from a Hotline image tag: the digest of the whole
multi-platform image, only if its signature verifies. Until Hotline has a
release, edge is allowed and the line is marked prerelease."""

import json
import re

from .common import IMAGE_REPO

RELEASE_URL = "https://api.github.com/repos/hotlinehq/hotline/releases/latest"


def run_pin(tag, root, system, log=print):
    """Returns the exit code."""
    prerelease = ""
    if tag.startswith("sha-"):
        log("refusing a sha- tag; pin a release, or edge before the first release")
        return 1
    if tag == "edge":
        if system.http_get(RELEASE_URL, {"User-Agent": "hotline-github-action"}) is not None:
            log("refusing edge: Hotline has a release now; pin a release tag")
            return 1
        prerelease = "  # prerelease"
    try:
        out = system.run("docker", "buildx", "imagetools", "inspect", f"{IMAGE_REPO}:{tag}",
                         "--format", "{{json .Manifest}}", capture=True)
        digest = json.loads(out)["digest"]
    except Exception as error:
        log(f"could not look up {IMAGE_REPO}:{tag}: {error}")
        return 1
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        log(f"no digest found for {tag}")
        return 1
    try:
        system.run("cosign", "verify", f"{IMAGE_REPO}@{digest}",
                   "--certificate-identity-regexp", "^https://github.com/hotlinehq/hotline/",
                   "--certificate-oidc-issuer", "https://token.actions.githubusercontent.com")
    except Exception:
        log(f"the signature of {IMAGE_REPO}:{tag} does not verify; nothing was changed")
        return 1
    (root / "proxy-image.txt").write_text(
        "# The Hotline image the action pulls, by digest. Written by the 'pin image' workflow.\n"
        f"{IMAGE_REPO}@{digest}{prerelease}\n")
    log(f"pinned {IMAGE_REPO}@{digest}")
    return 0
