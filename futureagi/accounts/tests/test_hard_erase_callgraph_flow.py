"""Scanner-level call graph regressions; no invented graph edges."""

import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

GRAPH = runpy.run_path(str(Path(__file__).with_name("hard_erase_callgraph.py")))
ROOT = GRAPH["ROOT"]
SOURCE = "futureagi/accounts/services/scanner_regression.py"
KEY = "accounts.services.scanner_regression.unsafe_entry"


def scan_fixture(source):
    sources = source if isinstance(source, dict) else {SOURCE: source}
    original_read = Path.read_text

    def read(path, *args, **kwargs):
        for name, text in sources.items():
            if path == ROOT / name:
                return text
        return original_read(path, *args, **kwargs)

    files = [*GRAPH["source_files"](ROOT), *sources]
    with patch.object(Path, "read_text", read):
        return GRAPH["graph"](files=files)


class CallGraphDiscoveryTests(unittest.TestCase):
    def test_aliases_relative_imports_and_nested_calls_are_not_dropped(self):
        cases = (
            "from .identity_issuance import issue_membership\ndef unsafe_entry():\n    row.save()\n    issue_membership(manager, 'create')\n",
            "from ..services import identity_issuance as issuance\ndef unsafe_entry():\n    issue = issuance.issue_membership\n    row.save()\n    issue(manager, 'create')\n",
            "from accounts.services.identity_issuance import issue_membership\ndef unsafe_entry():\n    first = issue_membership\n    second = first\n    row.save()\n    second(manager, 'create')\n",
            "from accounts.services.identity_issuance import issue_membership\ndef unsafe_entry():\n    def inner():\n        issue = issue_membership\n        issue(manager, 'create')\n    row.save()\n    inner()\n",
            "from accounts.services.identity_issuance import issue_membership\ndef helper():\n    issue_membership(manager, 'create')\ndef unsafe_entry():\n    call = helper\n    row.save()\n    call()\n",
        )
        for source in cases:
            with self.subTest(source=source):
                functions, edges, reached = scan_fixture(source)
                self.assertIn(KEY, reached)
                self.assertTrue(
                    any(
                        key == KEY
                        for key, _ in GRAPH["violations"](functions, edges, reached)
                    )
                )

    def test_package_relative_reexported_callable_alias_is_discovered(self):
        functions, edges, reached = scan_fixture(
            {
                SOURCE: "from .scanner_reexport import exported as issue\ndef unsafe_entry():\n    row.save()\n    issue(manager, 'create')\n",
                "futureagi/accounts/services/scanner_reexport.py": "from .identity_issuance import issue_membership\nexported = issue_membership\n",
            }
        )
        self.assertIn(KEY, reached)
        self.assertTrue(
            any(key == KEY for key, _ in GRAPH["violations"](functions, edges, reached))
        )

    def test_threadpool_boundary_keeps_worker_prewrite_checked(self):
        source = (
            "from concurrent.futures import ThreadPoolExecutor\n"
            "from .identity_issuance import issue_membership\n"
            "def worker():\n    row.save()\n    issue_membership(manager, 'create')\n"
            "def unsafe_entry():\n    row.save()\n    with ThreadPoolExecutor() as pool:\n        pool.submit(worker)\n"
        )
        functions, edges, reached = scan_fixture(source)
        self.assertIn(KEY, reached)
        failures = GRAPH["violations"](functions, edges, reached)
        self.assertFalse(any(key == KEY for key, _ in failures))
        self.assertTrue(
            any(key.endswith("scanner_regression.worker") for key, _ in failures)
        )

    def test_unresolved_issuance_references_fail_closed(self):
        for expression in (
            "callbacks[0](manager, 'create')",
            "dispatch(issue_membership)",
            "getattr(issuance, method_name)(manager, 'create')",
        ):
            source = (
                "from accounts.services.identity_issuance import issue_membership\n"
                "from accounts.services import identity_issuance as issuance\n"
                "def unsafe_entry():\n"
                "    callbacks = [issue_membership]\n"
                "    row.save()\n"
                f"    {expression}\n"
            )
            with self.subTest(expression=expression):
                # A scanner failure is an explicit completeness failure, not an omitted caller.
                try:
                    functions, edges, reached = scan_fixture(source)
                except ValueError as exc:
                    self.assertIn("issuance", str(exc).lower())
                else:
                    self.assertIn(KEY, reached)
                    self.assertTrue(
                        any(
                            key == KEY
                            for key, _ in GRAPH["violations"](functions, edges, reached)
                        )
                    )


if __name__ == "__main__":
    unittest.main()
