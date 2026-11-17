"""Real filesystem regressions for both writer-census entry points, no Django."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_census(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WriterDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.paths = json.loads((HERE / "oauth_writer_scope.json").read_text())[
            "required_paths"
        ]
        for name in cls.paths:
            path = cls.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        cls.modules = [
            (
                load_census("test_oauth_writer_census"),
                "test_no_connector_persistence_outside_chokepoint",
            ),
            (
                load_census("test_oauth_writer_alias_census"),
                "test_all_connector_bound_scopes_use_chokepoint",
            ),
        ]
        for module, _ in cls.modules:
            module.__file__ = str(
                cls.root / "futureagi/ee/falcon_ai/tests" / Path(module.__file__).name
            )
        cls.planted = []

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def tearDown(self):
        for path in self.planted:
            if path.relative_to(self.root).as_posix() in self.paths:
                path.write_text("")
            elif path.exists():
                path.unlink()
        self.planted.clear()

    def plant(self, name, source):
        path = self.root / "futureagi" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        self.planted.append(path)

    def assert_rejected(self, message="writer"):
        for module, function in self.modules:
            with (
                self.subTest(census=module.__name__),
                self.assertRaisesRegex(AssertionError, message),
            ):
                getattr(module, function)()

    def test_clean_complete_tree_is_accepted(self):
        for module, function in self.modules:
            with self.subTest(census=module.__name__):
                getattr(module, function)()

    def test_nested_falcon_writer_is_rejected(self):
        self.plant(
            "ee/falcon_ai/services/nested/maintenance.py",
            "from ee.falcon_ai.models import MCPConnector as Row\nRow.objects.update(oauth_state='fixture')\n",
        )
        self.assert_rejected()

    def test_external_nested_importer_is_rejected(self):
        self.plant(
            "other_app/jobs/nested/maintenance.py",
            "from ee.falcon_ai.models import MCPConnector as Row\nRow.objects.update(**changes)\n",
        )
        self.assert_rejected()

    def test_reexported_alias_writer_is_rejected(self):
        self.plant(
            "other_app/row_alias.py",
            "from ee.falcon_ai.models import MCPConnector as Row\n",
        )
        self.plant(
            "other_app/jobs/deep/maintenance.py",
            "from other_app.row_alias import Row as Record\nRecord.objects.update(**changes)\n",
        )
        self.assert_rejected()

    def test_relative_reexport_writer_is_rejected(self):
        self.plant(
            "other_app/row_alias.py",
            "from ee.falcon_ai.models import MCPConnector as Row\n",
        )
        self.plant(
            "other_app/jobs/maintenance.py",
            "from ..row_alias import Row as Record\nRecord.objects.update(**changes)\n",
        )
        self.assert_rejected()

    def test_module_alias_reexport_writer_is_rejected(self):
        self.plant(
            "other_app/row_alias.py",
            "from ee.falcon_ai.models import MCPConnector as Row\n",
        )
        self.plant(
            "other_app/jobs/maintenance.py",
            "import other_app.row_alias as store\nstore.Row.objects.update(**changes)\n",
        )
        self.assert_rejected()

    def test_assignment_reexport_writer_is_rejected(self):
        self.plant(
            "other_app/row_alias.py",
            "from ee.falcon_ai.models import MCPConnector\nRow = MCPConnector\n",
        )
        self.plant(
            "other_app/jobs/maintenance.py",
            "from other_app.row_alias import Row\nRow.objects.update(**changes)\n",
        )
        self.assert_rejected()

    def test_unrelated_chokepoint_basename_is_not_exempt(self):
        self.plant(
            "other_app/oauth_registration.py",
            "from ee.falcon_ai.models import MCPConnector as Row\nRow.objects.update(**changes)\n",
        )
        self.assert_rejected()

    def test_secret_named_runtime_writer_is_not_exempt(self):
        self.plant(
            "ee/falcon_ai/services/secret_writer.py",
            "from ee.falcon_ai.models import MCPConnector as Row\nRow.objects.update(**changes)\n",
        )
        self.assert_rejected()

    def test_removing_one_scanned_module_is_rejected(self):
        path = self.root / "futureagi/ee/falcon_ai/oauth_client.py"
        self.assertIn(path.relative_to(self.root).as_posix(), self.paths)
        path.unlink()
        self.planted.append(path)
        self.assert_rejected("shrunk")

    def test_empty_scope_is_rejected(self):
        package = self.root / "futureagi"
        parked = self.root / "parked"
        package.rename(parked)
        try:
            self.assert_rejected("empty")
        finally:
            parked.rename(package)

    def test_only_exact_chokepoint_path_is_exempt(self):
        self.plant(
            "ee/falcon_ai/oauth_registration.py",
            "from ee.falcon_ai.models import MCPConnector\nMCPConnector.objects.update(**changes)\n",
        )
        for module, function in self.modules:
            with self.subTest(census=module.__name__):
                getattr(module, function)()


if __name__ == "__main__":
    unittest.main()
