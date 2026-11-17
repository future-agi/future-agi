"""No-Git/EE-free source checks retain discovery and refusal controls, offline."""

import ast
import importlib.util
import runpy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parents[2]
GRAPH = runpy.run_path(str(TESTS / "hard_erase_callgraph.py"))
FLOW = runpy.run_path(str(TESTS / "test_hard_erase_callgraph_flow.py"))
SQL = runpy.run_path(str(TESTS / "test_hard_erase_sql_source.py"))
CUT = runpy.run_path(str(TESTS / "test_hard_erase_cut_health_flow.py"))
EFFECTS = runpy.run_path(str(TESTS / "test_hard_erase_r2f7_flow.py"))
ISSUANCE = "futureagi/accounts/services/identity_issuance.py"
ROOT_SOURCE = """def issue_membership(manager, operation):
    with issuance_entry():
        manager.create()

def save_new_person(person):
    with issuance_entry():
        person.save()
"""
UNSAFE = """from accounts.services.identity_issuance import issue_membership

def unsafe_entry():
    row.save()
    issue_membership(manager, 'create')
"""


def put(root, name, source):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return path


def without_ee_read(path, *args, **kwargs):
    if "ee" in path.relative_to(ROOT).parts:
        raise FileNotFoundError("EE source is absent in this venue")
    return ORIGINAL_READ(path, *args, **kwargs)


ORIGINAL_READ = Path.read_text


class SourceVenueTests(unittest.TestCase):
    def test_graph_without_git_discovers_unknown_unsafe_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            put(root, ISSUANCE, ROOT_SOURCE)
            put(root, "futureagi/new_app/unreviewed.py", UNSAFE)
            with (
                patch.dict(GRAPH["graph"].__globals__, ROOT=root),
                patch.object(subprocess, "check_output", side_effect=AssertionError("Git is unavailable")),
            ):
                functions, edges, reached = GRAPH["graph"]()
            key = "new_app.unreviewed.unsafe_entry"
            self.assertIn(key, reached)
            self.assertTrue(any(name == key for name, _ in GRAPH["violations"](functions, edges, reached)))
            self.assertFalse((root / ".git").exists())

    def test_fixture_injection_without_git_keeps_all_discovery_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            put(root, ISSUANCE, ROOT_SOURCE)
            with (
                patch.dict(FLOW["GRAPH"]["graph"].__globals__, ROOT=root),
                patch.dict(FLOW["scan_fixture"].__globals__, ROOT=root),
                patch.object(subprocess, "check_output", side_effect=AssertionError("Git is unavailable")),
            ):
                for name in unittest.defaultTestLoader.getTestCaseNames(FLOW["CallGraphDiscoveryTests"]):
                    with self.subTest(control=name):
                        getattr(FLOW["CallGraphDiscoveryTests"](), name)()

    def test_sql_enumerator_without_git_keeps_unknown_sources_and_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = [
                "futureagi/accounts/migrations/new_step.py",
                "futureagi/new_app/unreviewed.py",
                "scripts/unreviewed.sql",
            ]
            for name in reversed(expected):
                put(root, name, "")
            for name in (
                ".git/objects/hidden.py", ".venv/lib/hidden.py",
                "node_modules/pkg/hidden.sql", "__pycache__/hidden.py",
                "futureagi/settings.py", "futureagi/secret_helper.py",
                "futureagi/values.sql", "futureagi/.env/private.py",
                "notes.txt",
            ):
                put(root, name, "")
            with (
                patch.dict(SQL["tracked_sources"].__globals__, ROOT=root),
                patch.object(subprocess, "check_output", side_effect=AssertionError("Git is unavailable")),
            ):
                actual = list(SQL["tracked_sources"]())
            self.assertEqual([name for name, _ in actual], expected)
            self.assertEqual([path for _, path in actual], [root / name for name in expected])
            # Discovery cannot turn a newly encountered protected mutation into
            # a filename exemption. This text is never dispatched to a driver.
            text = "DELETE FROM newly_seen_guarded_table"
            tree = ast.parse("def unsafe():\n    cursor.execute(" + repr(text) + ")\n")
            status, _ = SQL["classify"](expected[1], 2, text, tree, {"newly_seen_guarded_table": {"id"}}, {})
            self.assertEqual(status, "UNCLASSIFIED")

    def test_source_discovery_fails_closed_on_links_and_walk_errors(self):
        sources = runpy.run_path(str(TESTS / "hard_erase_source_files.py"))
        for directory_link in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                original = put(root, "futureagi/app/real.py", "")
                target = original.parent if directory_link else original
                link = root / ("linked_app" if directory_link else "linked.py")
                link.symlink_to(target, target_is_directory=directory_link)
                with self.subTest(directory_link=directory_link), self.assertRaises(ValueError):
                    sources["source_files"](root)
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(sources["os"], "scandir", side_effect=PermissionError("unreadable source")),
                self.assertRaises(PermissionError),
            ):
                sources["source_files"](Path(directory))

    def test_effect_row_selection_uses_product_presence_and_retains_every_oss_row(self):
        sources = runpy.run_path(str(TESTS / "hard_erase_source_files.py"))
        rows = {"accounts.service.first": {}, "new_app.second": {}, "ee.feature.third": {}}
        for present in (False, True):
            with (
                self.subTest(present=present),
                patch.object(importlib.util, "find_spec", return_value=object() if present else None) as presence,
            ):
                selected = sources["available_effect_rows"](ROOT, rows)
                self.assertEqual(set(selected), set(rows) if present else {"accounts.service.first", "new_app.second"})
                presence.assert_called_once_with("ee")

    def test_ee_absent_dispatcher_still_executes_all_binding_mutation_controls(self):
        with (
            patch.object(importlib.util, "find_spec", return_value=None) as presence,
            patch.object(Path, "read_text", without_ee_read),
        ):
            for name in unittest.defaultTestLoader.getTestCaseNames(CUT["DispatcherBindingTests"]):
                with self.subTest(control=name):
                    getattr(CUT["DispatcherBindingTests"](), name)()
            self.assertIn(unittest.mock.call("ee"), presence.call_args_list)

    def test_ee_present_dispatcher_missing_source_fails_closed(self):
        with (
            patch.object(importlib.util, "find_spec", return_value=object()),
            patch.object(Path, "read_text", without_ee_read),
            self.assertRaises(FileNotFoundError),
        ):
            CUT["DispatcherBindingTests"]().fixture()

    def test_ee_absent_classifications_keep_available_mutation_controls(self):
        with (
            patch.object(importlib.util, "find_spec", return_value=None) as presence,
            patch.object(Path, "read_text", without_ee_read),
        ):
            EFFECTS["ConservativeScannerTests"]().test_exact_non_row_phase_classifications_fail_closed_on_source_change()
            self.assertIn(unittest.mock.call("ee"), presence.call_args_list)

    def test_ee_present_classification_missing_source_fails_closed(self):
        with (
            patch.object(importlib.util, "find_spec", return_value=object()),
            patch.object(Path, "read_text", without_ee_read),
            self.assertRaises(FileNotFoundError),
        ):
            EFFECTS["ConservativeScannerTests"]().test_exact_non_row_phase_classifications_fail_closed_on_source_change()


if __name__ == "__main__":
    unittest.main()
