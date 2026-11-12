import subprocess
import unittest

from test.helpers import LOOKED_UP, PIN, PULLED, Base


class Guards(Base):
    def test_does_nothing_with_one_warning_on_a_system_that_is_not_linux(self):
        self.system.os = "Darwin"
        self.setup_job()
        self.assertIn("Linux runners only", self.output)
        self.assertEqual(self.system.calls, [])
        self.assertFalse(self.state.exists())

    def test_does_nothing_with_one_warning_when_there_are_no_exporters(self):
        self.setup_job(HOTLINE_IN_EXPORTERS="")
        self.assertIn("forks", self.output)
        self.assertEqual(self.system.calls, [])

    def test_warns_naming_the_tool_and_does_nothing_when_a_tool_is_missing(self):
        for tool in ("docker", "openssl", "iptables"):
            self.out.clear()
            self.system.missing = {tool}
            self.setup_job()
            self.assertIn(f"'{tool}' is not installed", self.output)
        self.assertEqual(self.system.calls, [])

    def test_env_mode_needs_no_iptables(self):
        self.system.missing = {"iptables"}
        self.setup_job(HOTLINE_IN_MODE="env")
        self.assertIn("HTTPS_PROXY=", self.read("GITHUB_ENV"))

    def test_warns_when_there_is_no_way_to_read_yaml(self):
        self.system.no_yaml = True
        self.setup_job()
        self.assertIn("PyYAML", self.output)
        self.assertEqual(self.system.calls, [])


class Image(Base):
    def test_uses_the_digest_in_proxy_image_txt_when_image_is_empty(self):
        self.setup_job()
        self.assertIn(f"docker pull {PIN}", self.system.calls)
        self.assertNotIn("not pinned", self.output)

    def test_ignores_comments_and_a_prerelease_note(self):
        (self.root / "proxy-image.txt").write_text(f"# a comment\n{PIN}  # prerelease\n")
        self.setup_job()
        self.assertIn(f"docker pull {PIN}", self.system.calls)

    def test_reads_the_word_edge_as_the_edge_tag(self):
        self.setup_job(HOTLINE_IN_IMAGE="edge")
        self.assertIn("docker pull ghcr.io/hotlinehq/hotline-proxy:edge", self.system.calls)

    def test_uses_a_full_reference_and_warns_once_that_it_is_not_pinned(self):
        self.setup_job(HOTLINE_IN_IMAGE="ghcr.io/hotlinehq/hotline-proxy:sha-abc")
        self.assertIn("docker pull ghcr.io/hotlinehq/hotline-proxy:sha-abc", self.system.calls)
        self.assertEqual(self.output.count("not pinned"), 1)

    def test_records_the_digest_actually_pulled_on_every_exporter(self):
        self.setup_job(HOTLINE_IN_IMAGE="edge")
        self.assertEqual(self.resource()["hotline.proxy.image"], PULLED)

    def test_does_not_fall_back_to_the_pin_when_the_override_pull_fails(self):
        self.system.fail = ["docker pull"]
        self.setup_job(HOTLINE_IN_IMAGE="edge")
        self.assertIn("pulling the image", self.output)
        self.assertEqual([c for c in self.system.calls if c.startswith("docker pull")],
                         ["docker pull ghcr.io/hotlinehq/hotline-proxy:edge"])
        self.assertNotCalled("iptables")

    def test_fails_open_when_nothing_is_pinned_and_no_image_is_set(self):
        (self.root / "proxy-image.txt").write_text("# nothing\n")
        self.setup_job()
        self.assertIn("choosing the image", self.output)
        self.assertNotCalled("docker")


class Config(Base):
    def test_gives_an_api_with_no_checks_the_list_empty_and_leaves_a_list_alone(self):
        self.setup_job(HOTLINE_IN_APIS=(
            "stripe:\n  hosts: [api.stripe.com]\n  spec: ./specs/stripe.yaml\n"
            "talon:\n  hosts: [api.talon.one]\n  spec: /abs/talon.yaml\n  checks: [path, body]"))
        apis = self.config()["apis"]
        self.assertEqual(apis["stripe"]["checks"], [])
        self.assertEqual(apis["talon"]["checks"], ["path", "body"])

    def test_reads_spec_files_from_the_workspace_mounted_in_the_container(self):
        self.setup_job(HOTLINE_IN_APIS="stripe:\n  hosts: [a]\n  spec: ./specs/stripe.yaml\n"
                                        "  overlays: [o.yaml]")
        api = self.config()["apis"]["stripe"]
        self.assertEqual(api["spec"], "/workspace/specs/stripe.yaml")
        self.assertEqual(api["overlays"], ["/workspace/o.yaml"])
        run = next(c for c in self.system.calls if c.startswith("docker run"))
        self.assertIn(f"{self.env['GITHUB_WORKSPACE']}:/workspace:ro", run)

    def test_writes_the_default_integration_from_owner_repository_and_workflow(self):
        self.setup_job()
        self.assertEqual(self.config()["integrations"][0]["id"], "ci-acme_shop__CI_build")
        self.assertEqual(self.config()["listen"]["intercept"]["integration"], "ci-acme_shop__CI_build")

    def test_uses_the_integration_input_when_given(self):
        self.setup_job(HOTLINE_IN_INTEGRATION="my-ci")
        self.assertEqual(self.config()["integrations"][0]["id"], "my-ci")

    def test_lists_the_apis_on_the_integration_and_sends_plain_calls_out_as_plain(self):
        self.setup_job(HOTLINE_IN_APIS="stripe:\n  hosts: [a]\n  spec: ./s.yaml")
        integrations = self.config()["integrations"]
        self.assertEqual(integrations[0]["apis"], ["stripe"])
        not_passed = next(i for i in integrations if i["id"] == "not-passed")
        self.assertIs(not_passed["tlsOrigination"], False)

    def test_keeps_secrets_as_env_references_and_never_writes_the_value(self):
        self.setup_job(OTLP_TOKEN="s3cret")
        self.assertEqual(self.config()["exporters"][0]["otlp"]["headers"]["Authorization"],
                         {"env": "OTLP_TOKEN"})
        self.assertTrue(any("-e OTLP_TOKEN" in c for c in self.system.calls))
        self.assertNotIn("s3cret", " ".join(self.system.calls))
        self.assertNotIn("s3cret", (self.state / "config" / "hotline-proxy.yaml").read_text())

    def test_signs_records_with_the_signing_key_by_name_and_leaves_it_out_when_none(self):
        self.setup_job(HOTLINE_IN_SIGNING_KEY="k")
        self.assertEqual(self.config()["integrations"][0]["fingerprintKey"], {"env": "HOTLINE_SIGNING_KEY"})
        self.assertTrue(any("-e HOTLINE_SIGNING_KEY" in c for c in self.system.calls))
        self.setup_job()
        self.assertNotIn("fingerprintKey", self.config()["integrations"][0])

    def test_records_the_address_of_minimal_records_only_when_asked(self):
        self.setup_job()
        self.assertNotIn("urlFull", self.config()["integrations"][1])
        self.setup_job(HOTLINE_IN_RECORD_URLS="false")
        self.assertNotIn("urlFull", self.config()["integrations"][1])
        self.setup_job(HOTLINE_IN_RECORD_URLS="true")
        not_passed = self.config()["integrations"][1]
        self.assertEqual(not_passed["id"], "not-passed")
        self.assertIs(not_passed["urlFull"], True)

    def test_puts_the_labels_in_every_exporter_extra_labels_and_the_exporters_own_winning(self):
        self.setup_job(
            HOTLINE_IN_EXPORTERS='- otlp: { endpoint: "http://a:4318" }\n'
                                 '- otlp: { endpoint: "http://b:4318", resource: { service.name: mine } }',
            HOTLINE_IN_LABELS="{team: infra, vcs.ref.head.name: custom}")
        for index in (0, 1):
            r = self.resource(index)
            self.assertEqual(r["cicd.pipeline.name"], "CI build")
            self.assertEqual(r["cicd.pipeline.task.name"], "lint")
            self.assertEqual(r["cicd.pipeline.run.id"], "42")
            self.assertEqual(r["vcs.repository.name"], "acme/shop")
            self.assertEqual(r["hotline.ci.run_attempt"], "1")
            self.assertEqual(r["hotline.ci.event"], "push")
            self.assertEqual(r["host.name"], "runner-1")
            self.assertEqual(r["hotline.runner.environment"], "github-hosted")
            self.assertEqual(r["team"], "infra")
            self.assertEqual(r["vcs.ref.head.name"], "custom")
        self.assertEqual(self.resource(1)["service.name"], "mine")

    def test_passes_passthrough_and_ech_relay_and_leaves_ech_out_when_empty(self):
        self.setup_job()
        self.assertNotIn("ech", self.config()["listen"]["intercept"])
        self.setup_job(HOTLINE_IN_PASSTHROUGH="[pinned.example.com]",
                       HOTLINE_IN_ECH_RELAY="[outer.example.net]")
        intercept = self.config()["listen"]["intercept"]
        self.assertIn("pinned.example.com", intercept["passthrough"])
        self.assertEqual(intercept["ech"], {"relay": ["outer.example.net"]})


class RunnerAndShutdown(Base):
    def test_always_relays_the_runners_own_github_hosts_untouched(self):
        self.setup_job()
        self.assertEqual(self.config()["listen"]["intercept"]["passthrough"], ["*.githubapp.com"])

    def test_adds_the_passthrough_input_to_the_default_once(self):
        self.setup_job(HOTLINE_IN_PASSTHROUGH="[pinned.example.com, '*.githubapp.com']")
        self.assertEqual(self.config()["listen"]["intercept"]["passthrough"],
                         ["*.githubapp.com", "pinned.example.com"])

    def test_the_proxy_stops_at_once_so_the_last_records_are_sent(self):
        self.setup_job()
        run = next(c for c in self.system.calls if c.startswith("docker run"))
        self.assertIn("--drain-delay 0s --shutdown-timeout 10s", run)

    def test_labels_a_pull_request_with_its_branch_not_the_merge_ref(self):
        self.setup_job(GITHUB_REF_NAME="119/merge", GITHUB_HEAD_REF="claude/trial")
        self.assertEqual(self.resource()["vcs.ref.head.name"], "claude/trial")
        self.setup_job(GITHUB_REF_NAME="main")
        self.assertEqual(self.resource()["vcs.ref.head.name"], "main")


class Issuer(Base):
    def test_makes_an_ecdsa_p256_certificate_named_hotline_ci_issuer_valid_for_a_day(self):
        self.setup_job()
        crt = str(self.state / "ca" / "ca.crt")
        text = subprocess.run(["openssl", "x509", "-in", crt, "-noout", "-text"],
                              capture_output=True, text=True, check=True).stdout
        self.assertIn("prime256v1", text)
        self.assertRegex(text, r"CN ?= ?Hotline CI issuer")
        self.assertIn("CA:TRUE", text)

        def lasts(seconds):
            return subprocess.run(["openssl", "x509", "-in", crt, "-noout", "-checkend", str(seconds)],
                                  capture_output=True).returncode == 0
        self.assertTrue(lasts(80000))
        self.assertFalse(lasts(90000))

    def test_keeps_the_key_private_and_out_of_the_environment_file(self):
        self.setup_job()
        self.assertTrue(self.called("sudo chown 64321 "))
        self.assertTrue(self.called("sudo chmod 400 "))
        self.assertNotIn("ca.key", self.read("GITHUB_ENV") + self.read("GITHUB_OUTPUT"))
        mode = (self.state / "ca" / "ca.key").stat().st_mode & 0o777
        self.assertEqual(mode & 0o077, 0)

    def test_hands_the_certificate_over_by_path(self):
        self.setup_job()
        crt = self.state / "ca" / "ca.crt"
        self.assertIn(f"ca-cert={crt}", self.read("GITHUB_OUTPUT"))
        self.assertIn(f"HOTLINE_CA_CERT={crt}", self.read("GITHUB_ENV"))
        self.assertIn("NODE_EXTRA_CA_CERTS=", self.read("GITHUB_ENV"))


AZURE = "http://169.254.169.254/metadata/instance/compute/location?api-version=2021-02-01&format=text"
AWS_TOKEN = "http://169.254.169.254/latest/api/token"
AWS_REGION = "http://169.254.169.254/latest/meta-data/placement/region"
GOOGLE = "http://metadata.google.internal/computeMetadata/v1/instance/zone"
TRACE = "https://1.1.1.1/cdn-cgi/trace"


class Region(Base):
    def test_uses_a_fixed_region_before_any_lookup(self):
        self.setup_job(HOTLINE_IN_REGION="eu-west-1")
        self.assertEqual(self.resource()["cloud.region"], "eu-west-1")
        self.assertNotCalled("169.254.169.254")

    def test_tries_azure_then_aws_then_google_and_stops_at_the_first_answer(self):
        self.system.web.update({AWS_TOKEN: "tok", AWS_REGION: "eu-central-1",
                                GOOGLE: "projects/1/zones/europe-west1-b"})
        self.setup_job()
        self.assertEqual(self.resource()["cloud.region"], "eu-central-1")
        self.assertNotCalled("metadata.google.internal")
        self.system.web[AZURE] = "westeurope"
        self.setup_job()
        self.assertEqual(self.resource()["cloud.region"], "westeurope")

    def test_turns_a_google_zone_into_a_region(self):
        self.system.web[GOOGLE] = "projects/1/zones/europe-west1-b"
        self.setup_job()
        self.assertEqual(self.resource()["cloud.region"], "europe-west1")

    def test_on_githubs_runners_the_public_address_and_cloudflare_site_come_from_the_trace(self):
        self.system.web[TRACE] = "fl=1\nip=203.0.113.9\ncolo=AMS\n"
        self.setup_job()
        self.assertEqual(self.resource()["hotline.runner.public_ip"], "203.0.113.9")
        self.assertEqual(self.resource()["cloud.region"], "AMS")

    def test_metadata_wins_for_the_region_but_the_address_is_still_labelled(self):
        self.system.web.update({AZURE: "westeurope", TRACE: "ip=203.0.113.9\ncolo=AMS\n"})
        self.setup_job()
        self.assertEqual(self.resource()["cloud.region"], "westeurope")
        self.assertEqual(self.resource()["hotline.runner.public_ip"], "203.0.113.9")

    def test_no_public_address_lookup_on_a_self_hosted_runner_unless_asked(self):
        self.system.web[TRACE] = "ip=203.0.113.9\ncolo=AMS\n"
        self.setup_job(RUNNER_ENVIRONMENT="self-hosted")
        self.assertNotCalled("1.1.1.1")
        self.assertNotIn("hotline.runner.public_ip", self.resource())
        self.setup_job(RUNNER_ENVIRONMENT="self-hosted", HOTLINE_IN_REGION_LOOKUP="public-ip")
        self.assertTrue(self.called("1.1.1.1"))

    def test_region_off_does_no_lookup_and_no_region_means_no_label(self):
        self.setup_job(HOTLINE_IN_REGION="off")
        self.assertNotCalled("169.254.169.254")
        self.assertNotCalled("1.1.1.1")
        self.assertTrue(self.state.exists())
        self.setup_job(HOTLINE_IN_REGION="auto", RUNNER_ENVIRONMENT="self-hosted")
        self.assertNotIn("cloud.region", self.resource())
        self.assertTrue(self.state.exists())


class OrderAndModes(Base):
    def test_switches_the_redirect_on_last(self):
        self.setup_job()
        run = self.index("docker run")
        ready = self.index("/readyz")
        trust = self.index("update-ca-certificates")
        nat = self.index("-A OUTPUT")
        self.assertLess(run, ready)
        self.assertLess(ready, trust)
        self.assertLess(trust, nat)
        self.assertEqual(nat, len(self.system.calls) - 1)

    def test_does_every_lookup_before_the_redirect(self):
        self.system.web[TRACE] = "ip=203.0.113.9\ncolo=AMS\n"
        self.setup_job()
        self.assertLess(self.index("http ", last=True), self.index("-A OUTPUT"))

    def test_runs_the_proxy_on_the_host_network_under_its_own_user_read_only(self):
        self.setup_job()
        run = next(c for c in self.system.calls if c.startswith("docker run"))
        for part in ("--network host", "--user 64321:64321", "--read-only", "--cap-drop ALL"):
            self.assertIn(part, run)

    def test_firewall_mode_sends_tls_ports_to_the_intercept_port_and_http_ports_to_the_proxy(self):
        self.setup_job(HOTLINE_IN_TLS_PORTS="[443, 8443]", HOTLINE_IN_HTTP_PORTS="[80]")
        self.assertTrue(self.called("--dport 443 -j REDIRECT --to-ports 18444"))
        self.assertTrue(self.called("--dport 8443 -j REDIRECT --to-ports 18444"))
        self.assertTrue(self.called("--dport 80 -j REDIRECT --to-ports 18080"))
        self.assertNotCalled("--dport 22 ")

    def test_firewall_mode_leaves_the_proxys_own_user_and_non_public_addresses_out(self):
        self.setup_job()
        self.assertTrue(self.called("--uid-owner 64321 -j RETURN"))
        for address_range in ("10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16"):
            self.assertTrue(self.called(f"-d {address_range} -j RETURN"))
        self.assertLess(self.index("--uid-owner"), self.index("--dport"))

    def test_env_mode_sets_the_proxy_variables_names_the_issuer_and_adds_no_rule(self):
        self.setup_job(HOTLINE_IN_MODE="env")
        self.assertNotCalled("iptables")
        env_file = self.read("GITHUB_ENV")
        self.assertIn("HTTPS_PROXY=http://ci-acme_shop__CI_build:x@127.0.0.1:18080", env_file)
        self.assertIn("HTTP_PROXY=", env_file)
        self.assertEqual(self.config()["listen"]["connect"], {"issuer": "ci"})

    def test_a_mode_that_is_neither_firewall_nor_env_fails_open(self):
        self.setup_job(HOTLINE_IN_MODE="banana")
        self.assertNotCalled("docker run")

    def test_starts_the_watchdog_in_firewall_mode_only(self):
        self.setup_job()
        self.assertTrue(self.system.watchdog_started)
        self.system.watchdog_started = False
        self.setup_job(HOTLINE_IN_MODE="env")
        self.assertFalse(self.system.watchdog_started)

    def test_changes_nothing_the_job_can_see_until_every_step_has_worked(self):
        self.system.fail = ["iptables"]
        self.setup_job()
        self.assertEqual(self.read("GITHUB_ENV"), "")
        self.assertEqual(self.read("GITHUB_OUTPUT"), "")
        self.assertFalse(self.system.watchdog_started)


class FailOpen(Base):
    def check(self, step):
        self.assertIn("::warning", self.output)
        self.assertIn(step, self.output)
        self.assertEqual(self.read("GITHUB_ENV"), "")
        self.assertFalse(self.state.exists())

    def test_the_pull_fails(self):
        self.system.fail = ["docker pull"]
        self.setup_job()
        self.check("pulling the image")

    def test_the_inputs_cannot_be_read(self):
        class Broken(type(self.system)):
            def load_yaml(self, text):
                raise ValueError("bad yaml")
        broken = Broken()
        self.system = broken
        self.setup_job()
        self.check("reading the inputs")

    def test_the_proxy_does_not_start_and_no_rule_is_added(self):
        self.system.fail = ["docker run"]
        self.setup_job()
        self.check("starting the proxy")
        self.assertNotCalled("-A OUTPUT")

    def test_the_proxy_never_becomes_ready_and_its_container_is_removed(self):
        del self.system.web[next(iter(self.system.web))]
        self.setup_job()
        self.check("waiting for the proxy to be ready")
        self.assertTrue(self.called("docker rm -f hotline-ci-proxy"))
        self.assertNotCalled("-A OUTPUT")

    def test_trusting_the_issuer_fails_the_container_is_removed_and_no_rule_is_added(self):
        self.system.fail = ["update-ca-certificates"]
        self.setup_job()
        self.check("trusting the issuer")
        self.assertNotCalled("-A OUTPUT")
        self.assertTrue(self.called("sudo rm -f /usr/local/share/ca-certificates/hotline-ci.crt"))
        self.assertGreater(self.index("docker rm -f", last=True), self.index("sudo cp"))

    def test_the_redirect_fails_and_everything_done_is_undone_newest_first(self):
        self.system.fail = ["iptables -t nat -A OUTPUT"]
        self.setup_job()
        self.check("switching on the redirect")
        self.assertTrue(self.called("-D OUTPUT"))
        self.assertLess(self.index("sudo rm -f /usr/local", last=True),
                        self.index("docker rm -f hotline-ci-proxy", last=True))

    def test_the_warning_says_why(self):
        self.system.fail = ["docker pull"]
        self.setup_job()
        self.assertIn("made to fail", self.output)


if __name__ == "__main__":
    unittest.main()
