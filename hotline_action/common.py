"""Constants and the few things that touch the machine, behind one class so
tests can replace it with a fake."""

import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

IMAGE_REPO = "ghcr.io/petercipov/hotline-proxy"
CONTAINER = "hotline-ci-proxy"
PROXY_UID = 64321  # the proxy's own numeric user; the firewall leaves it out
PROXY_PORT = 18080
ADMIN_PORT = 18081
INTERCEPT_PORT = 18444
CA_FILE = "/usr/local/share/ca-certificates/hotline-ci.crt"
READYZ = f"http://127.0.0.1:{ADMIN_PORT}/readyz"


class Fail(Exception):
    """A step failed; setup undoes what it did and the job carries on."""


def warn(log, message):
    """A warning shows on the job's page and never fails the job."""
    log(f"::warning title=Hotline::{message}")


def state_dir(env):
    if env.get("HOTLINE_CI_STATE_DIR"):
        return Path(env["HOTLINE_CI_STATE_DIR"])
    return Path(env.get("RUNNER_TEMP", "/tmp")) / "hotline-ci"


def load_state(env):
    try:
        return json.loads((state_dir(env) / "state.json").read_text())
    except (OSError, ValueError):
        return None


class System:
    """The real machine."""

    def system_name(self):
        import platform

        return platform.system()

    def which(self, tool):
        return shutil.which(tool) is not None

    def run(self, *args, capture=False, env=None):
        result = subprocess.run(
            args,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={**os.environ, **env} if env else None,
        )
        return result.stdout if capture else ""

    def try_run(self, *args):
        """Best effort: undoing something must never stop the rest of the undo."""
        try:
            self.run(*args)
            return True
        except Exception:
            return False

    def http_get(self, url, headers=None, method="GET"):
        """The answer as text, or None for any failure, including an error status."""
        request = urllib.request.Request(url, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=3) as reply:
                return reply.read().decode("utf-8", "replace")
        except Exception:
            return None

    def sleep(self, seconds):
        import time

        time.sleep(seconds)

    def has_yaml(self):
        try:
            import yaml  # noqa: F401

            return True
        except ImportError:
            return shutil.which("yq") is not None

    def load_yaml(self, text):
        """YAML text as Python data. PyYAML when there, else the yq program."""
        if not text.strip():
            return None
        try:
            import yaml

            return yaml.safe_load(text)
        except ImportError:
            pass
        version = subprocess.run(["yq", "--version"], capture_output=True, text=True).stdout
        command = ["yq", "-o=json", "."] if "mikefarah" in version else ["yq", "."]
        result = subprocess.run(command, input=text, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def start_watchdog(self, env):
        """Starts the watchdog so it outlives the setup step. The runner kills
        what a step leaves running unless it carries another tracking id."""
        import sys

        run_py = Path(__file__).resolve().parent.parent / "run.py"
        subprocess.Popen(
            [sys.executable, str(run_py), "watchdog"],
            env={**env, "RUNNER_TRACKING_ID": ""},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


# Ranges the redirect never touches: not public addresses.
NON_PUBLIC = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/3",
]


def redirect_on(system, tls_ports, http_ports):
    """Every outgoing TCP connection to a public address on a chosen port goes
    to the proxy, except the proxy's own."""
    nat = ["sudo", "iptables", "-t", "nat"]
    system.run(*nat, "-N", "HOTLINE_CI")
    system.run(*nat, "-A", "HOTLINE_CI", "-m", "owner", "--uid-owner", str(PROXY_UID), "-j", "RETURN")
    for address_range in NON_PUBLIC:
        system.run(*nat, "-A", "HOTLINE_CI", "-d", address_range, "-j", "RETURN")
    for ports, target in ((tls_ports, INTERCEPT_PORT), (http_ports, PROXY_PORT)):
        for port in ports:
            system.run(*nat, "-A", "HOTLINE_CI", "-p", "tcp", "--dport", str(port),
                       "-j", "REDIRECT", "--to-ports", str(target))
    system.run(*nat, "-A", "OUTPUT", "-p", "tcp", "-j", "HOTLINE_CI")


def redirect_off(system):
    nat = ["sudo", "iptables", "-t", "nat"]
    system.try_run(*nat, "-D", "OUTPUT", "-p", "tcp", "-j", "HOTLINE_CI")
    system.try_run(*nat, "-F", "HOTLINE_CI")
    system.try_run(*nat, "-X", "HOTLINE_CI")


def untrust_ca(system):
    system.try_run("sudo", "rm", "-f", CA_FILE)
    system.try_run("sudo", "update-ca-certificates")
