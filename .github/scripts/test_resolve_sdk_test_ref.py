"""Offline resolver contracts; Git's ref validator is local, never networked."""

import contextlib
import importlib.util
import io
import runpy
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "sdk_test_ref", Path(__file__).with_name("resolve-sdk-test-ref.py")
)
resolver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolver)
SHA = "a" * 40
OTHER_SHA = "b" * 40


class FakeGit(resolver.GitProbe):
    def __init__(self, refs=None, *, missing=(), package='{"scripts":{"test:live":"jest live"}}'):
        self.refs = refs or {}
        self.missing = missing
        self.package = package
        self.calls = []
        self.fetched_sha = None
        super().__init__(Path("."))

    def _git(self, *args, allow_missing=False):
        self.calls.append(args)
        code, output = 0, ""
        if args[0] == "ls-remote":
            names = args[5:]
            matches = [name for name in names if name in self.refs]
            code = 0 if matches else 2
            output = "".join(f"{self.refs[name]}\t{name}\n" for name in matches)
        elif args[0] == "fetch":
            ref = args[-1]
            self.fetched_sha = self.refs.get(ref, ref if len(ref) == 40 else None)
            if self.fetched_sha is None:
                raise resolver.ResolutionError("Unexpected unresolved fetch")
        elif args[0] == "rev-parse":
            output = self.fetched_sha + "\n"
        elif args[0] == "cat-file":
            code = 128 if args[-1].split(":", 1)[1] in self.missing else 0
            output = "blob\n" if not code else ""
        elif args[0] == "show":
            output = self.package
        elif args[0] != "init":
            raise AssertionError(args)
        if code and not allow_missing:
            raise resolver.ResolutionError("SDK repository operation failed")
        return subprocess.CompletedProcess(args, code, output, "not-for-output")


class ResolverTests(unittest.TestCase):
    def probe(self, results):
        seen = []

        def lookup(ref, mode):
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

    def test_main_bound_queue_prefers_main_to_dev_and_ignores_synthetic_ref(self):
        queue = "gh-readonly-queue/main/pr-42"
        probe, seen = self.probe({queue: (SHA, []), "dev": (SHA, []), "main": (OTHER_SHA, [])})
        with patch.dict(resolver.os.environ, {"GITHUB_EVENT_NAME": "merge_group", "HEAD_REF": queue,
                                              "BASE_REF": queue, "QUEUE_BASE_REF": "refs/heads/main"}):
            self.assertEqual(resolver.choose_ref("", *resolver.branch_inputs(), probe), (OTHER_SHA, "main"))
        self.assertEqual(seen, ["main"])

    def test_malformed_queue_base_fails_instead_of_qualifying_dev(self):
        for ref in ("", "main", "refs/tags/main", "refs/heads/", "refs/heads/bad..name"):
            with self.subTest(ref=ref), patch.dict(resolver.os.environ, {
                "GITHUB_EVENT_NAME": "merge_group", "QUEUE_BASE_REF": ref,
            }), self.assertRaises(resolver.InvalidRefError):
                resolver.branch_inputs()

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
        probe, _ = self.probe({resolver.BOOTSTRAP_REF: (resolver.BOOTSTRAP_REF, ["test:live"])})
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

    def test_invalid_explicit_refs_never_reach_transport(self):
        for value in ["--upload-pack=bad", "dev\nref=main", "x..y", "x//y", "x/", "bad name", "refs/pull/1/head"]:
            with self.subTest(value=value):
                probe, seen = self.probe({})
                with self.assertRaises(resolver.ResolutionError):
                    resolver.choose_ref(value, "head", "dev", probe)
                self.assertEqual(seen, [])

    def test_valid_plus_named_branch_is_preferred(self):
        ref = "release/v1.2.3+build.1"
        probe = FakeGit({"refs/heads/" + ref: SHA, "refs/heads/dev": OTHER_SHA})
        self.assertEqual(resolver.choose_ref("", ref, "dev", probe), (SHA, ref))
        self.assertEqual([c[-1] for c in probe.calls if c[0] == "fetch"], ["refs/heads/" + ref])

    def test_unsupported_automatic_ref_does_not_block_defaults(self):
        probe, seen = self.probe({"dev": (SHA, [])})
        self.assertEqual(resolver.choose_ref("", "not a branch", "dev", probe), (SHA, "dev"))
        self.assertEqual(seen, ["dev"])

    def test_qualified_heads_and_tags_keep_their_namespace(self):
        for ref in ["refs/heads/dev", "refs/tags/v1.2.3"]:
            with self.subTest(ref=ref):
                probe = FakeGit({ref: SHA})
                self.assertEqual(resolver.choose_ref(ref, "head", "dev", probe), (SHA, ref))
                advertisement = next(c for c in probe.calls if c[0] == "ls-remote")
                self.assertEqual(advertisement[5:], (ref,))
                self.assertEqual(next(c for c in probe.calls if c[0] == "fetch")[-1], ref)

    def test_missing_hex_automatic_branch_falls_back_without_fetch(self):
        for ref in ["abcdef0", "a" * 40]:
            with self.subTest(ref=ref):
                probe = FakeGit({"refs/heads/dev": OTHER_SHA})
                self.assertEqual(resolver.choose_ref("", ref, "dev", probe), (OTHER_SHA, "dev"))
                self.assertEqual([c[-1] for c in probe.calls if c[0] == "fetch"], ["refs/heads/dev"])

    def test_advertised_hex_named_branch_is_not_confused_with_commit(self):
        probe = FakeGit({"refs/heads/abcdef0": SHA})
        self.assertEqual(resolver.choose_ref("", "abcdef0", "dev", probe), (SHA, "abcdef0"))
        self.assertEqual(next(c for c in probe.calls if c[0] == "fetch")[-1], "refs/heads/abcdef0")

    def test_explicit_short_sha_fails_clearly_without_fetch(self):
        probe = FakeGit()
        with self.assertRaisesRegex(resolver.ResolutionError, "full 40-character"):
            resolver.choose_ref("855f2ad", "head", "dev", probe)
        self.assertFalse(any(c[0] == "fetch" for c in probe.calls))

    def test_explicit_full_sha_fetches_only_the_exact_object(self):
        probe = FakeGit()
        self.assertEqual(resolver.choose_ref(SHA, "head", "dev", probe), (SHA, SHA))
        self.assertFalse(any(c[0] == "ls-remote" for c in probe.calls))
        self.assertEqual(next(c for c in probe.calls if c[0] == "fetch")[-1], SHA)

    def test_bootstrap_pin_cannot_resolve_to_different_commit(self):
        probe, _ = self.probe({resolver.BOOTSTRAP_REF: (SHA, [])})
        with self.assertRaisesRegex(resolver.ResolutionError, "identity mismatch"):
            resolver.choose_ref("", "head", "dev", probe)

    def test_all_required_files_and_script_remain_mandatory(self):
        for path in (*resolver.REQUIRED_PATHS, resolver.PACKAGE_JSON):
            with self.subTest(path=path):
                probe = FakeGit({"refs/heads/selected": SHA}, missing=[path])
                with self.assertRaisesRegex(resolver.ResolutionError, "lacks required"):
                    resolver.choose_ref("selected", "head", "dev", probe)
        for package in ["null", "[]", "{}", '{"scripts":null}', '{"scripts":{"test:live":""}}']:
            with self.subTest(package=package):
                probe = FakeGit({"refs/heads/selected": SHA}, package=package)
                with self.assertRaisesRegex(resolver.ResolutionError, "lacks required"):
                    resolver.choose_ref("selected", "head", "dev", probe)

    def test_local_validator_failure_is_not_a_candidate_miss(self):
        probe, seen = self.probe({"dev": (SHA, [])})
        with patch.object(resolver, "validate_ref", side_effect=resolver.ResolutionError("validation timed out")):
            with self.assertRaisesRegex(resolver.ResolutionError, "timed out"):
                resolver.choose_ref("", "head", "dev", probe)
        self.assertEqual(seen, [])

    def test_transport_diagnostics_are_not_exposed(self):
        probe = resolver.GitProbe.__new__(resolver.GitProbe)
        probe.directory = Path(".")
        secret = "SYNTHETIC_DO_NOT_PRINT"
        with patch.object(resolver.subprocess, "run", return_value=subprocess.CompletedProcess([], 128, secret, secret)):
            with self.assertRaises(resolver.ResolutionError) as error:
                probe("dev", "named")
        self.assertNotIn(secret, str(error.exception))

    def test_script_only_changes_are_in_e2e_changes_gate(self):
        scripts = Path(__file__).resolve().parent
        workflow = (scripts.parent / "workflows/e2e-ci.yml").read_text()
        self.assertIn("QUEUE_BASE_REF: ${{ github.event.merge_group.base_ref }}", workflow)
        self.assertIn("any: ${{ steps.filter.outputs.any }}", workflow)
        self.assertIn('file=.github/scripts/ci_scope.py', workflow)
        # The planner selects the entire suite unless a path is certified metadata.
        metadata = runpy.run_path(str(scripts / "ci_scope.py"))["METADATA"]
        for name in ("resolve-sdk-test-ref.py", "test_resolve_sdk_test_ref.py"):
            self.assertNotIn(f".github/scripts/{name}".encode(), metadata)


if __name__ == "__main__":
    with contextlib.redirect_stdout(io.StringIO()):
        unittest.main()
