"""Start of the job: runs the Hotline proxy and sends this job's outgoing
connections through it. Any problem leaves the job exactly as it was."""

import json
import os
import re
import shutil
import stat
import subprocess

from . import config as configuration
from . import image, region
from .common import (ADMIN_PORT, CA_FILE, CONTAINER, PROXY_PORT, PROXY_STOP_FLAGS, PROXY_UID, READYZ,
                     Fail, redirect_off, redirect_on, state_dir, untrust_ca, warn)


def _list(system, text, default):
    value = system.load_yaml(text or "")
    return default if value is None else value


def run_setup(env, system, root, log=print):
    mode = env.get("HOTLINE_IN_MODE") or "firewall"

    # ---- guards: say so once and do nothing ---------------------------
    if system.system_name() != "Linux":
        warn(log, "Hotline works on Linux runners only; nothing was changed.")
        return
    if not env.get("HOTLINE_IN_EXPORTERS", "").strip():
        warn(log, "No exporters were given (secrets are not available to pull requests "
                  "from forks); nothing was changed.")
        return
    for tool in ["docker", "openssl"] + (["iptables"] if mode == "firewall" else []):
        if not system.which(tool):
            warn(log, f"The tool '{tool}' is not installed on this runner; nothing was changed.")
            return
    if not system.has_yaml():
        warn(log, "The inputs are YAML: PyYAML or the yq program is needed; nothing was changed.")
        return

    state = state_dir(env)
    undo = []  # each step that changes the machine adds its own undo, run newest first
    step = ["start"]

    def at(name):
        step[0] = name

    try:
        shutil.rmtree(state, ignore_errors=True)
        (state / "ca").mkdir(parents=True)
        (state / "config").mkdir()
        for folder in (state, state / "ca", state / "config"):
            folder.chmod(0o755)
        undo.append(lambda: shutil.rmtree(state, ignore_errors=True))

        # ---- inputs ---------------------------------------------------
        at("reading the inputs")
        exporters = _list(system, env["HOTLINE_IN_EXPORTERS"], [])
        apis = _list(system, env.get("HOTLINE_IN_APIS"), {})
        extra_labels = _list(system, env.get("HOTLINE_IN_LABELS"), {})
        passthrough = _list(system, env.get("HOTLINE_IN_PASSTHROUGH"), [])
        ech_relay = _list(system, env.get("HOTLINE_IN_ECH_RELAY"), [])
        tls_ports = _list(system, env.get("HOTLINE_IN_TLS_PORTS"), [443])
        http_ports = _list(system, env.get("HOTLINE_IN_HTTP_PORTS"), [80])
        if mode not in ("firewall", "env"):
            raise Fail("mode must be firewall or env")
        if not isinstance(exporters, list) or not isinstance(apis, dict):
            raise Fail("exporters must be a list and apis a map")
        integration = env.get("HOTLINE_IN_INTEGRATION") or (
            f"ci-{env.get('GITHUB_REPOSITORY_OWNER', 'owner')}_"
            f"{env.get('GITHUB_REPOSITORY', 'owner/repo').split('/', 1)[-1]}__"
            f"{env.get('GITHUB_WORKFLOW', 'workflow')}")
        integration = re.sub(r"[^A-Za-z0-9_.-]", "_", integration)

        # ---- the image ------------------------------------------------
        at("choosing the image")
        ref, overridden = image.resolve(env.get("HOTLINE_IN_IMAGE", ""), root)
        if overridden:
            warn(log, f"The 'image' input is set ({ref}): this image is not pinned by "
                      "digest and its signature is not checked.")
        at("pulling the image")
        system.run("docker", "pull", ref)
        digest = system.run("docker", "image", "inspect", "--format",
                            "{{index .RepoDigests 0}}", ref, capture=True).strip()

        # ---- where the runner is, before the redirect so none of it is recorded
        at("looking up the region")
        found_region, public_ip = region.lookup(
            system, env.get("HOTLINE_IN_REGION") or "auto",
            env.get("HOTLINE_IN_REGION_LOOKUP", ""), env.get("RUNNER_ENVIRONMENT", ""))

        # ---- the issuer: a new one for this job, valid one day --------
        at("making the issuer")
        key, crt = state / "ca" / "ca.key", state / "ca" / "ca.crt"
        old_umask = os.umask(0o077)
        try:
            system.run("openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt",
                       "ec_paramgen_curve:P-256", "-nodes", "-days", "1",
                       "-subj", "/CN=Hotline CI issuer",
                       "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                       "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                       "-keyout", str(key), "-out", str(crt))
        finally:
            os.umask(old_umask)
        crt.chmod(0o644)
        # only the proxy's own user can read the key; the job's steps cannot
        system.run("sudo", "chown", str(PROXY_UID), str(key))
        system.run("sudo", "chmod", "400", str(key))

        # ---- the config -----------------------------------------------
        at("writing the config")
        signing = bool(env.get("HOTLINE_IN_SIGNING_KEY"))
        config = configuration.build(
            exporters=exporters, apis=apis,
            labels=configuration.base_labels(env, found_region, public_ip, digest),
            extra_labels=extra_labels, passthrough=passthrough, ech_relay=ech_relay,
            integration=integration, mode=mode, signing=signing)
        # JSON is valid YAML
        (state / "config" / "hotline-proxy.yaml").write_text(json.dumps(config, indent=2))

        # ---- run the proxy --------------------------------------------
        at("starting the proxy")
        env_args = [arg for name in configuration.env_names(config) for arg in ("-e", name)]
        system.try_run("docker", "rm", "-f", CONTAINER)
        system.run(
            "docker", "run", "-d", "--name", CONTAINER, "--network", "host",
            "--user", f"{PROXY_UID}:{PROXY_UID}", "--read-only", "--cap-drop", "ALL",
            "-v", f"{state}/config:/etc/hotline/config:ro", "-v", f"{state}/ca:/etc/hotline/ci:ro",
            "-v", f"{env.get('GITHUB_WORKSPACE') or os.getcwd()}:/workspace:ro",
            *env_args, ref, "run", "--config", "/etc/hotline/config/hotline-proxy.yaml", *PROXY_STOP_FLAGS,
            env={"HOTLINE_SIGNING_KEY": env.get("HOTLINE_IN_SIGNING_KEY", "")} if signing else None)
        undo.append(lambda: system.try_run("docker", "rm", "-f", CONTAINER))

        at("waiting for the proxy to be ready")
        for _ in range(30):
            if system.http_get(READYZ) is not None:
                break
            system.sleep(1)
        else:
            raise Fail("the proxy did not become ready")

        # ---- trust the issuer -----------------------------------------
        at("trusting the issuer")
        system.run("sudo", "cp", str(crt), CA_FILE)
        undo.append(lambda: untrust_ca(system))
        system.run("sudo", "update-ca-certificates")

        # ---- the redirect, last ---------------------------------------
        at("switching on the redirect")
        if mode == "firewall":
            undo.append(lambda: redirect_off(system))
            redirect_on(system, tls_ports, http_ports)

        # ---- done: only now does the rest of the job see any change ----
        at("finishing")
        (state / "state.json").write_text(json.dumps({
            "mode": mode, "integration": integration, "image_ref": ref,
            "image_digest": digest, "overridden": overridden, "region": found_region,
        }))
        if env.get("GITHUB_ENV"):
            lines = [f"HOTLINE_CA_CERT={crt}", f"NODE_EXTRA_CA_CERTS={crt}"]
            if mode == "env":
                proxy = f"http://{integration}:x@127.0.0.1:{PROXY_PORT}"
                lines += [f"HTTPS_PROXY={proxy}", f"HTTP_PROXY={proxy}", "NO_PROXY=localhost,127.0.0.1"]
            with open(env["GITHUB_ENV"], "a") as handle:
                handle.write("\n".join(lines) + "\n")
        if env.get("GITHUB_OUTPUT"):
            with open(env["GITHUB_OUTPUT"], "a") as handle:
                handle.write(f"ca-cert={crt}\n")
        if mode == "firewall":
            system.start_watchdog(env)
        log(f"hotline: recording this job's calls with {digest}")
    except Exception as error:  # fail open: a recording tool must never fail a build
        detail = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) and error.stderr else str(error)
        warn(log, f"Setup stopped at the step '{step[0]}'"
                  f"{': ' + detail.splitlines()[-1] if detail else ''}; the job carries on without Hotline.")
        for action in reversed(undo):
            try:
                action()
            except Exception:
                pass
        shutil.rmtree(state, ignore_errors=True)
