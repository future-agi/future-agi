"""Exercise the scoped/root conftest union with real pytest, without Django."""

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name("conftest.py")


class ConftestUnionTests(unittest.TestCase):
    def test_root_client_identity_survives_scoped_conftest_collection(self):
        source = ast.parse(SOURCE.read_text())
        hooks = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "pytest_configure"]
        # Only the union hook runs here. Application fixtures/settings remain
        # unimported; both conftest modules follow the real pytest lifecycle.
        scoped_source = "from pathlib import Path\n" + "\n".join(ast.unparse(n) for n in hooks)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "futureagi"
            tests = root / "agentcc" / "tests"
            tests.mkdir(parents=True)
            (root / "conftest.py").write_text(
                "class WorkspaceAwareAPIClient:\n    registry = []\n"
            )
            (tests / "conftest.py").write_text(scoped_source)
            (tests / "shared_permissions.py").write_text(
                "from conftest import WorkspaceAwareAPIClient\n"
            )
            for name in ("test_api.py", "test_api_key_delete_ownership_r2.py"):
                (tests / name).write_text(
                    "from pathlib import Path\n"
                    "from shared_permissions import WorkspaceAwareAPIClient\n"
                    "def test_same_root_client(request):\n"
                    "    root = Path(__file__).resolve().parents[2] / 'conftest.py'\n"
                    "    registered = next(p for p in request.config.pluginmanager.get_plugins() if getattr(p, '__file__', None) and Path(p.__file__).resolve() == root)\n"
                    "    assert WorkspaceAwareAPIClient is registered.WorkspaceAwareAPIClient\n"
                    "    assert WorkspaceAwareAPIClient.registry is registered.WorkspaceAwareAPIClient.registry\n"
                )
            environment = dict(os.environ)
            environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-m", "pytest", "-q", "-c", "/dev/null",
                 "-p", "no:cacheprovider", "--confcutdir", str(root), str(root)],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("2 passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
