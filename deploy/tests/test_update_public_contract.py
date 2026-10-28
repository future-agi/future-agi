"""Closed S01 golden contract, schema parity and cross-field semantics.

jsonschema is an optional test dependency, never a host runtime dependency: the
tests that validate the schema files skip without it, and everything else runs.
Fixtures are synthetic and executable by future host/application consumers.
"""
from __future__ import annotations

import copy
import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from typing import get_args

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:  # optional: only the schema-file checks need it
    Draft202012Validator = FormatChecker = None

from deploy.update import contracts as c

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / "api_contracts" / "host-update"
FIXTURES = json.loads((DIRECTORY / "fixtures.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
SCHEMAS = {key: json.loads((DIRECTORY / filename).read_text(encoding="utf-8")) for key, filename in (
    ("status", "public-status.v1.schema.json"), ("api", "api-response.v1.schema.json"))}
FORMATS = FormatChecker() if FormatChecker is not None else None
needs_jsonschema = unittest.skipIf(Draft202012Validator is None, "optional 'jsonschema' module is not installed")


def calendar_datetime(value):
    # jsonschema silently ignores date-time when its optional RFC3339 package
    # is absent. Explicitly assert calendar validity, independently of the host
    # strptime parser. The frozen schema additionally requires exact UTC-Z form.
    if not isinstance(value, str):
        return True
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


if FORMATS is not None:
    FORMATS.checks("date-time", raises=ValueError)(calendar_datetime)
VALIDATORS = ({key: Draft202012Validator(schema, format_checker=FORMATS) for key, schema in SCHEMAS.items()}
              if Draft202012Validator is not None else {})
# Canonical hashes of the *agreed contract sections*, not regenerated from code.
SCHEMA_HASHES = {"status": "9d652e3dfc0340f05bc24586a1c156eada9c5abbfcdf4621f5d991927bbe67dc",
                 "api": "89e8fb18a177c93e05a69f881a3512477955338d8d7683c7b1d9ac27e2802ed3"}


def wire(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def sample():
    return copy.deepcopy(FIXTURES["valid_status"][0]["snapshot"])


def kwargs(case):
    return {"expected_installation": case.get("expected_installation", FIXTURES["expected_installation"]),
            "now": datetime.strptime(case.get("now", FIXTURES["now"]), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)}


class GoldenContractTests(unittest.TestCase):
    @needs_jsonschema
    def test_frozen_schemas_are_exact_contract_and_valid_2020_12(self):
        for key, schema in SCHEMAS.items():
            Draft202012Validator.check_schema(schema)
            self.assertEqual(hashlib.sha256(json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                             SCHEMA_HASHES[key])
        embedded = SCHEMAS["api"]["$defs"]["PublicStatusV1"]
        # The embedded schema omits only root dialect/id metadata.
        self.assertEqual(embedded, {key: value for key, value in SCHEMAS["status"].items()
                                    if key not in ("$id", "$schema")})

    def test_fixture_names_unique_and_contract_version_known(self):
        self.assertEqual(FIXTURES["fixture_version"], 1)
        for group in ("valid_status", "invalid_status", "valid_api", "invalid_api", "invalid_wire"):
            names = [case["name"] for case in FIXTURES[group]]
            self.assertEqual(len(names), len(set(names)), group)
            self.assertGreater(len(names), 10)

    @needs_jsonschema
    def test_valid_status_fixtures_through_actual_schema_and_stdlib(self):
        for case in FIXTURES["valid_status"]:
            with self.subTest(case=case["name"]):
                value = case["snapshot"]
                VALIDATORS["status"].validate(value)
                options = kwargs(case)
                options["previous"] = case.get("previous")
                self.assertEqual(c.decode_public_status(wire(value), **options), value)
                self.assertIsNone(c.validate_public_status(value, **options))

    @needs_jsonschema
    def test_invalid_status_fixtures_reject_even_when_shape_is_valid(self):
        for case in FIXTURES["invalid_status"]:
            with self.subTest(case=case["name"]):
                value = case["snapshot"]
                self.assertEqual(VALIDATORS["status"].is_valid(value), case["schema_valid"])
                options = kwargs(case)
                options["previous"] = case.get("previous")
                with self.assertRaises(c.ContractError) as caught:
                    c.decode_public_status(wire(value), **options)
                self.assertEqual(caught.exception.reason_code, case["reason_code"])
                self.assertEqual(str(caught.exception), case["reason_code"])

    @needs_jsonschema
    def test_valid_api_fixtures_independent_freshness_labels(self):
        for case in FIXTURES["valid_api"]:
            with self.subTest(case=case["name"]):
                value = case["response"]
                VALIDATORS["api"].validate(value)
                self.assertEqual(c.decode_api_response(wire(value), **kwargs(case)), value)
                self.assertIsNone(c.validate_api_response(value, **kwargs(case)))

    @needs_jsonschema
    def test_invalid_api_fixtures_do_not_return_unavailable_snapshots(self):
        for case in FIXTURES["invalid_api"]:
            with self.subTest(case=case["name"]):
                value = case["response"]
                self.assertEqual(VALIDATORS["api"].is_valid(value), case["schema_valid"])
                with self.assertRaises(c.ContractError) as caught:
                    c.decode_api_response(wire(value), **kwargs(case))
                self.assertEqual(caught.exception.reason_code, case["reason_code"])

    def test_invalid_wire_fixtures(self):
        for case in FIXTURES["invalid_wire"]:
            with self.subTest(case=case["name"]):
                with self.assertRaises(c.ContractError) as caught:
                    c.decode_public_status(bytes.fromhex(case["raw_hex"]), now=NOW)
                self.assertEqual(caught.exception.reason_code, case["reason_code"])

    def test_schema_is_closed_and_has_no_unbounded_free_text(self):
        def visit(schema):
            if schema.get("type") == "object":
                self.assertIs(schema["additionalProperties"], False)
                if set(schema['required']) != set(schema['properties']):
                    # Restore additions are a complete optional group. Every
                    # reader also checks the exact legacy/full alternatives.
                    self.assertTrue('anyOf' in schema or schema.get('maxProperties') == len(schema['required']))
                    if 'anyOf' in schema:
                        self.assertEqual(len(schema['anyOf']), 2)
                        self.assertEqual(schema['anyOf'][0]['maxProperties'], len(schema['required']))
                        self.assertEqual(set(schema['anyOf'][1]['required']), set(schema['properties']))
                else:
                    self.assertEqual(set(schema['required']), set(schema['properties']))
            if schema.get("type") == "string":
                self.assertTrue("enum" in schema or "const" in schema or "maxLength" in schema)
            if schema.get("type") == "array":
                self.assertIn("maxItems", schema)
            for key, value in schema.items():
                if key in ("properties", "$defs"):
                    for child in value.values():
                        visit(child)
                elif key in ("anyOf", "oneOf"):
                    for child in value:
                        visit(child)
                elif key == "items":
                    visit(value)
        for schema in SCHEMAS.values():
            visit(schema)

    def test_reason_and_component_vocabulary_matches_types(self):
        properties = SCHEMAS["status"]["properties"]
        for section in ("discovery", "download", "operation"):
            self.assertEqual(properties[section]["properties"]["reason_codes"]["items"]["enum"],
                             [reason.value for reason in c.ReasonCode])
        self.assertEqual(properties["versions"]["properties"]["components"]["items"]["properties"]["component"]["enum"],
                         list(get_args(c.ComponentName)))
        self.assertEqual(properties["operation"]["properties"]["state"]["enum"], list(get_args(c.OperationState)))
        self.assertEqual(set(c.PublicStatusV1.__annotations__), set(SCHEMAS["status"]["required"]))
        self.assertEqual(set(c.PublicAPIResultV1.__annotations__), set(SCHEMAS["api"]["properties"]["result"]["required"]))

    @needs_jsonschema
    def test_all_nested_objects_reject_unallowlisted_secret_fields(self):
        # Includes each union variant; no log/message/path hole at any depth.
        for case in FIXTURES["valid_status"]:
            def visit(value, path=()):
                if isinstance(value, dict):
                    changed = copy.deepcopy(case["snapshot"])
                    target = changed
                    for key in path:
                        target = target[key]
                    target["raw_error"] = "synthetic-private-value"
                    self.assertFalse(VALIDATORS["status"].is_valid(changed))
                    with self.assertRaises(c.ContractError) as caught:
                        c.decode_public_status(wire(changed))
                    self.assertNotIn("synthetic-private-value", str(caught.exception))
                    for key, child in value.items():
                        visit(child, path + (key,))
                elif isinstance(value, list):
                    for index, child in enumerate(value):
                        visit(child, path + (index,))
            visit(case["snapshot"])

    def test_exact_byte_limit_and_bounded_recursion(self):
        encoded = wire(sample())
        c.decode_public_status(encoded + b" " * (32768 - len(encoded)))
        with self.assertRaises(c.ContractError):
            c.decode_public_status(encoded + b" " * (32769 - len(encoded)))
        for raw in (b"[" * 5000, b"{\"x\":" + b"[" * 13 + b"0" + b"]" * 13 + b"}"):
            with self.assertRaises(c.ContractError):
                c.decode_public_status(raw)
        cycle = sample()
        cycle["cycle"] = cycle
        with self.assertRaises(c.ContractError):
            c.validate_public_status(cycle)

    def test_bool_and_float_never_wire_integers_and_no_string_coercion(self):
        for section, field in ((None, "schema_version"), (None, "sequence"), ("policy", "check_interval_seconds")):
            for value in (True, False, 1.0, "1", None):
                payload = sample()
                (payload[section] if section else payload)[field] = value
                with self.subTest(section=section, field=field, value=value), self.assertRaises(c.ContractError):
                    c.validate_public_status(payload)

    def test_identity_all_three_fields_are_exact(self):
        for field, value in (("slot_id", "a" * 32), ("topology", "distributed"), ("production", True)):
            with self.subTest(field=field), self.assertRaisesRegex(c.ContractError, "installation_mismatch"):
                c.decode_public_status(wire(sample()), expected_installation=dict(FIXTURES["expected_installation"], **{field: value}))
        with self.assertRaises(c.ContractError):
            c.decode_public_status(wire(sample()), expected_installation=dict(FIXTURES["expected_installation"], production=0))

    def test_failed_discovery_history_requires_same_installation(self):
        previous = sample()
        previous["installation"]["slot_id"] = "a" * 32
        current = sample()
        current["discovery"]["state"] = "failed"
        with self.assertRaisesRegex(c.ContractError, "installation_mismatch"):
            c.validate_public_status(current, previous=previous)

    def test_observation_skew_applies_to_all_timestamp_sources(self):
        for section, field in (("versions", "observed_at"), ("discovery", "checked_at"), ("discovery", "last_attempt_at"),
                               ("automation", "last_tick_at"), ("operation", "reported_at")):
            for seconds in (300, 301):
                payload = sample()
                payload[section][field] = (NOW + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
                if seconds == 300:
                    c.validate_public_status(payload, now=NOW)
                else:
                    with self.assertRaises(c.ContractError):
                        c.validate_public_status(payload, now=NOW)
        payload = copy.deepcopy(FIXTURES["valid_status"][6]["snapshot"])
        self.assertIsNotNone(payload["last_run"])
        payload["last_run"]["finished_at"] = "2026-10-24T12:05:01Z"
        with self.assertRaises(c.ContractError):
            c.validate_public_status(payload)

    def test_age_rounds_down_and_aware_clocks_required(self):
        value = copy.deepcopy(FIXTURES["valid_api"][0]["response"])
        c.validate_api_response(value, now=NOW + timedelta(microseconds=999999))
        with self.assertRaises(c.ContractError):
            c.validate_api_response(value, now=NOW + timedelta(seconds=1))
        for naive in (datetime(2026, 10, 24, 12), "2026-10-24T12:00:00Z", None):
            with self.assertRaises(c.ContractError):
                c.validate_api_response(value, now=naive)
        c.validate_api_response(value, now=NOW.astimezone(timezone(timedelta(hours=5, minutes=30))))

    def test_no_malformed_text_is_reflected_in_errors(self):
        for field in ("state", "stage", "reason_codes"):
            payload = sample()
            payload["operation"][field] = "synthetic-private-value"
            with self.assertRaises(c.ContractError) as caught:
                c.decode_public_status(wire(payload))
            self.assertEqual(str(caught.exception), "malformed")
            self.assertIsNone(caught.exception.__cause__)

    def test_api_decoder_enforces_same_wire_checks(self):
        raw = wire(FIXTURES["valid_api"][0]["response"])
        for value in (raw.replace(b'"status":true', b'"status":true,"status":true'), raw + b" " * 32769,
                      raw.replace(b'"age_seconds":0', b'"age_seconds":NaN'), b'\xff'):
            with self.assertRaises(c.ContractError):
                c.decode_api_response(value, now=NOW)


class PrivateValueContractTests(unittest.TestCase):
    def binding(self):
        return c.InstallationBinding(slot_id="a" * 32, selection_hash="b" * 64,
            installation_fingerprint="c" * 64, checkout=Path("/synthetic/checkout"),
            env_alias=Path("/synthetic/.env"), env_target=Path("/synthetic/deploy/.env"),
            topology="standalone", production=False, compose_project="synthetic-project",
            compose_files=[Path("compose.yml")], profiles=["serving"], docker_context="synthetic-context",
            daemon_identity="synthetic-daemon", host_uid=501)

    def prepared(self, **updates):
        raw = b'{ "version": "v1.2.3" }'
        bundle = b'{ "synthetic": true }'
        args = dict(manifest={"version": "v1.2.3", "nested": [{"key": "value"}]}, raw_manifest=raw,
            raw_bundle=bundle, manifest_sha256=hashlib.sha256(raw).hexdigest(), bundle_sha256=hashlib.sha256(bundle).hexdigest(),
            verification=c.VerificationSnapshot(True, "synthetic", "synthetic", "synthetic", "synthetic", NOW),
            binding=self.binding(), config_fingerprint="d" * 64, profile_fingerprint="e" * 64,
            deployment_fingerprint="f" * 64, trust_fingerprint="0" * 64, plan={"changes": {"key": "value"}})
        args.update(updates)
        return c.PreparedUpdate(**args)

    def test_prepared_defensively_copies_and_deep_freezes(self):
        manifest, plan = {"nested": [{"key": "value"}]}, {"changes": {"key": "value"}}
        prepared = self.prepared(manifest=manifest, plan=plan)
        manifest["nested"][0]["key"] = "changed"
        plan["changes"]["key"] = "changed"
        self.assertEqual(prepared.manifest["nested"][0]["key"], "value")
        self.assertEqual(prepared.plan["changes"]["key"], "value")
        with self.assertRaises(TypeError):
            prepared.manifest["nested"][0]["key"] = "changed"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            prepared.raw_manifest = b"changed"
        self.assertIsInstance(prepared.binding.compose_files, tuple)
        self.assertIsInstance(prepared.binding.profiles, tuple)
        with self.assertRaises(TypeError):
            json.dumps(prepared)
        # Public boundary cannot accept private dataclass objects or wholesale fields.
        with self.assertRaises(c.ContractError):
            c.validate_public_status(dataclasses.asdict(self.binding()))

    def test_exact_raw_byte_hashes_not_reserialized_json(self):
        prepared = self.prepared()
        for field, value in (("raw_manifest", prepared.raw_manifest.replace(b" ", b"")),
                             ("raw_bundle", b"different"), ("manifest_sha256", "0" * 64),
                             ("bundle_sha256", "0" * 64), ("raw_manifest", bytearray(prepared.raw_manifest))):
            with self.subTest(field=field), self.assertRaises(c.ContractError):
                self.prepared(**{field: value})

    def test_private_json_cannot_hide_nonjson_mutable_values(self):
        for value in (object(), {"x"}, float("nan"), float("inf"), {1: "key"}):
            with self.subTest(value=type(value)), self.assertRaises(c.ContractError):
                self.prepared(plan={"value": value})

    def test_protocols_are_not_operational_implementations(self):
        for interface in (c.AutomationGuard, c.LockedContext):
            with self.assertRaises(TypeError):
                interface()
        self.assertFalse(hasattr(c, "HostRunner"))
        self.assertFalse(hasattr(c, "publish_public_status"))

    def test_invalid_private_values_fail_closed(self):
        for changes in ({"production": True}, {"slot_id": "not-a-slot"}, {"host_uid": True},
                        {"compose_files": ["not-a-path"]}, {"profiles": [{}]}):
            with self.subTest(changes=changes), self.assertRaises(c.ContractError):
                dataclasses.replace(self.binding(), **changes)
        for changes in ({"verified": 1}, {"checked_at": datetime(2026, 10, 24)}, {"tool": []}):
            with self.subTest(changes=changes), self.assertRaises(c.ContractError):
                dataclasses.replace(self.prepared().verification, **changes)
        for changes in ({"disposition": "pretend_success"}, {"target_version": "v1.2.3 "},
                        {"target_version": "unvalidated"}, {"operation_token": []}, {"reason_codes": ["raw message"]}):
            with self.subTest(changes=changes), self.assertRaises(c.ContractError):
                dataclasses.replace(c.RunOutcome("blocked"), **changes)
        event = c.OperationEvent("blocked", None, None, NOW, "private-token")
        for changes in ({"state": "pretend_success"}, {"kind": "install"}, {"stage": "raw message"},
                        {"reported_at": datetime(2026, 10, 24)}, {"operation_token": []}, {"reason_codes": "raw message"}):
            with self.subTest(changes=changes), self.assertRaises(c.ContractError):
                dataclasses.replace(event, **changes)

    def test_event_and_outcome_defensively_copy_reasons(self):
        reasons = [c.ReasonCode.PROCESS_FAILED]
        event = c.OperationEvent("failed", "apply", "rollout", NOW, "private-token", reasons)
        result = c.RunOutcome("failed", reasons, "private-token", "v1.2.3")
        reasons.clear()
        self.assertEqual(event.reason_codes, (c.ReasonCode.PROCESS_FAILED,))
        self.assertEqual(result.reason_codes, event.reason_codes)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            event.state = "succeeded"
        self.assertNotIn("operation_token", SCHEMAS["status"]["properties"]["operation"]["properties"])


if __name__ == "__main__":
    unittest.main()
