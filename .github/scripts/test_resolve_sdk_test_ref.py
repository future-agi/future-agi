"""Pure resolver contracts: no SDK code, network, credentials or application."""

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "sdk_test_ref", Path(__file__).with_name("resolve-sdk-test-ref.py")
)
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)
SHA = "a" * 40
OTHER_SHA = "b" * 40


class ResolverTests(unittest.TestCase):
    def probe(self, results):
        seen = []

        def lookup(ref):
            seen.append(ref)
            value = results.get(ref)
            if isinstance(value, Exception):
                raise value
            return value

        return lookup, seen

    def test_complete_same_named_branch_wins_and_is_pinned(self):
        probe, seen = self.probe({"fix/tracing": (SHA, []), "dev": (OTHER_SHA, [])})
        self.assertEqual(resolver.choose_ref("", "fix/tracing", "dev", probe), (SHA, "fix/tracing"))
        self.assertEqual(seen, ["fix/tracing"])

    def test_incomplete_default_branches_use_exact_bootstrap(self):
        probe, seen = self.probe({
            "dev": (SHA, ["sdkgen/local_api_key.py"]),
            "main": (OTHER_SHA, ["python/tests/platform/test_all_operations_live.py"]),
            resolver.BOOTSTRAP_REF: (resolver.BOOTSTRAP_REF, []),
        })
        self.assertEqual(resolver.choose_ref("", "fix/tracing", "dev", probe), (resolver.BOOTSTRAP_REF, resolver.BOOTSTRAP_REF))
        self.assertEqual(seen, ["fix/tracing", "dev", "main", resolver.BOOTSTRAP_REF])

    def test_new_complete_default_supersedes_bootstrap(self):
        probe, seen = self.probe({"dev": (SHA, [])})
        self.assertEqual(resolver.choose_ref("", "feature", "dev", probe), (SHA, "dev"))
        self.assertNotIn(resolver.BOOTSTRAP_REF, seen)

    def test_explicit_incomplete_ref_does_not_fall_back(self):
        probe, seen = self.probe({"chosen": (SHA, ["test:live"]), resolver.BOOTSTRAP_REF: (OTHER_SHA, [])})
        with self.assertRaisesRegex(resolver.ResolutionError, "lacks required"):
            resolver.choose_ref("chosen", "head", "dev", probe)
        self.assertEqual(seen, ["chosen"])

    def test_explicit_missing_ref_fails(self):
        probe, seen = self.probe({})
        with self.assertRaisesRegex(resolver.ResolutionError, "does not exist"):
            resolver.choose_ref("chosen", "head", "dev", probe)
        self.assertEqual(seen, ["chosen"])

    def test_transport_failure_does_not_fall_back(self):
        probe, seen = self.probe({"dev": resolver.ResolutionError("network unavailable")})
        with self.assertRaisesRegex(resolver.ResolutionError, "network unavailable"):
            resolver.choose_ref("", "dev", "dev", probe)
        self.assertEqual(seen, ["dev"])

    def test_no_complete_candidate_is_failure(self):
        probe, _ = self.probe({resolver.BOOTSTRAP_REF: (SHA, ["test:live"])})
        with self.assertRaisesRegex(resolver.ResolutionError, "No SDK revision"):
            resolver.choose_ref("", "head", "dev", probe)

    def test_returned_revision_must_be_immutable(self):
        for sha in ["main", "a" * 7, "a" * 39, "A" * 40, "a" * 40 + "\nref=main"]:
            with self.subTest(sha=sha):
                probe, _ = self.probe({"dev": (sha, [])})
                with self.assertRaisesRegex(resolver.ResolutionError, "full commit"):
                    resolver.choose_ref("", "dev", "dev", probe)

    def test_explicit_sha_is_supported(self):
        probe, seen = self.probe({SHA: (SHA, [])})
        self.assertEqual(resolver.choose_ref(SHA, "head", "dev", probe), (SHA, SHA))
        self.assertEqual(seen, [SHA])

    def test_unsafe_refs_never_reach_transport(self):
        for value in ["--upload-pack=bad", "dev\nref=main", "x..y", "x//y", "x/", "$(id)", "x;id"]:
            with self.subTest(value=value):
                probe, seen = self.probe({})
                with self.assertRaises(resolver.ResolutionError):
                    resolver.choose_ref(value, "head", "dev", probe)
                self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
