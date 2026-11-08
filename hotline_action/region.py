"""Where the runner is: the region and the public address. Never fails."""

import re

_AZURE = "http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text"
_AWS_TOKEN = "http://169.254.169.254/latest/api/token"
_AWS_REGION = "http://169.254.169.254/latest/meta-data/placement/region"
_GOOGLE_ZONE = "http://metadata.google.internal/computeMetadata/v1/instance/zone"
_TRACE = "https://1.1.1.1/cdn-cgi/trace"


def _text(value):
    return value.strip() if value else ""


def lookup(system, mode, lookup_input, runner_environment):
    """Returns (region, public_ip); either may be empty.

    mode is auto, off, or a fixed region that always wins."""
    if mode == "off":
        return "", ""
    region = "" if mode == "auto" else mode
    public_ip = ""
    if not region:
        # 1. the cloud's own metadata address: Azure, AWS, then Google Cloud
        region = _text(system.http_get(_AZURE, {"Metadata": "true"}))
        if not region:
            token = _text(system.http_get(
                _AWS_TOKEN, {"X-aws-ec2-metadata-token-ttl-seconds": "60"}, method="PUT"))
            if token:
                region = _text(system.http_get(_AWS_REGION, {"X-aws-ec2-metadata-token": token}))
        if not region:
            zone = _text(system.http_get(_GOOGLE_ZONE, {"Metadata-Flavor": "Google"}))
            if zone:
                region = zone.rsplit("/", 1)[-1].rsplit("-", 1)[0]
    # 2. the public address and the nearest Cloudflare site: GitHub's own
    # runners only; a self-hosted runner never sends its address to a third
    # party unless `region-lookup: public-ip` says so.
    if runner_environment == "github-hosted" or lookup_input == "public-ip":
        trace = system.http_get(_TRACE)
        if trace:
            ip = re.search(r"^ip=(.+)$", trace, re.M)
            colo = re.search(r"^colo=(.+)$", trace, re.M)
            public_ip = ip.group(1).strip() if ip else ""
            if not region and mode == "auto" and colo:
                region = colo.group(1).strip()
    return region, public_ip
