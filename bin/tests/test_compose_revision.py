"""Build metadata contracts; run with python3 -I -B bin/tests/test_compose_revision.py.

Check the block-style build mappings, including extension anchors, without
loading Compose, reading an environment file, or needing a YAML dependency.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REVISION = "${FUTURE_AGI_REVISION:-}"


def block(lines, start):
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#"):
            if len(line) - len(line.lstrip()) <= indent:
                break
        end += 1
    return lines[start + 1 : end]


class ComposeRevisionTests(unittest.TestCase):
    def check_builds(self, filename, expected_count):
        lines = (ROOT / filename).read_text(encoding="utf-8").splitlines()
        starts = [i for i, line in enumerate(lines) if re.match(r"^\s+build:", line)]
        self.assertEqual(len(starts), expected_count, "update the build-section census")
        for start in starts:
            with self.subTest(file=filename, line=start + 1):
                # Short-form builds must become a mapping so both fields exist.
                self.assertEqual(lines[start].strip(), "build:")
                indent = len(lines[start]) - len(lines[start].lstrip())
                build = block(lines, start)
                for field, key in (("args", "REVISION"),
                                   ("labels", "org.opencontainers.image.revision")):
                    header = " " * (indent + 2) + field + ":"
                    self.assertEqual(build.count(header), 1, f"missing/duplicate build.{field}")
                    children = block(build, build.index(header))
                    self.assertIn(" " * (indent + 4) + key + ": " + REVISION, children)

    def test_standalone_builds(self):
        self.check_builds("docker-compose.yml", 2)

    def test_distributed_builds_including_collector_anchor(self):
        self.check_builds("docker-compose.distributed.yml", 3)

    def test_collector_services_inherit_the_stamped_build(self):
        lines = (ROOT / "docker-compose.distributed.yml").read_text(encoding="utf-8").splitlines()
        anchor = lines.index("x-fi-collector-image: &fi-collector-image")
        self.assertIn("  build:", block(lines, anchor))
        for name in ("fi-collector", "fi-property-catalog-consumer"):
            service = block(lines, lines.index(f"  {name}:"))
            self.assertIn("    <<: *fi-collector-image", service)
            self.assertNotIn("    build:", service, "collector build must stay shared")


if __name__ == "__main__":
    unittest.main(verbosity=2)
