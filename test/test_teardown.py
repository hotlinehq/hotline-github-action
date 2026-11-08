import unittest

from test.helpers import READYZ, Base


class Teardown(Base):
    def test_does_nothing_quietly_when_setup_did_nothing(self):
        self.teardown_job()
        self.assertEqual(self.output, "")
        self.assertEqual(self.system.calls, [])

    def test_stops_the_proxy_so_waiting_records_are_sent_after_taking_the_redirect_out(self):
        self.setup_job()
        self.system.calls.clear()
        self.teardown_job()
        self.assertIn("docker stop -t 20 hotline-ci-proxy", self.system.calls)
        self.assertLess(self.index("-D OUTPUT"), self.index("docker stop"))
        self.assertFalse(self.state.exists())

    def test_still_removes_the_rule_and_the_trusted_issuer_when_the_proxy_was_already_gone(self):
        self.setup_job()
        self.system.calls.clear()
        self.system.fail = ["docker"]
        self.teardown_job()
        self.assertTrue(self.called("-F HOTLINE_CI"))
        self.assertTrue(self.called("sudo rm -f /usr/local/share/ca-certificates/hotline-ci.crt"))
        self.assertIn("unknown", self.read("GITHUB_STEP_SUMMARY"))

    def test_in_env_mode_there_is_no_rule_to_remove(self):
        self.setup_job(HOTLINE_IN_MODE="env")
        self.system.calls.clear()
        self.teardown_job()
        self.assertNotCalled("iptables")
        self.assertTrue(self.called("docker stop"))

    def test_the_summary_names_mode_image_and_region_and_says_when_the_image_is_not_pinned(self):
        self.setup_job(HOTLINE_IN_IMAGE="edge", HOTLINE_IN_REGION="eu-west-1")
        self.teardown_job()
        summary = self.read("GITHUB_STEP_SUMMARY")
        self.assertIn("### Hotline", summary)
        self.assertIn("sha256:bbbb", summary)
        self.assertIn("not pinned", summary)
        self.assertIn("eu-west-1", summary)

    def test_the_summary_says_pinned_when_the_pin_file_was_used(self):
        self.setup_job()
        self.teardown_job()
        self.assertIn("(pinned)", self.read("GITHUB_STEP_SUMMARY"))


class Watchdog(Base):
    def test_removes_the_redirect_when_the_proxy_stops_after_two_failed_checks(self):
        self.setup_job()
        self.system.calls.clear()
        self.system.web.pop(READYZ)
        self.watchdog(HOTLINE_CI_WATCHDOG_TICKS="5")
        self.assertTrue(self.called("-D OUTPUT"))
        self.assertEqual(len([c for c in self.system.calls if c.startswith("http ")]), 2)
        self.assertIn("proxy stopped", (self.state / "watchdog.txt").read_text())

    def test_leaves_the_redirect_alone_while_the_proxy_answers(self):
        self.setup_job()
        self.system.calls.clear()
        self.watchdog(HOTLINE_CI_WATCHDOG_TICKS="4")
        self.assertNotCalled("iptables")

    def test_one_failed_check_is_not_enough(self):
        self.setup_job()
        self.system.calls.clear()
        self.system.web.pop(READYZ)
        self.watchdog(HOTLINE_CI_WATCHDOG_TICKS="1")
        self.assertNotCalled("iptables")

    def test_stops_when_teardown_has_removed_the_state(self):
        self.watchdog(HOTLINE_CI_WATCHDOG_TICKS="3")
        self.assertEqual(self.system.calls, [])


if __name__ == "__main__":
    unittest.main()
