"""A pair lock must name the expected source, package role, and intact bytes."""

import hashlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path

code = runpy.run_path(str(Path(__file__).with_name("verify-skill-packages.py")))
verify, stage = code["verify"], code["stage"]


class PairLockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.sources = {k: v * 40 for k, v in zip(("product", "sdk", "traceai"), "abc")}
        self.names = {
            "sdk_python": "futureagi-0.6.14-py3-none-any.whl",
            "traceai_python": "fi_instrumentation_otel-1.5.0-py3-none-any.whl",
            "sdk_ts": "future-agi-sdk-0.1.2.tgz",
            "traceai_ts": "traceai-fi-core-1.4.0.tgz",
            "semconv_ts": "traceai-fi-semantic-conventions-1.3.0.tgz",
        }
        self.branches = {"sdk": "feat/skills", "traceai": "dev"}
        self.contract = {
            "source": {"backend_commit": self.sources["product"], "non_skills_sdk_commit": "d" * 40,
                       **dict.fromkeys(("openapi_sha256", "mcp_sha256", "non_skills_slice_sha256"), "e" * 64)},
            "sha256": dict.fromkeys(("swagger.json", "mcp-tools.json", "SOURCE.json"), "f" * 64),
        }
        self.items = {}
        for role, name in self.names.items():
            data = role.encode()
            (self.dist / name).write_bytes(data)
            self.items[role] = {"file": name, "sha256": hashlib.sha256(data).hexdigest()}

    def lock(self):
        data = json.dumps({"schema": 2, "sources": self.sources, "branches": self.branches,
                           "contract": self.contract, "artifacts": self.items}).encode()
        (self.dist / "pair.json").write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def test_verified_files_are_staged_and_rechecked(self):
        records = verify(self.dist, self.lock(), self.sources)
        stage(self.dist, self.root, records)
        wheels = self.root / "native-runtime/wheels"
        self.assertEqual(len((wheels / "SHA256SUMS").read_text().splitlines()), 2)
        stage(self.dist, self.root, records)
        (wheels / self.names["sdk_python"]).write_bytes(b"other build")
        with self.assertRaisesRegex(ValueError, "Different artifact"):
            stage(self.dist, self.root, records)

    def test_wrong_lock_component_or_package_is_rejected(self):
        expected = self.lock()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            verify(self.dist, "0" * 64, self.sources)
        with self.assertRaisesRegex(ValueError, "source revisions"):
            verify(self.dist, expected, {**self.sources, "sdk": "d" * 40})
        (self.dist / self.names["sdk_ts"]).write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "Package does not match"):
            verify(self.dist, expected, self.sources)

    def test_missing_provenance_is_rejected(self):
        self.contract["source"]["backend_commit"] = "1" * 40
        with self.assertRaisesRegex(ValueError, "contract provenance"):
            verify(self.dist, self.lock(), self.sources)
        self.contract["source"]["backend_commit"] = self.sources["product"]
        del self.contract["sha256"]["SOURCE.json"]
        with self.assertRaisesRegex(ValueError, "contract provenance"):
            verify(self.dist, self.lock(), self.sources)
        del self.branches["sdk"]
        with self.assertRaisesRegex(ValueError, "branch"):
            verify(self.dist, self.lock(), self.sources)

    def test_missing_extra_and_wrongly_named_packages_are_rejected(self):
        self.items["sdk_python"]["file"] = "../futureagi-0.6.14-py3-none-any.whl"
        with self.assertRaisesRegex(ValueError, "Unexpected filename"):
            verify(self.dist, self.lock(), self.sources)
        del self.items["sdk_python"]
        with self.assertRaisesRegex(ValueError, "exactly"):
            verify(self.dist, self.lock(), self.sources)
        self.items["other"] = self.items["sdk_ts"]
        with self.assertRaisesRegex(ValueError, "exactly"):
            verify(self.dist, self.lock(), self.sources)
