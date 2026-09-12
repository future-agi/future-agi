import argparse
import subprocess
import unittest
from unittest.mock import patch

import current_stack_e2e as e2e


class CurrentStackRunnerTests(unittest.TestCase):
    def test_prefers_installed_standalone_compose(self):
        args = argparse.Namespace(compose_file=None, compose_project_name=None)
        with patch.object(e2e.shutil, "which", side_effect=lambda name: f"/bin/{name}"):
            self.assertEqual(e2e._compose_prefix(args)[0], "/bin/docker-compose")

    def test_timeout_never_prints_credentials_or_source(self):
        args = argparse.Namespace(backend_service="backend")
        with (
            patch.object(e2e, "_compose_prefix", return_value=["docker-compose"]),
            patch.object(
                e2e.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["private-command"], 180),
            ) as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "exceeded 180 seconds"):
                e2e._django_shell(
                    args, "private-source", {"OMEGA_E2E_API_KEY": "private-key"}
                )
        command = run.call_args.args[0]
        self.assertNotIn("private-key", " ".join(command))
        self.assertEqual(
            run.call_args.kwargs["env"]["OMEGA_E2E_API_KEY"], "private-key"
        )
        self.assertIn("OMEGA_E2E_API_KEY", command)

    def test_default_does_not_execute_stack_or_models(self):
        with (
            patch.object(e2e, "_run") as run,
            patch("sys.argv", ["runner"]),
            patch("builtins.print"),
        ):
            self.assertEqual(e2e.main(), 0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
