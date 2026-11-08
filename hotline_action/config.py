"""The proxy's config, written from the action's inputs."""

from .common import ADMIN_PORT, INTERCEPT_PORT, PROXY_PORT, RUNNER_PASSTHROUGH


def _abspath(value):
    # spec files are read from the workspace, mounted at /workspace
    if not isinstance(value, str) or value.startswith("/"):
        return value
    return "/workspace/" + (value[2:] if value.startswith("./") else value)


def base_labels(env, region, public_ip, image_digest):
    """Labels on every exporter, with standard OpenTelemetry names where they exist."""
    labels = {
        "cicd.pipeline.name": env.get("GITHUB_WORKFLOW", ""),
        "cicd.pipeline.task.name": env.get("GITHUB_JOB", ""),
        "cicd.pipeline.run.id": env.get("GITHUB_RUN_ID", ""),
        "vcs.repository.name": env.get("GITHUB_REPOSITORY", ""),
        # on a pull request GITHUB_REF_NAME is the merge ref, such as 119/merge
        "vcs.ref.head.name": env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME", ""),
        "hotline.ci.run_attempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "hotline.ci.event": env.get("GITHUB_EVENT_NAME", ""),
        "os.type": "linux",
        "host.name": env.get("RUNNER_NAME", ""),
        "hotline.runner.environment": env.get("RUNNER_ENVIRONMENT", ""),
        "cloud.region": region,
        "hotline.runner.public_ip": public_ip,
        "hotline.proxy.image": image_digest,
    }
    return {k: v for k, v in labels.items() if v != ""}


def build(*, exporters, apis, labels, extra_labels, passthrough, ech_relay,
          integration, mode, signing):
    exporters = exporters or []
    apis = apis or {}
    intercept = {"address": f"127.0.0.1:{INTERCEPT_PORT}", "issuer": "ci", "integration": integration}
    intercept["passthrough"] = RUNNER_PASSTHROUGH + [h for h in (passthrough or []) if h not in RUNNER_PASSTHROUGH]
    if ech_relay:
        intercept["ech"] = {"relay": ech_relay}
    listen = {"proxy": f"127.0.0.1:{PROXY_PORT}", "admin": f"127.0.0.1:{ADMIN_PORT}", "intercept": intercept}
    if mode == "env":
        listen["connect"] = {"issuer": "ci"}

    out_exporters = []
    for entry in exporters:
        entry = dict(entry)
        if "otlp" in entry:
            otlp = dict(entry["otlp"])
            # the exporter's own labels win over the extra labels, which win over ours
            otlp["resource"] = {**labels, **(extra_labels or {}), **(otlp.get("resource") or {})}
            entry["otlp"] = otlp
        out_exporters.append(entry)

    out_apis = {}
    for name, api in apis.items():
        api = dict(api)
        api.setdefault("checks", [])  # minimal: the route only, so bodies are never read
        if "spec" in api:
            api["spec"] = _abspath(api["spec"])
        if "overlays" in api:
            api["overlays"] = [_abspath(o) for o in api["overlays"]]
        out_apis[name] = api

    main = {"id": integration}
    if out_apis:
        main["apis"] = sorted(out_apis)
    if signing:
        main["fingerprintKey"] = {"env": "HOTLINE_SIGNING_KEY"}
    return {
        "listen": listen,
        "issuers": {"ci": {"cert": "/etc/hotline/ci/ca.crt", "key": "/etc/hotline/ci/ca.key"}},
        "exporters": out_exporters,
        "apis": out_apis,
        # plain calls sent here by the redirect must go out as plain
        "integrations": [main, {"id": "not-passed", "tlsOrigination": False}],
    }


def env_names(config):
    """Names of the variables the config reads, as { env: NAME }."""
    names = set()

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("env"), str):
                names.add(node["env"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    return sorted(names)
