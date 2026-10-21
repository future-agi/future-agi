"""Pure catalog privilege assertions for the real application bootstrap."""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from app_bootstrap import is_catalog_write_denial  # noqa: E402


class CatalogPrivilegeTests(unittest.TestCase):
    def test_readonly_and_clickhouse_privilege_denials_are_expected(self):
        for message in (
            "Cannot execute query in readonly mode",
            "read-only transaction",
            "Not enough privileges. ACCESS_DENIED",
            "Code: 497 (ACCESS_DENIED)",
        ):
            with self.subTest(message=message):
                self.assertTrue(is_catalog_write_denial(RuntimeError(message)))

    def test_unrelated_failures_and_success_are_not_denials(self):
        for message in ("network timeout", "syntax error", ""):
            with self.subTest(message=message):
                self.assertFalse(is_catalog_write_denial(RuntimeError(message)))


if __name__ == "__main__":
    unittest.main()
