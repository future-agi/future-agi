"""Offline fallibility checks for the composed attachment pins (run on the VM)."""
import copy
import importlib.util
import re
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
spec = importlib.util.spec_from_file_location("attachment_source_checks", HERE / "source_checks.py")
src = importlib.util.module_from_spec(spec)
spec.loader.exec_module(src)


class ComposedPins(unittest.TestCase):
    def matched(self, relative, text):
        patterns = [(name, pattern) for name, path, pattern in src.CODE_PATTERNS if path == relative]
        self.assertTrue(patterns, relative)
        return all(re.search(pattern, text, re.S) is not None for _, pattern in patterns)

    def test_product_source_pins_match(self):
        for relative in (
            "middleware/auth.go", "middleware/auth_errors.go", "models/errors.go",
            "server/router.go", "server/router_hints.go",
        ):
            with self.subTest(file=relative):
                path = src.GW + relative
                self.assertTrue(self.matched(path, (ROOT / path).read_text()))

    def test_one_sided_source_changes_are_refused(self):
        mutations = [
            ("middleware/auth.go", 'if scopes.signsInOwnCallers(r.URL.Path) {', 'if false {'),
            ("middleware/auth.go", '&models.APIError{Status: http.StatusServiceUnavailable,', '&models.APIError{Status: http.StatusOK,'),
            ("middleware/auth.go", '}\n\t\t\t\twriteGateError(w, r, ErrUnauthorizedKeyFor(r.URL.Path, rawKey))', '}\n\t\t\t\tmodels.WriteError(w, ErrUnauthorizedKeyFor(r.URL.Path, rawKey))'),
            ("middleware/auth.go", 'ipACL.CheckClient(clientIP, "", nil, requestID)', 'ipACL.CheckClient(clientIP, "wrong", nil, requestID)'),
            ("middleware/auth.go", 'ipACL.CheckClient(clientIP, identity.OrgID, apiKey.AllowedIPs, requestID)', 'ipACL.CheckClient(clientIP, identity.OrgID, nil, requestID)'),
            ("middleware/auth_errors.go", 'models.WriteError(w, apiErr)', 'anthropicfmt.WriteAPIError(w, apiErr)'),
            ("middleware/auth_errors.go", '"/v1/messages/"', '"/mcp/"'),
            ("middleware/auth_errors.go", '"unknown_api_key"', '"missing_api_key"'),
            ("middleware/auth_errors.go", 'mistyped, revoked or deleted.', 'invalid.'),
            ("middleware/auth_errors.go", 'map[string]string{"reason": reason}', 'map[string]string{"other": reason}'),
            ("models/errors.go", 'newAPIError(http.StatusUnauthorized, ErrTypeAuthentication, "invalid_api_key", message)', 'newAPIError(http.StatusForbidden, ErrTypeAuthentication, "invalid_api_key", message)'),
            ("models/errors.go", 'w.Header().Set("WWW-Authenticate", BearerChallenge)', 'w.Header().Set("X-Challenge", BearerChallenge)'),
            ("models/errors.go", 'w.WriteHeader(err.Status)', 'w.WriteHeader(http.StatusOK)'),
            ("server/router.go", 'rt.unknownEndpointMessage(method, path)', '"Unknown endpoint: "+path'),
            ("server/router_hints.go", '\n\treturn msg + "."\n}', '\n\treturn msg\n}'),
        ]
        for relative, before, after in mutations:
            with self.subTest(file=relative, change=before):
                path = src.GW + relative
                original = (ROOT / path).read_text()
                self.assertEqual(original.count(before), 1)
                self.assertFalse(self.matched(path, original.replace(before, after)))

    def test_complete_golden_envelopes_match(self):
        checks = src.protocol_fixture_checks(str(ROOT))
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(result is True for _, result in checks))

    def test_one_sided_fixture_changes_are_refused_without_digest_help(self):
        real_load = src.load_json
        mutations = [
            ("401-invalid-api-key.json", ("http", "status"), 403),
            ("401-invalid-api-key.json", ("http", "headers", "www-authenticate"), "Basic"),
            ("401-invalid-api-key.json", ("body", "error", "code"), "old_code"),
            ("401-invalid-api-key.json", ("body", "error", "message"), "Invalid or missing API key"),
            ("401-invalid-api-key.json", ("body", "error", "metadata"), None),
            ("403-ip-not-allowed.json", ("body", "error", "type"), "authentication_error"),
            ("404-mcp-not-served.json", ("body", "error", "message"), "Unknown endpoint: /mcp/v/vs_01K7CN5EEAGENTM0DE00000001"),
            ("404-mcp-not-served.json", ("body", "error", "param"), "path"),
        ]
        for filename, fields, value in mutations:
            with self.subTest(file=filename, field=fields):
                def changed(path):
                    doc = copy.deepcopy(real_load(path))
                    if Path(path).name == filename:
                        target = doc
                        for field in fields[:-1]:
                            target = target[field]
                        target[fields[-1]] = value
                    return doc
                with patch.object(src, "load_json", side_effect=changed):
                    checks = src.protocol_fixture_checks(str(ROOT))
                self.assertEqual(sum(result is not True for _, result in checks), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
