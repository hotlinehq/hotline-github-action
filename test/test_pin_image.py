import unittest

from hotline_action.pin_image import run_pin
from test.helpers import LOOKED_UP, PIN, RELEASE_URL, Base

REPO = "ghcr.io/hotlinehq/hotline-proxy"


class Pin(Base):
    def pin(self, tag):
        return run_pin(tag, self.root, self.system, log=self.out.append)

    def pinned(self):
        return (self.root / "proxy-image.txt").read_text()

    def test_writes_the_digest_of_the_whole_multi_platform_image(self):
        self.system.web[RELEASE_URL] = '{"tag_name":"v1.0.0"}'
        self.assertEqual(self.pin("1.0.0"), 0)
        self.assertIn(f"{REPO}@{LOOKED_UP}\n", self.pinned())
        self.assertTrue(self.called(f"buildx imagetools inspect {REPO}:1.0.0"))

    def test_turns_edge_into_its_digest_and_marks_it_prerelease_while_there_is_no_release(self):
        self.assertEqual(self.pin("edge"), 0)
        self.assertIn(f"{REPO}@{LOOKED_UP}  # prerelease\n", self.pinned())

    def test_refuses_a_sha_tag(self):
        self.assertEqual(self.pin("sha-abc1234"), 1)
        self.assertIn(PIN, self.pinned())

    def test_refuses_edge_once_hotline_has_a_release(self):
        self.system.web[RELEASE_URL] = '{"tag_name":"v1.0.0"}'
        self.assertEqual(self.pin("edge"), 1)
        self.assertIn("has a release", self.output)
        self.assertIn(PIN, self.pinned())

    def test_refuses_an_image_whose_signature_does_not_verify_and_changes_nothing(self):
        self.system.fail = ["cosign"]
        self.assertEqual(self.pin("edge"), 1)
        self.assertIn("does not verify", self.output)
        self.assertIn(PIN, self.pinned())

    def test_checks_the_signature_of_the_digest_written_by_hotlines_release_workflow(self):
        self.pin("edge")
        self.assertTrue(self.called(
            f"cosign verify {REPO}@{LOOKED_UP} --certificate-identity-regexp "
            "^https://github.com/hotlinehq/hotline/ --certificate-oidc-issuer "
            "https://token.actions.githubusercontent.com"))

    def test_refuses_a_digest_that_is_not_sha256(self):
        class Odd(type(self.system)):
            def run(self, *args, **kw):
                return '{"digest":"latest"}' if "imagetools" in args else super().run(*args, **kw)
        self.system = Odd()
        self.assertEqual(self.pin("edge"), 1)
        self.assertIn("no digest found", self.output)


if __name__ == "__main__":
    unittest.main()
