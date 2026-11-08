"""A fake machine: it records every command, answers web requests from a
table, and fails the commands a test names. Nothing real is touched, except
openssl, which makes a real certificate so tests can look at it."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from hotline_action.common import ADMIN_PORT, System

D = "sha256:"
PIN = f"ghcr.io/petercipov/hotline-proxy@{D}{'a' * 64}"
PULLED = f"ghcr.io/petercipov/hotline-proxy@{D}{'b' * 64}"
LOOKED_UP = f"{D}{'c' * 64}"
RELEASE_URL = "https://api.github.com/repos/petercipov/hotline/releases/latest"
READYZ = f"http://127.0.0.1:{ADMIN_PORT}/readyz"


class FakeSystem(System):
    def __init__(self):
        self.calls = []
        self.fail = []  # a command starting with any of these fails
        self.missing = set()
        self.no_yaml = False
        self.os = "Linux"
        self.web = {READYZ: "ok"}
        self.watchdog_started = False

    def system_name(self):
        return self.os

    def which(self, tool):
        return tool not in self.missing

    def has_yaml(self):
        return not self.no_yaml

    def run(self, *args, capture=False, env=None):
        line = " ".join(args)
        self.calls.append(line)
        plain = line[5:] if line.startswith("sudo ") else line
        if any(plain.startswith(prefix) for prefix in self.fail):
            raise subprocess.CalledProcessError(1, args, stderr="made to fail")
        if args[0] == "openssl":
            return subprocess.run(args, check=True, capture_output=True, text=True).stdout
        if plain.startswith("docker image inspect"):
            return PULLED + "\n"
        if plain.startswith("docker inspect"):
            return "0\n"
        if plain.startswith("docker buildx imagetools"):
            return json.dumps({"digest": LOOKED_UP})
        return ""

    def http_get(self, url, headers=None, method="GET"):
        self.calls.append(f"http {url}")
        return self.web.get(url)

    def sleep(self, seconds):
        pass

    def start_watchdog(self, env):
        self.watchdog_started = True


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.system = FakeSystem()
        self.root = self.tmp / "root"
        self.root.mkdir()
        (self.root / "proxy-image.txt").write_text(PIN + "\n")
        self.out = []
        self.env = {
            "HOTLINE_CI_STATE_DIR": str(self.tmp / "state"),
            "GITHUB_ENV": str(self.tmp / "github_env"),
            "GITHUB_OUTPUT": str(self.tmp / "github_output"),
            "GITHUB_STEP_SUMMARY": str(self.tmp / "summary"),
            "GITHUB_WORKSPACE": str(self.tmp / "workspace"),
            "GITHUB_REPOSITORY": "acme/shop", "GITHUB_REPOSITORY_OWNER": "acme",
            "GITHUB_WORKFLOW": "CI build", "GITHUB_JOB": "lint", "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "main",
            "RUNNER_NAME": "runner-1", "RUNNER_ENVIRONMENT": "github-hosted",
            "HOTLINE_IN_EXPORTERS": '- otlp: { endpoint: "https://otel.example.com:4318", '
                                    'headers: { Authorization: { env: OTLP_TOKEN } } }',
        }

    # -- running --
    def setup_job(self, **extra):
        from hotline_action.setup_job import run_setup
        run_setup(self._env(extra), self.system, self.root, log=self.out.append)

    def teardown_job(self, **extra):
        from hotline_action.teardown import run_teardown
        run_teardown(self._env(extra), self.system, self.root, log=self.out.append)

    def watchdog(self, **extra):
        from hotline_action.watchdog import run_watchdog
        run_watchdog(self._env(extra), self.system, self.root, log=self.out.append)

    def _env(self, extra):
        env = {**self.env, **extra}
        return {k: v for k, v in env.items() if v is not None}

    # -- looking --
    @property
    def output(self):
        return "\n".join(self.out)

    @property
    def state(self):
        return Path(self.env["HOTLINE_CI_STATE_DIR"])

    def config(self):
        return json.loads((self.state / "config" / "hotline-proxy.yaml").read_text())

    def resource(self, index=0):
        return self.config()["exporters"][index]["otlp"]["resource"]

    def read(self, name):
        path = Path(self.env[name])
        return path.read_text() if path.exists() else ""

    def index(self, prefix, last=False):
        """Position of the first (or last) command containing prefix; fails if none."""
        found = [i for i, c in enumerate(self.system.calls) if prefix in c]
        self.assertTrue(found, f"no call contains {prefix!r}: {self.system.calls}")
        return found[-1] if last else found[0]

    def called(self, prefix):
        return any(prefix in c for c in self.system.calls)

    def assertNotCalled(self, prefix):
        self.assertFalse(self.called(prefix), f"{prefix!r} was called: {self.system.calls}")
