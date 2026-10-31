"""S09 reader/contract tests plus real ORM/bearer endpoint tests.

ReaderTests/FileTests run on plain Python without Django or jsonschema.
EndpointTests require the project Django runner and an isolated test database;
missing backend prerequisites are failures, never skip-to-green permission tests.
"""

import copy
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tfc.host_updates import status

UTC = timezone.utc  # noqa: UP017 -- standalone reader tests also run on Python 3.9.
ROOT = Path(__file__).resolve().parents[3]
CONTRACTS = ROOT / "api_contracts" / "host-update"
FIXTURES = json.loads((CONTRACTS / "fixtures.json").read_text())
NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
IDENTITY = FIXTURES["expected_installation"]


def raw(snapshot):
    return json.dumps(snapshot).encode()


def decode(snapshot, identity=IDENTITY, now=NOW):
    return status.decode_public_status(
        raw(snapshot), expected_installation=identity, now=now
    )


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = copy.deepcopy(FIXTURES["valid_status"][0]["snapshot"])

    def test_packaged_schemas_equal_frozen_s01(self):
        public = json.loads((CONTRACTS / "public-status.v1.schema.json").read_text())
        for key in ("$id", "$schema", "title"):
            public.pop(key)
        self.assertEqual(status.PUBLIC_SCHEMA, public)
        api = json.loads((CONTRACTS / "api-response.v1.schema.json").read_text())
        for key in ("$id", "$schema", "$defs"):
            api.pop(key)
        api["properties"]["result"]["properties"]["snapshot"]["anyOf"][0] = public
        self.assertEqual(status.RESPONSE_SCHEMA, api)

    def test_restore_groups_are_closed_and_do_not_hide_forward_failure(self):
        snapshot = self.snapshot
        snapshot["operation"]["phase"] = "restored"
        snapshot["safety"].update(
            fallback_strategy="restore_backup",
            restore_state="verified",
            snapshot_at=snapshot["generated_at"],
            loss_window_start=snapshot["generated_at"],
            loss_window_end=snapshot["generated_at"],
            loss_window_qualification="bounded",
            guidance_id="none",
            estimates=None,
            failed_target_version="v1.52.0",
        )
        snapshot["last_run"] = {
            "kind": "apply",
            "automatic": True,
            "outcome": "failed_during_rollout",
            "from_version": "v1.51.0",
            "target_version": "v1.52.0",
            "finished_at": snapshot["generated_at"],
            "fallback_outcome": "none",
            "forward_result": "failed",
            "restore_result": "verified",
            "effective_version": "v1.51.0",
            "failed_target_version": "v1.52.0",
        }
        self.assertEqual(decode(snapshot), snapshot)
        for mutate in (
            lambda s: s["safety"].pop("guidance_id"),
            lambda s: s["safety"].update(archive_path="/private/backup"),
            lambda s: s["operation"].update(phase="guess"),
            lambda s: s["last_run"].update(restore_result="pretend_success"),
            lambda s: s["safety"].update(loss_window_end="2026-10-23T00:00:00Z"),
            lambda s: s["safety"].update(restore_state="running"),
        ):
            changed = copy.deepcopy(snapshot)
            mutate(changed)
            with self.assertRaises(status.StatusUnavailable):
                decode(changed)

    def test_a6_estimates_are_only_bounded_integers_and_reasons_are_closed(self):
        public = json.loads((CONTRACTS / "public-status.v1.schema.json").read_text())
        reasons = public["properties"]["discovery"]["properties"]["reason_codes"][
            "items"
        ]["enum"]
        self.assertEqual(status.REASONS, tuple(reasons))
        shape = status.PUBLIC_SCHEMA["properties"]["safety"]["properties"]["estimates"][
            "anyOf"
        ][0]
        value = dict.fromkeys(status.ESTIMATE_FIELDS, 10)
        self.assertEqual(status._shape(value, shape), value)
        for bad in (-1, 1.0, True, float("inf"), 9007199254740992):
            with self.assertRaises(status.StatusUnavailable):
                status._shape(dict(value, total_seconds=bad), shape)
        with self.assertRaises(status.StatusUnavailable):
            status._shape(dict(value, private_table="secret"), shape)

    def test_all_valid_status_fixtures(self):
        for row in FIXTURES["valid_status"]:
            with self.subTest(row["name"]):
                value = decode(
                    row["snapshot"], row.get("expected_installation", IDENTITY)
                )
                self.assertEqual(value, row["snapshot"])
                self.assertIsNot(value, row["snapshot"])

    def test_invalid_single_observation_fixtures(self):
        for row in FIXTURES["invalid_status"]:
            if "previous" in row:
                # Two producer-CAS cases require private prior state. A stateless
                # reader must not invent history or treat old public data as it.
                continue
            with self.subTest(row["name"]):
                with self.assertRaises(status.StatusUnavailable) as caught:
                    decode(row["snapshot"], row.get("expected_installation", IDENTITY))
                self.assertEqual(caught.exception.reason, row["reason_code"])

    def test_history_is_not_inferred_from_a_single_file(self):
        rows = [row for row in FIXTURES["invalid_status"] if "previous" in row]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(decode(row["snapshot"]), row["snapshot"])

    def test_all_invalid_wire_fixtures(self):
        for row in FIXTURES["invalid_wire"]:
            with self.subTest(row["name"]):
                with self.assertRaises(status.StatusUnavailable) as caught:
                    status.decode_public_status(
                        bytes.fromhex(row["raw_hex"]),
                        expected_installation=IDENTITY,
                        now=NOW,
                    )
                self.assertEqual(caught.exception.reason, row["reason_code"])

    def test_api_fixtures_exact_output(self):
        for row in FIXTURES["valid_api"]:
            with self.subTest(row["name"]):
                expected = row["response"]
                result = expected["result"]
                if result["snapshot"] is not None:
                    actual = status.available_response(result["snapshot"], now=NOW)
                else:
                    actual = status.unavailable_response(result["reason_code"])
                self.assertEqual(actual, expected)
                self.assertEqual(status._shape(actual, status.RESPONSE_SCHEMA), actual)

    @unittest.skipUnless(
        importlib.util.find_spec("jsonschema"), "optional jsonschema is not installed"
    )
    def test_emitted_responses_against_jsonschema(self):
        from jsonschema import Draft202012Validator, FormatChecker

        schema = json.loads((CONTRACTS / "api-response.v1.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for row in FIXTURES["valid_status"]:
            validator.validate(
                status.available_response(
                    decode(row["snapshot"], row.get("expected_installation", IDENTITY)),
                    now=NOW,
                )
            )
        for reason in status.API_REASONS:
            validator.validate(status.unavailable_response(reason))

    def test_heartbeat_does_not_refresh_old_observations(self):
        self.snapshot["versions"]["observed_at"] = "2026-10-23T11:59:59Z"
        self.snapshot["discovery"]["checked_at"] = "2026-10-23T11:59:59Z"
        self.snapshot["operation"].update(
            state="applying", reported_at="2026-10-24T11:54:59Z"
        )
        result = status.available_response(decode(self.snapshot), now=NOW)["result"]
        self.assertEqual(result["freshness"], "fresh")
        for key in ("discovery_freshness", "versions_freshness", "progress_freshness"):
            self.assertEqual(result[key], "stale")
        self.assertEqual(result["snapshot"]["operation"]["state"], "applying")

    def test_exact_freshness_thresholds(self):
        for maximum in (300, 86400, 21900):
            self.assertEqual(
                status._freshness(
                    "2026-10-24T12:00:00Z", NOW + timedelta(seconds=maximum), maximum
                ),
                "fresh",
            )
            self.assertEqual(
                status._freshness(
                    "2026-10-24T12:00:00Z",
                    NOW + timedelta(seconds=maximum + 1),
                    maximum,
                ),
                "stale",
            )

    def test_app_identity_omits_host_reported_production(self):
        for topology, production in (
            ("standalone", False),
            ("distributed", False),
            ("distributed", True),
        ):
            with self.subTest(topology=topology, production=production):
                self.snapshot["installation"].update(
                    topology=topology, production=production
                )
                expected = {"slot_id": IDENTITY["slot_id"], "topology": topology}
                self.assertEqual(decode(self.snapshot, expected), self.snapshot)
                with patch.object(
                    status, "_read_file", return_value=raw(self.snapshot)
                ):
                    response = status.read_public_status(
                        configured_file=status.STATUS_FILE,
                        expected_installation=expected,
                        clock=lambda: NOW,
                    )
                self.assertEqual(
                    response, status.available_response(self.snapshot, now=NOW)
                )
                # The reader still supports callers with an independent expected
                # production fact. Absence is optional, not a wildcard for bad data.
                with self.assertRaises(status.StatusUnavailable) as caught:
                    decode(self.snapshot, {**expected, "production": not production})
                self.assertEqual(caught.exception.reason, "installation_mismatch")

    def test_optional_production_keeps_identity_and_snapshot_checks_closed(self):
        expected = {key: IDENTITY[key] for key in ("slot_id", "topology")}
        for key, value in (("slot_id", "f" * 32), ("topology", "distributed")):
            with self.subTest(key=key):
                with self.assertRaises(status.StatusUnavailable) as caught:
                    decode(self.snapshot, {**expected, key: value})
                self.assertEqual(caught.exception.reason, "installation_mismatch")
        for identity in (
            None,
            [],
            {},
            {"slot_id": IDENTITY["slot_id"]},
            {"topology": "standalone"},
            {**expected, "slot_id": ""},
            {**expected, "topology": "unknown"},
            {**expected, "production": None},
            {**expected, "production": 0},
            {**expected, "production": "false"},
            {**expected, "extra": "CANARY_SECRET"},
        ):
            with (
                self.subTest(identity=identity),
                patch.object(
                    status, "_read_file", side_effect=AssertionError("must not open")
                ),
            ):
                self.assertEqual(
                    status.read_public_status(
                        configured_file=status.STATUS_FILE,
                        expected_installation=identity,
                    ),
                    status.unavailable_response("installation_mismatch"),
                )
        for production in (True, None, 0, "false"):
            with self.subTest(production=production):
                self.snapshot["installation"]["production"] = production
                with self.assertRaises(status.StatusUnavailable) as caught:
                    decode(self.snapshot, expected)
                self.assertEqual(caught.exception.reason, "malformed")
        del self.snapshot["installation"]["production"]
        with self.assertRaises(status.StatusUnavailable) as caught:
            decode(self.snapshot, expected)
        self.assertEqual(caught.exception.reason, "malformed")

    def test_identity_clock_and_unavailable_timezone(self):
        for key, value in (
            ("slot_id", "f" * 32),
            ("topology", "distributed"),
            ("production", True),
        ):
            with self.subTest(key), self.assertRaises(status.StatusUnavailable):
                decode(self.snapshot, {**IDENTITY, key: value})
        for clock in (
            lambda: None,
            lambda: datetime(2026, 10, 24),
            lambda: (_ for _ in ()).throw(OSError("SECRET")),
        ):
            result = status.read_public_status(
                configured_file=status.STATUS_FILE,
                expected_installation=IDENTITY,
                clock=clock,
            )
            self.assertEqual(result, status.unavailable_response("clock_skew"))
        self.snapshot["automation"]["maintenance_window"] = {
            "kind": "always",
            "timezone": "Unavailable/Zone",
        }
        with self.assertRaises(status.StatusUnavailable) as caught:
            decode(self.snapshot)
        self.assertEqual(caught.exception.reason, "malformed")

    def test_fixed_mount_and_missing_configuration_never_open(self):
        with patch.object(
            status, "_read_file", side_effect=AssertionError("must not open")
        ):
            for path, reason in (
                ("", "not_configured"),
                ("/private/SECRET", "untrusted_file"),
            ):
                self.assertEqual(
                    status.read_public_status(
                        configured_file=path, expected_installation=IDENTITY
                    ),
                    status.unavailable_response(reason),
                )
            self.assertEqual(
                status.read_public_status(
                    configured_file=status.STATUS_FILE, expected_installation={}
                ),
                status.unavailable_response("installation_mismatch"),
            )

    def test_error_reasons_never_expose_canary_or_payload(self):
        for key in self.snapshot:
            value = copy.deepcopy(self.snapshot)
            value[key] = "CANARY_SECRET /private/host backup"
            with patch.object(status, "_read_file", return_value=raw(value)):
                response = status.read_public_status(
                    configured_file=status.STATUS_FILE,
                    expected_installation=IDENTITY,
                    clock=lambda: NOW,
                )
            self.assertEqual(response["result"]["snapshot"], None)
            self.assertNotIn("CANARY", json.dumps(response))
        for reason in status.API_REASONS:
            with patch.object(
                status, "_read_file", side_effect=status.StatusUnavailable(reason)
            ):
                self.assertEqual(
                    status.read_public_status(
                        configured_file=status.STATUS_FILE,
                        expected_installation=IDENTITY,
                        clock=lambda: NOW,
                    ),
                    status.unavailable_response(reason),
                )


class FileTests(unittest.TestCase):
    def setUp(self):
        # Native filesystem, no filesystem qualification mocks. Avoid symlinked
        # or world-writable system tmp ancestors, which the reader must reject.
        base = Path(os.environ.get("FI_HOST_UPDATE_TEST_TMPDIR", Path.home())).resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="s09-status-test-", dir=base)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.file = self.directory / "status.json"
        self.payload = raw(FIXTURES["valid_status"][0]["snapshot"])
        self.file.write_bytes(self.payload)
        self.file.chmod(0o644)

    def test_regular_file(self):
        self.assertEqual(status._read_file(str(self.file)), self.payload)

    def test_missing(self):
        self.file.unlink()
        with self.assertRaises(status.StatusUnavailable) as caught:
            status._read_file(str(self.file))
        self.assertEqual(caught.exception.reason, "not_reported")

    def test_symlink_file_and_ancestor(self):
        link = self.directory / "link"
        link.symlink_to(self.file)
        with self.assertRaises(status.StatusUnavailable):
            status._read_file(str(link))
        alias = self.directory / "alias"
        alias.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(status.StatusUnavailable):
            status._read_file(str(alias / "status.json"))

    def test_fifo_directory_device_hardlink_and_oversize(self):
        fifo = self.directory / "fifo"
        os.mkfifo(fifo)
        hardlink = self.directory / "hardlink"
        os.link(self.file, hardlink)
        for path in (fifo, self.directory, Path("/dev/null"), hardlink):
            with self.subTest(path.name), self.assertRaises(status.StatusUnavailable):
                status._read_file(str(path))
        hardlink.unlink()
        self.file.write_bytes(b" " * (status.MAX_BYTES + 1))
        with self.assertRaises(status.StatusUnavailable) as caught:
            status._read_file(str(self.file))
        self.assertEqual(caught.exception.reason, "malformed")

    def test_writable_file_and_ancestor(self):
        for mode in (0o664, 0o646):
            self.file.chmod(mode)
            with self.assertRaises(status.StatusUnavailable):
                status._read_file(str(self.file))
        self.file.chmod(0o644)
        self.directory.chmod(0o775)
        with self.assertRaises(status.StatusUnavailable):
            status._read_file(str(self.file))

    def test_atomic_replacement_returns_opened_complete_inode(self):
        replacement = self.directory / "replacement"
        replacement.write_bytes(b"new complete inode")
        original_read = os.read
        replaced = False

        def replace_after_open(fd, count):
            nonlocal replaced
            if not replaced:
                os.replace(replacement, self.file)
                replaced = True
            return original_read(fd, count)

        with patch.object(status.os, "read", side_effect=replace_after_open):
            self.assertEqual(status._read_file(str(self.file)), self.payload)
        self.assertEqual(status._read_file(str(self.file)), b"new complete inode")

    def test_in_place_change_during_read_rejected(self):
        original_read = os.read
        changed = False

        def mutate(fd, count):
            nonlocal changed
            data = original_read(fd, count)
            if not changed:
                self.file.write_bytes(b"changed")
                changed = True
            return data

        with (
            patch.object(status.os, "read", side_effect=mutate),
            self.assertRaises(status.StatusUnavailable),
        ):
            status._read_file(str(self.file))

    def test_read_is_bounded_even_if_file_grows(self):
        calls = []
        bytes_read = []
        original_read = os.read

        def grow_after_size_check(fd, count):
            if not calls:
                self.file.write_bytes(b" " * (status.MAX_BYTES * 2))
            calls.append(count)
            chunk = original_read(fd, count)
            bytes_read.append(len(chunk))
            return chunk

        with (
            patch.object(status.os, "read", side_effect=grow_after_size_check),
            self.assertRaises(status.StatusUnavailable) as caught,
        ):
            status._read_file(str(self.file))
        self.assertEqual(caught.exception.reason, "malformed")
        self.assertEqual(sum(bytes_read), status.MAX_BYTES + 1)
        self.assertLessEqual(max(calls), status.MAX_BYTES + 1)


class SerializerTests(unittest.TestCase):
    def test_checkedin_host_swagger_definition_matches_actual_serializer(self):
        from tfc.host_updates.serializers import HostUpdateStatusResponseSerializer

        swagger = json.loads((ROOT / "api_contracts/openapi/swagger.json").read_text())
        self.assertEqual(
            swagger["definitions"]["HostUpdateStatusResponse"],
            HostUpdateStatusResponseSerializer.Meta.swagger_schema_fields,
        )

    def test_swagger_accepts_complete_restore_objects_and_retains_exact_unions(self):
        from jsonschema import Draft4Validator

        from tfc.host_updates.serializers import HostUpdateStatusResponseSerializer

        swagger = json.loads((ROOT / "api_contracts/openapi/swagger.json").read_text())
        schema = swagger["definitions"]["HostUpdateStatusResponse"]
        response = copy.deepcopy(FIXTURES["valid_api"][0]["response"])
        snapshot = response["result"]["snapshot"]
        snapshot["operation"].update(
            state="restored",
            kind="restore",
            stage="restore",
            phase="restored",
            started_at=snapshot["generated_at"],
            finished_at=snapshot["generated_at"],
            reported_at=snapshot["generated_at"],
        )
        snapshot["safety"].update(
            backup_state="sealed",
            fallback_strategy="restore_backup",
            restore_state="verified",
            snapshot_at=snapshot["generated_at"],
            loss_window_start=snapshot["generated_at"],
            loss_window_end=snapshot["generated_at"],
            loss_window_qualification="bounded",
            guidance_id="none",
            estimates=dict.fromkeys(status.ESTIMATE_FIELDS, 10),
            failed_target_version="v1.52.0",
        )
        snapshot["last_run"] = {
            "kind": "apply",
            "automatic": True,
            "outcome": "failed_during_rollout",
            "from_version": "v1.51.0",
            "target_version": "v1.52.0",
            "finished_at": snapshot["generated_at"],
            "fallback_outcome": "none",
            "forward_result": "failed",
            "restore_result": "verified",
            "effective_version": "v1.51.0",
            "failed_target_version": "v1.52.0",
        }
        self.assertEqual(status._shape(response, status.RESPONSE_SCHEMA), response)
        serializer = HostUpdateStatusResponseSerializer(data=response)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        documented = schema["properties"]["result"]["properties"]["snapshot"]
        # Reviewer's raw Draft4 reproduction: no complete object can inherit the
        # legacy maxProperties. No nulls in operation; even extension-unaware
        # Swagger 2 tools must accept its eight fields.
        Draft4Validator(documented["properties"]["operation"]).validate(
            snapshot["operation"]
        )
        for name in ("operation", "safety", "last_run"):
            with self.subTest(name=name):
                group = documented["properties"][name]
                self.assertNotIn("maxProperties", group)
                self.assertEqual(len(group["x-anyOf"]), 2)
                self.assertIn("maxProperties", group["x-anyOf"][0])
        for name in ("operation", "safety"):
            self.assertNotIn("x-nullable", documented["properties"][name])

        def native(value):
            if isinstance(value, list):
                return [native(item) for item in value]
            if not isinstance(value, dict):
                return value
            converted = {
                key: native(item) for key, item in value.items() if key != "x-nullable"
            }
            for vendor, keyword in (("x-anyOf", "anyOf"), ("x-oneOf", "oneOf")):
                if vendor in converted:
                    converted[keyword] = converted.pop(vendor)
            return (
                {"anyOf": [converted, {"type": "null"}]}
                if value.get("x-nullable")
                else converted
            )

        validator = Draft4Validator(native(schema))
        validator.validate(response)
        for row in FIXTURES["valid_api"]:
            with self.subTest(legacy=row["name"]):
                validator.validate(row["response"])
        for name in ("operation", "safety"):
            changed = copy.deepcopy(response)
            changed["result"]["snapshot"][name] = None
            self.assertFalse(validator.is_valid(changed))
        for name, key in (("safety", "guidance_id"), ("last_run", "restore_result")):
            changed = copy.deepcopy(response)
            del changed["result"]["snapshot"][name][key]
            self.assertFalse(validator.is_valid(changed))

    def test_response_nullable_roundtrip_and_unknown_rejection(self):
        from tfc.host_updates.serializers import HostUpdateStatusResponseSerializer

        for row in FIXTURES["valid_api"]:
            with self.subTest(row["name"]):
                serializer = HostUpdateStatusResponseSerializer(data=row["response"])
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.data, row["response"])
        value = copy.deepcopy(FIXTURES["valid_api"][0]["response"])
        value["result"]["snapshot"]["safety"]["private"] = "CANARY_SECRET"
        serializer = HostUpdateStatusResponseSerializer(data=value)
        self.assertFalse(serializer.is_valid())
        self.assertNotIn("CANARY_SECRET", str(serializer.errors))

    def test_host_reported_production_remains_a_required_boolean(self):
        from tfc.host_updates.serializers import HostUpdateStatusResponseSerializer

        value = copy.deepcopy(FIXTURES["valid_api"][0]["response"])
        installation = value["result"]["snapshot"]["installation"]
        installation["topology"] = "distributed"
        for production in (False, True):
            installation["production"] = production
            serializer = HostUpdateStatusResponseSerializer(data=value)
            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertIs(
                serializer.validated_data["result"]["snapshot"]["installation"][
                    "production"
                ],
                production,
            )
        for production in (None, 0, "false"):
            installation["production"] = production
            self.assertFalse(HostUpdateStatusResponseSerializer(data=value).is_valid())
        del installation["production"]
        self.assertFalse(HostUpdateStatusResponseSerializer(data=value).is_valid())

    def test_swagger_nullability_closed_objects_and_required_fields(self):
        from tfc.host_updates.serializers import HostUpdateStatusResponseSerializer

        schema = HostUpdateStatusResponseSerializer.Meta.swagger_schema_fields
        result = schema["properties"]["result"]
        self.assertFalse(result["additionalProperties"])
        self.assertTrue(result["properties"]["snapshot"]["x-nullable"])
        self.assertTrue(result["properties"]["age_seconds"]["x-nullable"])
        self.assertIn("snapshot", result["required"])
        snapshot = result["properties"]["snapshot"]
        self.assertTrue(snapshot["properties"]["last_run"]["x-nullable"])
        self.assertEqual(snapshot["properties"]["schema_version"]["enum"], [1])
        window = snapshot["properties"]["automation"]["properties"][
            "maintenance_window"
        ]
        variants = window["x-oneOf"]
        self.assertEqual(
            [item["properties"]["kind"]["enum"] for item in variants],
            [["always"], ["interval"]],
        )
        self.assertEqual(set(variants[0]["required"]), {"kind", "timezone"})
        self.assertEqual(
            set(variants[1]["required"]),
            {"kind", "timezone", "days_of_week", "start", "end"},
        )


# These expected wire values intentionally do not call the envelope builder: a
# change to its aliases or classification must fail this endpoint's contract.
ERROR_CASES = (
    (
        400,
        "invalid",
        "This endpoint accepts no query parameters or body.",
        "validation_error",
    ),
    (401, "not_authenticated", "Authentication required.", "authentication_error"),
    (
        403,
        "permission_denied",
        "Host update status is not authorized.",
        "permission_error",
    ),
    (403, "2fa_required", "2FA required", "permission_error"),
    (
        403,
        "saml_scope_conflict",
        "Organization selection is not authorized.",
        "permission_error",
    ),
    (404, "not_found", "Not found.", "not_found"),
    (405, "method_not_allowed", "Method not allowed.", "api_error"),
    (503, "service_unavailable", "Status service unavailable.", "service_unavailable"),
)


def error_body(code):
    _, _, detail, error_type = next(row for row in ERROR_CASES if row[1] == code)
    return {
        "status": False,
        "type": error_type,
        "code": code,
        "detail": detail,
        "message": detail,
        "error": detail,
        "result": detail,
    }


class ErrorContractTests(unittest.TestCase):
    def test_exact_safe_envelopes_use_shared_builder_and_rollback(self):
        from rest_framework.exceptions import APIException
        from rest_framework.test import APIRequestFactory
        from rest_framework.views import set_rollback

        from tfc.utils.api_errors import build_error_envelope
        from tfc.views.host_update_status import HostUpdateStatusView

        canary = "CANARY credential=secret /private/host postgres://user:password@db"
        cases = []
        for http_status, code, _, _ in ERROR_CASES:
            exc = APIException(canary, code=code)
            exc.status_code = http_status
            cases.append((exc, http_status, code))
        for http_status in (400, 401, 403, 404, 405, 503, 500, 418):
            exc = APIException({"request_body": [canary]}, code=canary)
            exc.status_code = http_status
            expected = next(
                (row for row in ERROR_CASES if row[0] == http_status), ERROR_CASES[-1]
            )
            cases.append((exc, expected[0], expected[1]))
        cases.append((RuntimeError(canary), 503, "service_unavailable"))
        for exc, http_status, code in cases:
            view = HostUpdateStatusView()
            view.request = view.initialize_request(
                APIRequestFactory().get("/api/host-update-status/")
            )
            with (
                self.subTest(status=http_status, code=code, exc=type(exc).__name__),
                patch(
                    "tfc.views.host_update_status.build_error_envelope",
                    wraps=build_error_envelope,
                    create=True,
                ) as build,
                patch(
                    "tfc.views.host_update_status.set_rollback", wraps=set_rollback
                ) as rollback,
            ):
                response = view.handle_exception(exc)
                expected = error_body(code)
                self.assertEqual(response.status_code, http_status)
                self.assertEqual(response.data, expected)
                self.assertTrue(response.exception)
                self.assertNotIn("CANARY", json.dumps(response.data))
                build.assert_called_once_with(
                    expected["detail"], status_code=http_status, code=code
                )
                rollback.assert_called_once_with()
                if http_status == 401:
                    self.assertEqual(
                        response["WWW-Authenticate"],
                        view.get_authenticate_header(view.request),
                    )
                else:
                    self.assertNotIn("WWW-Authenticate", response)

    def test_error_serializer_and_documentation_match_exact_wire(self):
        from jsonschema import Draft4Validator

        from tfc.host_updates.serializers import HostUpdateStatusErrorSerializer

        schema = HostUpdateStatusErrorSerializer.Meta.swagger_schema_fields
        keys = set(error_body("invalid"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), keys)
        self.assertEqual(set(schema["properties"]), keys)
        self.assertEqual(
            schema["properties"]["status"], {"type": "boolean", "enum": [False]}
        )
        self.assertEqual(
            set(schema["properties"]["type"]["enum"]), {row[3] for row in ERROR_CASES}
        )
        self.assertEqual(
            set(schema["properties"]["code"]["enum"]), {row[1] for row in ERROR_CASES}
        )
        validator = Draft4Validator(schema)
        for _, code, _, _ in ERROR_CASES:
            with self.subTest(code=code):
                expected = error_body(code)
                serializer = HostUpdateStatusErrorSerializer(data=expected)
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.validated_data, expected)
                self.assertEqual(serializer.data, expected)
                validator.validate(expected)

    def test_error_serializer_rejects_extra_missing_and_non_wire_types(self):
        from tfc.host_updates.serializers import HostUpdateStatusErrorSerializer

        body = error_body("service_unavailable")
        invalid = [
            {**body, "details": {"private": ["CANARY"]}},
            {**body, "status": True},
        ]
        for key in body:
            invalid.append({k: v for k, v in body.items() if k != key})
            for value in (None, [], {"secret": "CANARY"}, 0):
                invalid.append({**body, key: value})
        for key in ("type", "code"):
            invalid.append({**body, key: "CANARY"})
        for key in ("detail", "message", "error", "result"):
            for value in ("", "x" * 129):
                invalid.append({**body, key: value})
        for value in invalid:
            with self.subTest(value=value):
                serializer = HostUpdateStatusErrorSerializer(data=value)
                self.assertFalse(serializer.is_valid())
                self.assertNotIn("CANARY", str(serializer.errors))


# Do not require Django merely to run the stdlib reader suite. Selecting the
# endpoint suite without Django still fails at setUp; it is not silently skipped.
try:
    from django.test import TestCase as DatabaseTestCase
except ModuleNotFoundError as exc:
    if exc.name != "django":
        raise
    DatabaseTestCase = unittest.TestCase


class EndpointTests(DatabaseTestCase):
    def setUp(self):
        from django.core.cache import cache
        from django.test import override_settings
        from django.utils import timezone as django_timezone
        from rest_framework.test import APIClient

        from accounts.authentication import generate_encrypted_message
        from accounts.models import User
        from accounts.models.auth_token import AuthToken, AuthTokenType
        from accounts.models.organization import Organization
        from accounts.models.organization_membership import OrganizationMembership
        from tfc.constants.roles import OrganizationRoles

        cache.clear()
        self.addCleanup(cache.clear)
        self.org = Organization.objects.create(name="S09 isolated test")
        self.user = User.objects.create_user(
            "s09-test@example.invalid", name="S09", organization=self.org
        )
        self.membership, _ = (
            OrganizationMembership.no_workspace_objects.update_or_create(
                user=self.user,
                organization=self.org,
                defaults={
                    "level": 15,
                    "role": OrganizationRoles.OWNER,
                    "is_active": True,
                    "deleted": False,
                },
            )
        )
        self.token = AuthToken.objects.create(
            user=self.user,
            auth_type=AuthTokenType.ACCESS,
            last_used_at=django_timezone.now(),
        )
        self.bearer = generate_encrypted_message(
            {"user_id": str(self.user.pk), "id": str(self.token.pk)}
        )
        self.client = APIClient()
        # APIClient credentials override per-request headers. Keep org selection
        # per-request so wrong/empty org tests exercise the actual supplied value.
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.bearer)
        self.url = "/api/host-update-status/"
        config = override_settings(
            CLOUD_DEPLOYMENT="",
            EE_LICENSE_KEY="",
            ENV_TYPE="local",
            FI_HOST_UPDATE_STATUS_FILE="",
            FI_HOST_UPDATE_STATUS_SLOT=IDENTITY["slot_id"],
        )
        config.enable()
        self.addCleanup(config.disable)
        env = patch.dict(os.environ, {"FI_EMBEDDED_TEMPORAL_WORKER": "false"})
        env.start()
        self.addCleanup(env.stop)
        # No auth/permission/membership mocks. Only the optional edition module
        # probe is isolated to model the supported OSS-without-EE distribution.
        mode = patch("tfc.host_updates.permissions.find_spec", return_value=None)
        mode.start()
        self.addCleanup(mode.stop)
        self.mode_patch = mode

    def assert_error_response(self, response, http_status, code, *, head=False):
        from tfc.host_updates.serializers import HostUpdateStatusErrorSerializer

        self.assertEqual(response.status_code, http_status)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertIn("Authorization", response["Vary"])
        self.assertIn("X-Organization-Id", response["Vary"])
        if http_status == 401:
            self.assertIn("WWW-Authenticate", response)
        if head:
            self.assertEqual(response.content, b"")
            self.assertIsNone(response.data)
            self.assertNotIn("X-2FA-Required", response)
            self.assertNotIn("X-2FA-Grace-Ends", response)
        else:
            self.assertEqual(response.json(), error_body(code))
            serializer = HostUpdateStatusErrorSerializer(data=response.json())
            self.assertTrue(serializer.is_valid(), serializer.errors)
            self.assertEqual(serializer.validated_data, response.json())

    def test_rejected_requests_and_metadata_have_exact_common_errors(self):
        from django.test import override_settings

        with patch.object(
            status, "_read_file", side_effect=AssertionError("must not read")
        ):
            for method in ("get", "head", "options"):
                with self.subTest(method=method):
                    send = getattr(self.client, method)
                    self.assert_error_response(
                        send(self.url + "?path=/private/CANARY"),
                        400,
                        "invalid",
                        head=method == "head",
                    )
                    self.assert_error_response(
                        self.client.generic(
                            method.upper(),
                            self.url,
                            b'{"credential":"CANARY"}',
                            content_type="application/json",
                        ),
                        400,
                        "invalid",
                        head=method == "head",
                    )
                    with override_settings(EE_LICENSE_KEY="CANARY"):
                        self.assert_error_response(
                            send(self.url), 404, "not_found", head=method == "head"
                        )
                    self.assert_error_response(
                        send(self.url, HTTP_X_API_KEY="CANARY"),
                        403,
                        "permission_denied",
                        head=method == "head",
                    )
                    self.client.credentials()
                    self.assert_error_response(
                        send(self.url), 401, "not_authenticated", head=method == "head"
                    )
                    self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.bearer)
            for method in ("post", "put", "patch", "delete"):
                self.assert_error_response(
                    getattr(self.client, method)(self.url), 405, "method_not_allowed"
                )

    def test_auth_database_and_transport_failures_are_sanitized_for_metadata(self):
        from django.db import OperationalError

        for exc in (
            OperationalError("CANARY postgres://user:password@db"),
            RuntimeError("CANARY /private/host"),
        ):
            with patch("tfc.host_updates.permissions.decode_token", side_effect=exc):
                for method in ("get", "head", "options"):
                    with self.subTest(exc=type(exc).__name__, method=method):
                        self.assert_error_response(
                            getattr(self.client, method)(self.url),
                            503,
                            "service_unavailable",
                            head=method == "head",
                        )

    def new_human_session(self):
        """Issue a real fresh fixture token after lifecycle signals revoke one."""
        from django.utils import timezone as django_timezone

        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthToken, AuthTokenType

        self.token = AuthToken.objects.create(
            user=self.user,
            auth_type=AuthTokenType.ACCESS,
            last_used_at=django_timezone.now(),
        )
        self.bearer = generate_encrypted_message(
            {"user_id": str(self.user.pk), "id": str(self.token.pk)}
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.bearer)

    def test_active_human_organization_owner_get_cache_and_static_metadata(self):
        with patch(
            "tfc.host_updates.status._read_file",
            side_effect=AssertionError("not configured"),
        ):
            response = self.client.get(
                self.url, HTTP_X_ORGANIZATION_ID=str(self.org.pk)
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(), status.unavailable_response("not_configured")
            )
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertIn("Authorization", response["Vary"])
            self.assertIn("X-Organization-Id", response["Vary"])
            self.assertEqual(self.client.head(self.url).content, b"")
            self.assertEqual(
                self.client.options(self.url).json(),
                {"api_schema_version": 1, "methods": ["GET", "HEAD", "OPTIONS"]},
            )

    def test_active_human_organization_admin_needs_no_staff_or_workspace_role(self):
        from tfc.constants.roles import OrganizationRoles

        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.membership.level = 8
        self.membership.role = OrganizationRoles.ADMIN
        self.membership.save(update_fields=["level", "role"])
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_missing_invalid_and_revoked_credentials(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.credentials()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)
        self.assertIn("WWW-Authenticate", response)
        self.client.credentials(HTTP_AUTHORIZATION="Bearer CANARY_SECRET")
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("CANARY", response.content.decode())
        self.token.is_active = False
        self.token.save(update_fields=["is_active"])
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.bearer)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_no_staff_workspace_or_legacy_role_bypass(self):
        from tfc.constants.roles import OrganizationRoles

        self.user.is_staff = self.user.is_superuser = True
        self.user.save(update_fields=["is_staff", "is_superuser"])
        cases = [
            (8, OrganizationRoles.MEMBER, 200),
            (15, OrganizationRoles.MEMBER, 200),
            (3, OrganizationRoles.OWNER, 403),
            (1, OrganizationRoles.ADMIN, 403),
            (None, OrganizationRoles.OWNER, 200),
            (None, OrganizationRoles.ADMIN, 200),
            (None, OrganizationRoles.WORKSPACE_ADMIN, 403),
            (9, OrganizationRoles.OWNER, 403),
        ]
        for level, role, expected in cases:
            with self.subTest(level=level, role=role):
                self.membership.level, self.membership.role = level, role
                self.membership.save(update_fields=["level", "role"])
                self.assertEqual(self.client.get(self.url).status_code, expected)

    def test_inactive_organization_membership_denies_active_human_owner(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_soft_deleted_organization_membership_denies_active_human_owner(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.membership.deleted = True
        self.membership.save(update_fields=["deleted"])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_missing_membership_never_uses_legacy_user_owner_or_creates_membership(
        self,
    ):
        from accounts.models.organization_membership import OrganizationMembership

        OrganizationMembership.all_objects.filter(pk=self.membership.pk).delete()
        # The existing post_delete signal invalidates all of this user's tokens.
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.token.refresh_from_db()
        self.assertFalse(self.token.is_active)
        self.new_human_session()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertFalse(
            OrganizationMembership.all_objects.filter(user=self.user).exists()
        )

    def test_inactive_human_owner_is_denied_even_after_token_cache_warmup(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_removed_human_owner_is_denied_even_after_token_cache_warmup(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.user.delete()
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_removed_selected_organization_denies_still_active_human(self):
        from accounts.models import User
        from accounts.models.organization_membership import OrganizationMembership

        # Organization deletion normally cascades the legacy User FK. Detach it
        # first so this tests organization existence, not just user revocation.
        self.user.organization = None
        self.user.save(update_fields=["organization"])
        self.assertEqual(self.client.get(self.url).status_code, 200)
        removed_org_id = str(self.org.pk)
        # Complete membership removal/auditing while its parent still exists.
        # This fixture tests the reader's missing-org gate, not cascade auditing.
        OrganizationMembership.all_objects.filter(pk=self.membership.pk).delete()
        self.org.delete()
        self.assertTrue(User.objects.filter(pk=self.user.pk, is_active=True).exists())
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.new_human_session()
        self.assertEqual(
            self.client.get(
                self.url, HTTP_X_ORGANIZATION_ID=removed_org_id
            ).status_code,
            403,
        )

    def test_api_key_transports_rejected_alone_and_with_human_bearer(self):
        for credentials in (
            {"HTTP_AUTHORIZATION": "Bearer " + self.bearer},
            {},
        ):
            self.client.credentials(**credentials)
            for header in ("HTTP_X_API_KEY", "HTTP_X_SECRET_KEY"):
                response = self.client.get(self.url, **{header: "CANARY_SECRET"})
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("CANARY", response.content.decode())

    def test_explicit_wrong_org_cannot_fall_back(self):
        from accounts.models.organization import Organization

        other = Organization.objects.create(name="S09 inaccessible")
        response = self.client.get(self.url, HTTP_X_ORGANIZATION_ID=str(other.pk))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.get(self.url, HTTP_X_ORGANIZATION_ID="").status_code, 403
        )
        self.user.config = {"currentOrganizationId": str(other.pk)}
        self.user.save(update_fields=["config"])
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + self.bearer)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_selectors_and_mutations_denied_without_read(self):
        with patch(
            "tfc.host_updates.status._read_file",
            side_effect=AssertionError("must not read"),
        ):
            for method in ("post", "put", "patch", "delete"):
                self.assertEqual(
                    getattr(self.client, method)(self.url).status_code, 405
                )
            for query in (
                "path=/private/CANARY",
                "organization_id=not-a-uuid",
                "organization_id=" + str(self.org.pk),
                "format=json",
            ):
                response = self.client.get(self.url + "?" + query)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("CANARY", response.content.decode())
            self.assertEqual(
                self.client.generic(
                    "GET", self.url, b"{}", content_type="application/json"
                ).status_code,
                400,
            )

    def test_cloud_license_helm_and_broken_ee_before_file(self):
        from django.test import override_settings

        with patch(
            "tfc.host_updates.status._read_file",
            side_effect=AssertionError("must not read"),
        ):
            for cloud in (" US ", "eu", "DEV"):
                with override_settings(
                    CLOUD_DEPLOYMENT=cloud, CLOUD_DEPLOYMENT_SECRET="invalid"
                ):
                    self.assertEqual(self.client.get(self.url).status_code, 404)
            with override_settings(EE_LICENSE_KEY="invalid"):
                self.assertEqual(self.client.get(self.url).status_code, 404)
            with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": ""}):
                self.assertEqual(self.client.get(self.url).status_code, 404)
            with patch(
                "tfc.host_updates.permissions.find_spec",
                side_effect=RuntimeError("CANARY"),
            ):
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("CANARY", response.content.decode())
            with (
                patch("tfc.host_updates.permissions.find_spec", return_value=object()),
                patch(
                    "tfc.host_updates.permissions.import_module",
                    side_effect=ImportError("CANARY"),
                ),
            ):
                self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_bearer_2fa_enforcement_through_middleware_and_drf(self):
        from django.conf import settings
        from django.test import override_settings
        from django.utils import timezone as django_timezone

        middleware = [*settings.MIDDLEWARE]
        if "accounts.middleware.require_2fa.Require2FAMiddleware" not in middleware:
            middleware.append("accounts.middleware.require_2fa.Require2FAMiddleware")
        self.org.require_2fa = True
        self.org.require_2fa_enforced_at = django_timezone.now() - timedelta(days=30)
        self.org.save()
        with override_settings(MIDDLEWARE=middleware):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "2fa_required")
            self.assert_error_response(response, 403, "2fa_required")
            self.assert_error_response(
                self.client.options(self.url), 403, "2fa_required"
            )
            self.assert_error_response(
                self.client.head(self.url), 403, "2fa_required", head=True
            )
            self.org.require_2fa_enforced_at = django_timezone.now()
            self.org.save()
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["X-2FA-Required"], "grace-period")
            from accounts.models.totp_device import UserTOTPDevice

            self.org.require_2fa_enforced_at = django_timezone.now() - timedelta(
                days=30
            )
            self.org.save()
            device = UserTOTPDevice.objects.create(
                user=self.user, confirmed=False, secret_encrypted="s09-fixture"
            )
            self.assertEqual(self.client.get(self.url).status_code, 403)
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            self.assertEqual(self.client.get(self.url).status_code, 200)
            device.delete()
            self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_saml_scope_conflicts_and_revocation_use_real_tokens(self):
        from accounts.authentication import generate_encrypted_message
        from accounts.models.auth_token import AuthTokenOrigin
        from accounts.models.organization import Organization
        from saml2_auth.models import SAMLMetadataModel

        idp = SAMLMetadataModel.no_workspace_objects.create(
            organization=self.org,
            identity_type=2,
            relay_state="s09-test",
            is_enabled=True,
            security_generation=1,
        )
        self.token.auth_origin = AuthTokenOrigin.SAML
        self.token.scoped_organization = self.org
        self.token.origin_idp = idp
        self.token.origin_idp_generation = 1
        self.token.save()
        bearer = generate_encrypted_message(
            {"user_id": str(self.user.pk), "id": str(self.token.pk)}
        )
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + bearer)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        other = Organization.objects.create(name="S09 other scope")
        for response in (
            self.client.get(self.url, HTTP_X_ORGANIZATION_ID=str(other.pk)),
            self.client.get(self.url + "?organization_id=" + str(other.pk)),
        ):
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "saml_scope_conflict")
            self.assert_error_response(response, 403, "saml_scope_conflict")
        for method in ("head", "options"):
            self.assert_error_response(
                getattr(self.client, method)(
                    self.url, HTTP_X_ORGANIZATION_ID=str(other.pk)
                ),
                403,
                "saml_scope_conflict",
                head=method == "head",
            )
        idp.is_enabled = False
        idp.save(update_fields=["is_enabled"])
        self.assertEqual(self.client.get(self.url).status_code, 401)
        idp.is_enabled = True
        idp.save(update_fields=["is_enabled"])
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.membership.is_active = True
        self.membership.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.url).status_code, 200)
        idp.security_generation = 2
        idp.save(update_fields=["security_generation"])
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_workspace_admin_member_cannot_read_or_learn_config(self):
        from accounts.models.workspace import Workspace, WorkspaceMembership
        from tfc.constants.roles import OrganizationRoles

        workspace = Workspace.no_workspace_objects.create(
            name="S09 workspace", organization=self.org, created_by=self.user
        )
        WorkspaceMembership.no_workspace_objects.create(
            user=self.user,
            workspace=workspace,
            role=OrganizationRoles.WORKSPACE_ADMIN,
            level=8,
        )
        self.membership.level = 3
        self.membership.save(update_fields=["level"])
        with patch(
            "tfc.views.host_update_status.supported_installation",
            side_effect=AssertionError("must authorize first"),
        ):
            for method in ("get", "head", "options"):
                self.assertEqual(
                    getattr(self.client, method)(
                        self.url, HTTP_X_WORKSPACE_ID=str(workspace.pk)
                    ).status_code,
                    403,
                )

    def test_snapshot_error_and_stale_responses_are_closed(self):
        from django.test import override_settings

        with override_settings(FI_HOST_UPDATE_STATUS_FILE=status.STATUS_FILE):
            for reason in status.API_REASONS:
                with patch(
                    "tfc.host_updates.status._read_file",
                    side_effect=status.StatusUnavailable(reason),
                ):
                    self.assertEqual(
                        self.client.get(self.url).json(),
                        status.unavailable_response(reason),
                    )
            snapshot = copy.deepcopy(FIXTURES["valid_status"][0]["snapshot"])
            snapshot["installation"]["topology"] = "distributed"
            # Retain actual wall clock; very old, internally consistent timestamps.
            snapshot["generated_at"] = "2000-01-01T00:00:00Z"
            snapshot["versions"]["observed_at"] = snapshot["generated_at"]
            snapshot["discovery"]["checked_at"] = snapshot["generated_at"]
            snapshot["discovery"]["last_attempt_at"] = snapshot["generated_at"]
            with patch(
                "tfc.host_updates.status._read_file", return_value=raw(snapshot)
            ):
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["result"]["freshness"], "stale")
            snapshot["private"] = "CANARY_SECRET"
            with patch(
                "tfc.host_updates.status._read_file", return_value=raw(snapshot)
            ):
                response = self.client.get(self.url)
                self.assertEqual(
                    response.json(), status.unavailable_response("malformed")
                )
                self.assertNotIn("CANARY", response.content.decode())

    def test_env_type_does_not_define_host_production_identity(self):
        from django.test import override_settings

        base = Path(os.environ.get("FI_HOST_UPDATE_TEST_TMPDIR", Path.home())).resolve()
        with tempfile.TemporaryDirectory(
            prefix="host-identity-", dir=base
        ) as directory:
            mount = Path(directory) / "status.json"
            snapshot = copy.deepcopy(FIXTURES["valid_status"][0]["snapshot"])
            for env_type in ("local", "production"):
                for topology, production in (
                    ("standalone", False),
                    ("distributed", False),
                    ("distributed", True),
                    ("standalone", True),
                ):
                    snapshot["installation"].update(
                        topology=topology, production=production
                    )
                    mount.write_bytes(raw(snapshot))
                    mount.chmod(0o644)
                    with (
                        self.subTest(
                            env_type=env_type, topology=topology, production=production
                        ),
                        patch.dict(
                            os.environ,
                            {
                                "FI_EMBEDDED_TEMPORAL_WORKER": str(
                                    topology == "standalone"
                                ).lower(),
                            },
                        ),
                        patch.object(status, "STATUS_FILE", str(mount)),
                        override_settings(
                            ENV_TYPE=env_type,
                            FI_HOST_UPDATE_STATUS_FILE=str(mount),
                        ),
                    ):
                        response = self.client.get(self.url)
                        self.assertEqual(response.status_code, 200)
                        if topology == "standalone" and production:
                            self.assertEqual(
                                response.json(),
                                status.unavailable_response("malformed"),
                            )
                        else:
                            result = response.json()["result"]
                            self.assertEqual(result["availability"], "available")
                            self.assertIsNone(result["reason_code"])
                            self.assertEqual(result["snapshot"], snapshot)
                            self.assertIs(
                                result["snapshot"]["installation"]["production"],
                                production,
                            )
                        self.assertEqual(mount.read_bytes(), raw(snapshot))

    def test_slot_and_topology_mismatch_still_deny_without_production_expectation(self):
        from django.test import override_settings

        snapshot = copy.deepcopy(FIXTURES["valid_status"][0]["snapshot"])
        for topology, production in (
            ("standalone", False),
            ("distributed", False),
            ("distributed", True),
        ):
            snapshot["installation"].update(topology=topology, production=production)
            for env_type in ("local", "production"):
                for mismatch in ("slot_id", "topology"):
                    with (
                        self.subTest(
                            topology=topology,
                            production=production,
                            env_type=env_type,
                            mismatch=mismatch,
                        ),
                        override_settings(
                            ENV_TYPE=env_type,
                            FI_HOST_UPDATE_STATUS_FILE=status.STATUS_FILE,
                            FI_HOST_UPDATE_STATUS_SLOT=(
                                "f" * 32
                                if mismatch == "slot_id"
                                else IDENTITY["slot_id"]
                            ),
                        ),
                        patch.dict(
                            os.environ,
                            {
                                "FI_EMBEDDED_TEMPORAL_WORKER": str(
                                    (topology == "standalone")
                                    != (mismatch == "topology")
                                ).lower(),
                            },
                        ),
                        patch.object(status, "_read_file", return_value=raw(snapshot)),
                    ):
                        response = self.client.get(self.url)
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(
                            response.json(),
                            status.unavailable_response("installation_mismatch"),
                        )

    def test_real_status_files_return_closed_results_without_mutation(self):
        from django.test import override_settings

        base = Path(os.environ.get("FI_HOST_UPDATE_TEST_TMPDIR", Path.home())).resolve()
        with tempfile.TemporaryDirectory(prefix="s09-endpoint-", dir=base) as directory:
            mount = Path(directory) / "status.json"
            snapshot = copy.deepcopy(FIXTURES["valid_status"][0]["snapshot"])
            snapshot["installation"]["topology"] = "distributed"
            # An old, internally consistent report is valid but stale.
            for item, key in (
                (snapshot, "generated_at"),
                (snapshot["versions"], "observed_at"),
                (snapshot["discovery"], "checked_at"),
                (snapshot["discovery"], "last_attempt_at"),
            ):
                item[key] = "2000-01-01T00:00:00Z"
            with (
                patch.object(status, "STATUS_FILE", str(mount)),
                override_settings(FI_HOST_UPDATE_STATUS_FILE=str(mount)),
            ):
                self.assertEqual(
                    self.client.get(self.url).json(),
                    status.unavailable_response("not_reported"),
                )
                mount.write_bytes(raw(snapshot))
                mount.chmod(0o644)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["result"]["freshness"], "stale")
                self.assertEqual(response.json()["result"]["snapshot"], snapshot)
                self.assertEqual(mount.read_bytes(), raw(snapshot))
                for payload in (b"CANARY_SECRET /private/file", b" " * 32769):
                    mount.write_bytes(payload)
                    response = self.client.get(self.url)
                    self.assertEqual(
                        response.json(), status.unavailable_response("malformed")
                    )
                    self.assertEqual(mount.read_bytes(), payload)
                    self.assertNotIn("CANARY", response.content.decode())
                    self.assertNotIn(directory, response.content.decode())
                target = Path(directory) / "target"
                target.write_bytes(raw(snapshot))
                mount.unlink()
                mount.symlink_to(target)
                self.assertEqual(
                    self.client.get(self.url).json(),
                    status.unavailable_response("untrusted_file"),
                )
                mount.unlink()
                os.mkfifo(mount)
                self.assertEqual(
                    self.client.get(self.url).json(),
                    status.unavailable_response("untrusted_file"),
                )
                mount.unlink()
                mount.mkdir()
                self.assertEqual(
                    self.client.get(self.url).json(),
                    status.unavailable_response("untrusted_file"),
                )

    def test_unexpected_reader_error_is_sanitized_and_other_routes_still_work(self):
        from django.test import override_settings

        with (
            override_settings(FI_HOST_UPDATE_STATUS_FILE=status.STATUS_FILE),
            patch.object(
                status, "_read_file", side_effect=RuntimeError("CANARY /private/file")
            ),
        ):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(
                response.json(),
                error_body("service_unavailable"),
            )
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(self.client.get("/api/deployment-info/").status_code, 200)
            self.assertEqual(self.client.get("/health/").status_code, 200)

    def test_real_oss_mode_with_ee_package_and_both_compose_topologies(self):
        from django.test import override_settings

        from tfc.host_updates.permissions import supported_installation

        self.mode_patch.stop()
        if importlib.util.find_spec("ee") is not None:
            from ee.usage.deployment import DeploymentMode, _detect_mode

            self.addCleanup(_detect_mode.cache_clear)
            # Reproduce CI ordering with the actual process-lifetime cache, not
            # a mocked OSS result. Changing settings must not change that cache.
            with override_settings(
                CLOUD_DEPLOYMENT="", EE_LICENSE_KEY="test-license-key"
            ):
                _detect_mode.cache_clear()
                self.assertEqual(DeploymentMode.get_mode(), "ee")
            self.assertEqual(DeploymentMode.get_mode(), "ee")
            self.assertIsNone(supported_installation())
            # Like the EE deployment tests, isolate the cache when the fixture
            # changes settings. Cleanup leaves ambient settings free to redetect.
            _detect_mode.cache_clear()
        for embedded, expected in (("false", "distributed"), ("true", "standalone")):
            with patch.dict(os.environ, {"FI_EMBEDDED_TEMPORAL_WORKER": embedded}):
                self.assertEqual(supported_installation(), expected)
                self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_explicit_non_oss_or_failed_mode_probe_is_denied_before_file_read(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        with (
            patch("tfc.host_updates.permissions.find_spec", return_value=object()),
            patch.object(
                status, "_read_file", side_effect=AssertionError("must not read")
            ),
        ):
            for mode in ("ee", "cloud", None):
                deployment = SimpleNamespace(
                    DeploymentMode=SimpleNamespace(get_mode=Mock(return_value=mode))
                )
                with patch(
                    "tfc.host_updates.permissions.import_module",
                    return_value=deployment,
                ):
                    self.assertEqual(self.client.get(self.url).status_code, 404)
            deployment = SimpleNamespace(
                DeploymentMode=SimpleNamespace(
                    get_mode=Mock(side_effect=RuntimeError("CANARY"))
                )
            )
            with patch(
                "tfc.host_updates.permissions.import_module", return_value=deployment
            ):
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("CANARY", response.content.decode())

    def test_reporting_failure_does_not_change_normal_public_routes(self):
        from django.test import override_settings

        self.client.credentials()
        with (
            override_settings(FI_HOST_UPDATE_STATUS_FILE="/private/CANARY"),
            patch(
                "tfc.host_updates.status._read_file",
                side_effect=AssertionError("no reporting access"),
            ),
        ):
            self.assertEqual(self.client.get("/api/deployment-info/").status_code, 200)
            self.assertEqual(self.client.get("/health/").status_code, 200)

    def test_reads_do_not_provision_workspace_or_membership(self):
        from accounts.models.organization_membership import OrganizationMembership
        from accounts.models.workspace import Workspace, WorkspaceMembership

        models = (OrganizationMembership, Workspace, WorkspaceMembership)
        before = [model.no_workspace_objects.count() for model in models]
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(
            [model.no_workspace_objects.count() for model in models], before
        )
