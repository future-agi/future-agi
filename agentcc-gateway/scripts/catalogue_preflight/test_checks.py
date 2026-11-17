"""Network-free stdlib unit tests; NEVER import or invoke the route launcher."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest


def load(name):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("catalogue_unit_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checks = load("checks")
mutation = load("assertion_red")


class FixtureAssertions(unittest.TestCase):
    def setUp(self):
        self.before = {"own": "before", "foreign": "unchanged"}
        self.first = {"probe_calls": 0, "resolver_calls": 0, "dns_queries": 0,
            "dns_failures": 0, "tools_list": [], "tls": [], "client_hello": [], "seal": "unit-seal",
            "synthetic_physical_attempts": 0, "name": "cn30a-docs.test"}
        self.last = dict(self.first, probe_calls=1, resolver_calls=1, dns_queries=1)
        self.error = {"code": "connector_tool_listing_failed", "message": "egress_refused"}

    def evaluate(self, **kw):
        args = dict(case="metadata", status=502, body=self.error, before=self.before,
            after=self.before, first=self.first, last=self.last, credential="synthetic-unit-value")
        args.update(kw)
        return checks.evaluate(**args)

    def test_refusal_and_no_write(self):
        self.assertEqual(self.evaluate(), [])
        self.assertIn("no_write", self.evaluate(after={"own": "changed", "foreign": "unchanged"}))
        self.assertIn("product_refusal", self.evaluate(body={"code": "connector_tool_listing_failed", "message": "unreachable"}))

    def test_missing_path_witnesses_fail(self):
        for field in ("probe_calls", "resolver_calls", "dns_queries"):
            last = dict(self.last, **{field: 0})
            self.assertTrue(self.evaluate(last=last), field)
        self.assertIn("registration_unchanged", self.evaluate(last=dict(self.last, seal="changed")))
        self.assertIn("dns_fixture_valid", self.evaluate(last=dict(self.last, dns_failures=1)))

    def test_positive_requires_status_tls_and_mock_credential(self):
        secret = "synthetic-unit-value"
        last = dict(self.last, client_hello=["cn30a-docs.test"], tls=[{"sni": "cn30a-docs.test"}], tools_list=[{
            "method": "tools/list", "auth": {"sha256": hashlib.sha256(("Bearer " + secret).encode()).hexdigest()}}])
        body = {"result": {"baseline": {"tool_count": 5, "tools_hash": "a" * 64},
            "gateway_synced": True, "mcp_enabled": True, "credential": {"connected": True}}}
        args = dict(case="positive", status=201, body=body, last=last,
            after={"own": "after", "foreign": "unchanged"})
        self.assertEqual(self.evaluate(**args), [])
        for field, value in (("tls", []), ("tools_list", [])):
            self.assertTrue(self.evaluate(**dict(args, last=dict(last, **{field: value}))))
        self.assertIn("actual_201", self.evaluate(**dict(args, status=502)))
        malformed = copy.deepcopy(body)
        malformed["result"]["baseline"]["tools_hash"] = "z" * 64
        self.assertIn("five_tools", self.evaluate(**dict(args, body=malformed)))
        bad = copy.deepcopy(last)
        bad["tools_list"][0]["auth"]["sha256"] = "wrong"
        self.assertIn("real_mock_credential", self.evaluate(**dict(args, last=bad)))

    def test_tls_negatives_keep_502_and_no_write(self):
        body = {"code": "connector_tool_listing_failed", "message": "unreachable"}
        for case in ("tls-wrong-san", "tls-untrusted-ca"):
            last = dict(self.last, client_hello=["cn30a-docs.test"])
            self.assertEqual(self.evaluate(case=case, body=body, last=last), [])
            self.assertIn("tls_handshake_reached", self.evaluate(case=case, body=body))
            self.assertIn("product_refusal", self.evaluate(case=case, body=body, last=last, status=201))

    def test_localhost_and_member_are_pre_probe(self):
        self.assertEqual(self.evaluate(case="localhost", status=400, body={"code": "url_variable_invalid"}, last=self.first), [])
        self.assertEqual(self.evaluate(case="member", status=403, body={}, last=self.first), [])
        self.assertIn("real_probe_route", self.evaluate(case="member", status=403, body={}))

    def test_mutation_changes_only_policy_entry(self):
        source = ("package egress\n" + mutation.SIGNATURE + "\treturn original(host)\n}\n").encode()
        sha = hashlib.sha256(source).hexdigest()
        result = mutation.mutated_check(source, sha)
        self.assertEqual(result.replace(mutation.MUTATION.encode(), b"", 1), source)
        with self.assertRaises(ValueError):
            mutation.mutated_check(source, "wrong")
        with self.assertRaises(ValueError):
            mutation.mutated_check(result, hashlib.sha256(result).hexdigest())

    def test_red_requires_actual_synthetic_reach(self):
        green = {"case": "synthetic-metadata", "intent": "unit-only-intent", "failures": [],
            "last": dict(self.last, name="metadata.google.internal"),
            "ordinary_nonloopback_attempts": 0,
            "native_reconciliation": {"accepted": True, "dropped_events": 0}}
        red = copy.deepcopy(green)
        red.update(status=201, failures=["product_refusal"], credential_sha256="unit-fingerprint")
        red["last"].update(synthetic_physical_attempts=1, tools_list=[{"method": "tools/list", "auth": {"sha256": "unit-fingerprint"}}], tls=[{"sni": "metadata.google.internal"}], client_hello=["metadata.google.internal"])
        self.assertTrue(checks.assertion_red(green, red, green)["assertion_red"])
        for field, value in (("synthetic_physical_attempts", 0), ("tools_list", []), ("tls", [])):
            wrong = copy.deepcopy(red)
            wrong["last"][field] = value
            with self.assertRaises(AssertionError):
                checks.assertion_red(green, wrong, green)


if __name__ == "__main__":
    unittest.main()
