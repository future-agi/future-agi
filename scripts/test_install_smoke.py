"""scripts/install-smoke.sh, sourced in bash against a stand-in `docker` on PATH."""

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

SMOKE = Path(__file__).with_name("install-smoke.sh")

# Answers `docker compose ps -q SERVICE` and `docker inspect --format ... ID`
# from the containers file: one "service id image" line per container.
FAKE_DOCKER = textwrap.dedent(
    """\
    #!/bin/sh
    case "$1 $2" in
      "compose ps") awk -v s="$4" '$1 == s {print $2}' "$FAKE_CONTAINERS" ;;
      "inspect --format") awk -v i="$4" '$2 == i {print $3}' "$FAKE_CONTAINERS" ;;
      *) echo "unexpected docker call: $*" >&2; exit 64 ;;
    esac
    """
)


class ServicesRunTag(unittest.TestCase):
    def run_check(self, containers, *services):
        with tempfile.TemporaryDirectory() as tmp:
            docker = Path(tmp) / "docker"
            docker.write_text(FAKE_DOCKER)
            docker.chmod(0o755)
            listing = Path(tmp) / "containers"
            listing.write_text("".join(f"{line}\n" for line in containers))
            env = {
                **os.environ,
                "PATH": f"{tmp}:{os.environ['PATH']}",
                "FAKE_CONTAINERS": str(listing),
            }
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    '. "$0"; smoke_services_run_tag local "$@"',
                    SMOKE,
                    *services,
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_every_service_on_the_tag_passes(self):
        result = self.run_check(
            [
                "backend b1 futureagi/future-agi:local",
                "worker w1 futureagi/future-agi:local",
            ],
            "backend",
            "worker",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_service_left_on_the_old_release_fails_by_name(self):
        result = self.run_check(
            [
                "backend b1 futureagi/future-agi:local",
                "worker w1 futureagi/future-agi:v1.41.1",
            ],
            "backend",
            "worker",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "worker runs futureagi/future-agi:v1.41.1, not :local", result.stderr
        )

    def test_a_service_without_a_running_container_fails(self):
        result = self.run_check(
            ["backend b1 futureagi/future-agi:local"], "backend", "frontend"
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("frontend has no running container", result.stderr)

    def test_every_replica_is_checked(self):
        result = self.run_check(
            [
                "worker w1 futureagi/future-agi:local",
                "worker w2 futureagi/future-agi:latest",
            ],
            "worker",
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("worker runs futureagi/future-agi:latest", result.stderr)

    def test_an_untagged_image_behind_a_registry_port_is_not_the_tag(self):
        result = self.run_check(
            ["frontend f1 localhost:5000/futureagi/frontend"], "frontend"
        )
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
