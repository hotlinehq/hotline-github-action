# hotline-github-action

Records the failed downloads and other calls of a CI job (the 502, 503 and 504 answers that stop a lint download, for example) through the [Hotline](https://github.com/petercipov/hotline) proxy, without changing the job's other steps. Linux runners only; anywhere else it prints one warning and does nothing.

## Use

Put it first in a job, and the teardown last:

```yaml
steps:
  - uses: petercipov/hotline-github-action@v1
    env:
      OTLP_TOKEN: ${{ secrets.OTLP_TOKEN }}     # a secret named by { env: OTLP_TOKEN } below
    with:
      exporters: |
        - otlp:
            endpoint: https://otlp-gateway.example.com/otlp
            headers:
              Authorization: { env: OTLP_TOKEN }
  # ... your steps, unchanged ...
  - uses: petercipov/hotline-github-action/teardown@v1
    if: always()
```

The teardown step is a second action because a composite action has no end step of its own. Without it the job still works and a watchdog removes the redirect if the proxy stops, but the last records may be lost and there is no summary.

Without `exporters` (for example a pull request from a fork, where secrets are empty) the action does nothing.

## What it does

1. Pulls the Hotline proxy image, pinned by digest in [`proxy-image.txt`](proxy-image.txt).
2. Makes a new issuer for the job (ECDSA P-256, valid one day), writes the proxy config from your inputs and runs the proxy on the host network under its own numeric user.
3. Waits for `/readyz`, trusts the issuer (`update-ca-certificates`, `NODE_EXTRA_CA_CERTS`), and switches the redirect on last. Outgoing TCP connections to public addresses on `tls-ports` (default 443) and `http-ports` (default 80) go to the proxy; the proxy's own user is left out.
4. The runner's own agent talks to `*.githubapp.com` and does not trust the job's issuer, so those calls are always relayed untouched (and not recorded). The proxy is told to stop at once at the end of the job (`--drain-delay 0s --shutdown-timeout 10s`), so teardown does not wait for open connections.
5. If any step fails, the job gets a warning and nothing changes: no rule, no variables, no container.

Every lookup (region, public address) happens before the redirect, so the proxy does not record it. Written in Python 3, standard library only, which is on GitHub's Ubuntu runners. Needs `python3`, `docker`, `openssl`, and `iptables` in `firewall` mode, plus PyYAML or `yq` to read the YAML inputs. A runner missing one gets a warning and nothing changes.

## Inputs

See [`action.yml`](action.yml). In short: `exporters`, `apis` (an api with no `checks` list gets `checks: []`, the route only, so bodies are never read), `integration`, `passthrough`, `ech-relay`, `tls-ports`, `http-ports`, `signing-key`, `mode` (`firewall` or `env`), `image`, `region`, `region-lookup`, `labels`.

Secrets reach the proxy as `{ env: NAME }`: give the step `env: NAME: ${{ secrets.NAME }}`. Relative `spec:` paths in `apis` are read from the workspace, so check the repository out before the action, or use absolute paths.

The issuer's public certificate is the output `ca-cert` and the variable `HOTLINE_CA_CERT`; the key is never exposed. For Java: `keytool -importcert -noprompt -alias hotline-ci -file "$HOTLINE_CA_CERT" -cacerts`.

## Choosing the image

By default the action pulls the digest in `proxy-image.txt`, so the same version of the action always runs the same image. The `image` input replaces it: `image: edge` runs the newest build from Hotline's `main`, or give a full image reference. **An override gives up the digest pin and the signature check.** The action warns, and the job summary and the label `hotline.proxy.image` on every record carry the digest it actually pulled.

## Releasing

Run the `pin image` workflow by hand with a Hotline image tag. It looks up the digest of the whole multi-platform image, refuses unless the cosign signature verifies, and commits `proxy-image.txt`. Until Hotline has a release it accepts `edge` and marks the line `# prerelease`. Then tag that commit; a moving `v1` tag reads the file of the version it points to.

## Limits

- The job summary lists the mode, image and region, not the calls: the calls are in your OpenTelemetry destination. Counting 5xx answers there needs the proxy to offer them (not built).
- With no metadata service, the region is the nearest Cloudflare site code, not a cloud region name.
- Programs that pin a vendor's certificate fail unless listed in `passthrough`. UDP such as HTTP/3 is not redirected.
- Anything before the action's own step is not seen, including the runner fetching actions.

## Development

```
python3 -m unittest discover -s . -t . -p 'test_*.py'
```

The code is in `hotline_action/`, started by `run.py` (`setup`, `teardown`, `watchdog`, `pin-image TAG`). The tests use a fake machine (`test/helpers.py`) that records every command and answers web requests from a table, so they need no Docker, firewall or network. Only `openssl` is real, so the tests can look at the certificate.
