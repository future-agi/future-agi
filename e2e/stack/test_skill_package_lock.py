"""A pair lock must name the expected source, package role, and intact bytes."""

import hashlib
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        inputs = {"backend_commit": self.sources["product"], "openapi_sha256": "1" * 64,
                  "mcp_sha256": "2" * 64, "tools_yaml_sha256": "3" * 64}
        self.contract = {
            "sync_mode": "scoped",
            "source": {**{scope: {**inputs, "tools": [tool]} for scope, tool in
                          (("sql", "run_sql"), ("epoch", "get_epoch"), ("skills", "list_skills"))},
                       "other": {"sdk_commit": "d" * 40, "slice_sha256": "e" * 64, "openapi_sha256": "4" * 64}},
            "sha256": {"swagger.json": "f" * 64},
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

    def test_partial_or_inconsistent_scoped_provenance_is_rejected(self):
        source = self.contract["source"]
        for scope in source:
            missing = {k: v for k, v in source.items() if k != scope}
            with self.subTest(missing=scope), patch.dict(self.contract, source=missing):
                with self.assertRaisesRegex(ValueError, "contract provenance"):
                    verify(self.dist, self.lock(), self.sources)
        for scope, key, value in [("sql", "backend_commit", "1" * 40), ("epoch", "openapi_sha256", "e" * 64),
                                  ("skills", "mcp_sha256", "e" * 64), ("sql", "tools_yaml_sha256", "e" * 64),
                                  ("skills", "tools", []), ("skills", "tools", ["run_sql"]),
                                  ("other", "sdk_commit", "branch"), ("other", "slice_sha256", None),
                                  ("other", "openapi_sha256", "")]:
            with self.subTest(scope=scope, key=key), patch.dict(source[scope], {key: value}):
                with self.assertRaisesRegex(ValueError, "contract provenance"):
                    verify(self.dist, self.lock(), self.sources)
        for wrong in [{}, {"swagger.json": ""}, {**self.contract["sha256"], "SOURCE.json": "e" * 64}]:
            with patch.dict(self.contract, sha256=wrong), self.assertRaisesRegex(ValueError, "contract provenance"):
                verify(self.dist, self.lock(), self.sources)
        del self.branches["sdk"]
        with self.assertRaisesRegex(ValueError, "branch"):
            verify(self.dist, self.lock(), self.sources)

    def test_product_pairing_owns_only_the_skills_scope(self):
        # Product pairing re-syncs Skills from the product under test; sql and epoch keep the
        # paired SDK commit's own records, synced from another product commit.
        source = self.contract["source"]
        pinned = {"backend_commit": "5" * 40, "openapi_sha256": "6" * 64,
                  "mcp_sha256": "7" * 64, "tools_yaml_sha256": "8" * 64}
        for scope in ("sql", "epoch"):
            source[scope] = {**pinned, "tools": source[scope]["tools"]}
        with self.assertRaisesRegex(ValueError, "contract provenance"):
            verify(self.dist, self.lock(), self.sources)  # without owned_scopes, all must match
        self.contract["owned_scopes"] = ["skills"]
        verify(self.dist, self.lock(), self.sources)
        for scope, key, value in [("skills", "backend_commit", "9" * 40),     # owned: must be this product
                                  ("epoch", "backend_commit", "9" * 40),      # pinned: must agree with sql
                                  ("sql", "tools", ["list_skills"])]:         # inventories stay disjoint
            with self.subTest(scope=scope, key=key), patch.dict(source[scope], {key: value}):
                with self.assertRaisesRegex(ValueError, "contract provenance"):
                    verify(self.dist, self.lock(), self.sources)
        for owned in [[], ["nope"], ["skills", "skills"], "skills", ["epoch"], ["sql", "epoch"], ["skills", "epoch"]]:
            with self.subTest(owned=owned), patch.dict(self.contract, owned_scopes=owned):
                with self.assertRaisesRegex(ValueError, "contract provenance"):
                    verify(self.dist, self.lock(), self.sources)

    def test_legacy_receipts_are_distinct_from_scoped_swagger(self):
        self.contract["sync_mode"] = "full"
        with self.assertRaisesRegex(ValueError, "contract provenance"):
            verify(self.dist, self.lock(), self.sources)
        source = {"backend_commit": self.sources["product"], "openapi_sha256": "e" * 64, "mcp_sha256": "f" * 64}
        self.contract["source"] = source
        with self.assertRaisesRegex(ValueError, "contract provenance"):
            verify(self.dist, self.lock(), self.sources)
        self.contract["sha256"].update({"mcp-tools.json": "f" * 64, "SOURCE.json": "e" * 64})
        self.assertEqual(verify(self.dist, self.lock(), self.sources), self.items)
        source.update(non_skills_sdk_commit="d" * 40, non_skills_slice_sha256="e" * 64)
        with self.assertRaisesRegex(ValueError, "contract provenance"):
            verify(self.dist, self.lock(), self.sources)
        self.contract["sync_mode"] = "scoped"
        self.assertEqual(verify(self.dist, self.lock(), self.sources), self.items)

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
