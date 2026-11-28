"""Build args belong to the invocation that sends them, not a global set."""

import importlib.util
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "build_args", ROOT / "deploy/standalone/check_build_args.py"
)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class BuildArgTests(unittest.TestCase):
    def test_routing_shell_and_powershell(self):
        for language in ("sh", "ps1"):
            powershell = language == "ps1"
            source = (ROOT / f"bin/lib/build-local.{language}").read_text()
            images = dict(guard.recipes(source, powershell=powershell))
            self.assertEqual(set(images), set(guard.DOCKERFILES))
            self.assertEqual(images["future-agi"], ["IMAGE_VARIANT"])
            if not powershell:
                self.assertEqual(images["frontend"], ["VITE_SKILL_TRACES"])
            self.assertEqual(
                set(images["standalone"]),
                {
                    "BACKEND_IMAGE",
                    "FRONTEND_IMAGE",
                    "FI_COLLECTOR_IMAGE",
                    "AGENTCC_GATEWAY_IMAGE",
                },
            )

    def test_shell_invocations_route_build_args_for_each_topology(self):
        for topology in ("standalone", "distributed"):
            # Capture the argv produced by the shipping helper; do not pull or
            # start a container in a CI guard test.
            recipes = subprocess.check_output(
                [
                    "bash",
                    "-ceu",
                    'source bin/lib/build-local.sh; build_local_image() { printf "%s " "$@"; echo; }; build_local_images "$1" all guard',
                    "--",
                    topology,
                ],
                env={**os.environ, "VITE_SKILL_TRACES": "on"},
                cwd=ROOT,
                text=True,
            ).splitlines()
            self.assertEqual(
                [line.split() for line in recipes if "VITE_SKILL_TRACES=" in line],
                [
                    [
                        "futureagi/frontend:guard",
                        "--build-arg",
                        "VITE_SKILL_TRACES=on",
                        "frontend",
                    ]
                ],
            )
            (backend,) = [
                line for line in recipes if line.startswith("futureagi/future-agi:")
            ]
            self.assertEqual(
                "--build-arg IMAGE_VARIANT=slim" in backend, topology == "standalone"
            )

    def test_same_argument_is_checked_for_both_destinations(self):
        cases = [
            (
                'build_local_image "futureagi/frontend:$tag" --build-arg VITE_X=1 frontend\nbuild_local_image "futureagi/future-agi:$tag" --build-arg VITE_X=1 futureagi',
                False,
            ),
            (
                "Build-Image 'futureagi/frontend:local' @('--build-arg', 'VITE_X=1')\nBuild-Image 'futureagi/future-agi:local' @('--build-arg', 'VITE_X=1')",
                True,
            ),
        ]
        for source, powershell in cases:
            self.assertEqual(
                dict(guard.recipes(source, powershell=powershell)),
                {"frontend": ["VITE_X"], "future-agi": ["VITE_X"]},
            )

    def test_unknown_recipe_is_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "outside an image recipe"):
            list(guard.recipes("docker build --build-arg VITE_X=1 .", powershell=False))
