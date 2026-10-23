"""Service-free bounds/isolation checks; no Docker daemon calls."""

import contextlib
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from generate import generate


class FixtureBoundsTests(unittest.TestCase):
    def test_real_app_fixture_is_bounded_and_loopback_only(self):
        with tempfile.TemporaryDirectory() as parent:
            target = Path(parent) / "new-fixture"
            with contextlib.redirect_stdout(io.StringIO()):
                generate(target, 28123, application_deps=True)
            compose = json.loads((target / "compose.json").read_text())
            fixture = json.loads((target / "fixture.json").read_text())
            self.assertEqual(len(compose["services"]), 11)
            self.assertEqual(
                sum(
                    int(s["mem_limit"].rstrip("m"))
                    for s in compose["services"].values()
                ),
                5760,
            )
            ports = [
                port
                for service in compose["services"].values()
                for port in service.get("ports", [])
            ]
            self.assertEqual(len(ports), 9)
            self.assertTrue(all(port.startswith("127.0.0.1:") for port in ports))
            self.assertEqual(fixture["native_port"], 28131)
            self.assertEqual(
                fixture["clickhouse_image"], "clickhouse/clickhouse-server:25.3.14.14"
            )
            for xml in target.glob("*.xml"):
                ET.fromstring(xml.read_text())
                self.assertEqual(xml.stat().st_mode & 0o777, 0o644)
            self.assertEqual((target / "fixture.json").stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                generate(target, 28123, application_deps=True)


if __name__ == "__main__":
    unittest.main()
