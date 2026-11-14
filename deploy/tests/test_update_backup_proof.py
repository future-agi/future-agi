"""Synthetic backup attestations only; no backup/restore or daemon claim."""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.tests.test_update_compatibility import NOW, RESERVED, stamp, synthetic_context  # noqa: E402
from deploy.update.backup_proof import BackupExpectation, read_backup_proof, validate_backup_proof  # noqa: E402
from deploy.update.compatibility import MAX_BACKUP_BYTES, CompatibilityError, schema, shape  # noqa: E402
from deploy.update.contracts import ReasonCode  # noqa: E402
from deploy.update.manifest import canonical_json  # noqa: E402

needs_jsonschema = unittest.skipUnless(importlib.util.find_spec("jsonschema"), "optional jsonschema is not installed")


def fixture():
    _, obs, _, raw = synthetic_context()
    value = json.loads(raw)
    expected = BackupExpectation(obs.attempt_id, obs.installation_fingerprint, value["target_manifest_hash"],
                                 value["source_version"], RESERVED, tuple(value["covered_domains"]))
    return value, raw, expected


class BackupProofTests(unittest.TestCase):
    def test_valid_attempt_bound_attestation_is_not_restore_guarantee(self):
        _, raw, expected = fixture()
        proof = validate_backup_proof(raw, expected, now=NOW)
        self.assertEqual(proof.public_summary(), {"backup_state": "reported_complete"})
        self.assertEqual(proof.raw, raw)
        self.assertEqual(proof.attempt_id, expected.attempt_id)
        self.assertNotIn("synthetic-backup", repr(proof))

    def test_each_binding_is_exact_and_no_previous_attempt_reuse(self):
        value, _, expected = fixture()
        for key, replacement in (("attempt_id", "b" * 32), ("installation_fingerprint", "f" * 64),
                                 ("target_manifest_hash", "f" * 64), ("source_version", "v1.50.0")):
            bad = dict(value)
            bad[key] = replacement
            with self.subTest(key=key), self.assertRaises(CompatibilityError) as caught:
                validate_backup_proof(canonical_json(bad), expected, now=NOW)
            self.assertEqual(caught.exception.reason_code, ReasonCode.BACKUP_PROOF_INVALID)

    def test_every_required_field_and_unknown_field_rejected(self):
        value, _, expected = fixture()
        for key in value:
            bad = dict(value)
            del bad[key]
            with self.subTest(key=key), self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(bad), expected, now=NOW)
        for key in ("command", "path", "credentials", "error", "stdout"):
            bad = dict(value, **{key: "/private/backup password=secret"})
            with self.assertRaises(CompatibilityError) as caught:
                validate_backup_proof(canonical_json(bad), expected, now=NOW)
            self.assertEqual(str(caught.exception), "backup_proof_invalid")

    def test_coverage_must_include_every_persistent_domain(self):
        value, _, expected = fixture()
        for domain in value["covered_domains"]:
            bad = copy.deepcopy(value)
            bad["covered_domains"].remove(domain)
            with self.subTest(domain=domain), self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(bad), expected, now=NOW)
        value["covered_domains"].append("redis")
        validate_backup_proof(canonical_json(value), expected, now=NOW)

    def test_unknown_duplicate_protocol_or_empty_domains_rejected(self):
        value, _, expected = fixture()
        for domains in ([], ["component_protocols"], ["unknown"], value["covered_domains"] * 2):
            bad = dict(value, covered_domains=domains)
            with self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(bad), expected, now=NOW)

    def test_verification_must_be_passed_not_exit_zero(self):
        value, _, expected = fixture()
        for result in ("failed", "skipped", "unknown", True, 0, {"exit": 0}):
            with self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(dict(value, verification=result)), expected, now=NOW)

    def test_freshness_inclusive_boundary_and_expiration(self):
        value, raw, expected = fixture()
        # Completion is NOW - 60 seconds. Exactly 3600 seconds is accepted.
        validate_backup_proof(raw, expected, now=NOW + timedelta(seconds=3540))
        with self.assertRaises(CompatibilityError) as caught:
            validate_backup_proof(raw, expected, now=NOW + timedelta(seconds=3540, microseconds=1))
        self.assertEqual(caught.exception.reason_code, ReasonCode.BACKUP_EXPIRED)
        value["completed_at"] = stamp(NOW)
        validate_backup_proof(canonical_json(value), expected, now=NOW)

    def test_clock_regression_future_times_and_naive_clock(self):
        value, raw, expected = fixture()
        for now in (RESERVED - timedelta(seconds=1), NOW.replace(tzinfo=None)):
            with self.assertRaises(CompatibilityError) as caught:
                validate_backup_proof(raw, expected, now=now)
            self.assertEqual(caught.exception.reason_code, ReasonCode.CLOCK_SKEW)
        for key in ("started_at", "completed_at"):
            bad = dict(value, **{key: stamp(NOW + timedelta(seconds=1))})
            with self.assertRaises(CompatibilityError) as caught:
                validate_backup_proof(canonical_json(bad), expected, now=NOW)
            self.assertEqual(caught.exception.reason_code, ReasonCode.CLOCK_SKEW)

    def test_started_before_attempt_and_reversed_times_rejected(self):
        value, _, expected = fixture()
        for start, end in ((RESERVED - timedelta(seconds=1), NOW), (NOW, RESERVED)):
            value.update(started_at=stamp(start), completed_at=stamp(end))
            with self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(value), expected, now=NOW)

    def test_timestamp_calendar_format_leapsecond_and_timezone_rejected(self):
        value, _, expected = fixture()
        for text in ("2026-02-30T12:00:00Z", "2026-10-24T12:00:60Z", "0000-01-01T00:00:00Z",
                     "2026-10-24T12:00:00+00:00", "2026-10-24T12:00:00.1Z", "2026-10-24T12:00:00Z\n"):
            with self.subTest(text=text), self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(dict(value, completed_at=text)), expected, now=NOW)

    def test_no_path_command_or_credentials_in_identifier_fields(self):
        value, _, expected = fixture()
        for key in ("backup_id", "restore_procedure_id"):
            for text in ("/private/path", "../backup", "s3://bucket/key", "token=secret", "run --restore", "", "x" * 129):
                with self.subTest(key=key, text=text), self.assertRaises(CompatibilityError):
                    validate_backup_proof(canonical_json(dict(value, **{key: text})), expected, now=NOW)

    def test_wrong_scalar_types_and_bool_integers(self):
        value, _, expected = fixture()
        for key, replacement in (("schema_version", True), ("schema_version", 1.0), ("schema_version", 2),
                                 ("attempt_id", 1), ("covered_domains", "postgres_app"), ("source_version", " v1.51.0")):
            with self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(dict(value, **{key: replacement})), expected, now=NOW)
        for maximum in (True, 1.0, 0, 59, 86401):
            with self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(value), expected, now=NOW, max_age_seconds=maximum)

    def test_duplicate_keys_nonfinite_invalid_utf8_and_size_are_rejected(self):
        _, raw, expected = fixture()
        damaged = (raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'), b'\xff',
                   b'{"x":NaN}', raw + b' ' * MAX_BACKUP_BYTES, raw + b'{}', b'[]')
        for text in damaged:
            with self.assertRaises(CompatibilityError):
                validate_backup_proof(text, expected, now=NOW)
        padded = raw + b' ' * (MAX_BACKUP_BYTES - len(raw))
        validate_backup_proof(padded, expected, now=NOW)

    def test_bad_expectation_cannot_erase_coverage_or_reservation(self):
        _, _, expected = fixture()
        for change in ({"covered_domains": ()}, {"attempt_id": ""}, {"source_version": "unknown"},
                       {"reserved_at": NOW.replace(tzinfo=None)}, {"covered_domains": ("component_protocols",)}):
            with self.assertRaises(CompatibilityError):
                replace(expected, **change)

    @needs_jsonschema
    def test_schema_runtime_parity_including_calendar_validation(self):
        from jsonschema import Draft202012Validator, FormatChecker
        spec = schema("backup-proof.schema.json")
        Draft202012Validator.check_schema(spec)
        checker = FormatChecker()
        # jsonschema's optional RFC3339 extras need not be installed.
        from deploy.update.compatibility import timestamp
        checker.checks("date-time", raises=ValueError)(lambda value: bool(timestamp(value)))
        validator = Draft202012Validator(spec, format_checker=checker)
        value, _, _ = fixture()
        validator.validate(value)
        shape(value, spec)
        for key, replacement in (("schema_version", True), ("attempt_id", "bad"), ("verification", "failed"),
                                 ("completed_at", "2026-02-30T12:00:00Z"), ("covered_domains", []), ("path", "/private")):
            bad = dict(value, **{key: replacement})
            self.assertFalse(validator.is_valid(bad), key)
            with self.assertRaises(CompatibilityError):
                shape(bad, spec)


class BackupProofV2Tests(unittest.TestCase):
    def fixture(self):
        from deploy.update.contracts import BackupSeal, RESTORE_PROCEDURE_ID, PERSISTENT_DOMAINS
        import hashlib
        value, _, expected = fixture()
        expected = replace(expected, covered_domains=PERSISTENT_DOMAINS)
        value.update(schema_version=2, covered_domains=list(PERSISTENT_DOMAINS), inventory_version=2,
            binding_sha256='a' * 64, inventory_sha256='b' * 64, source_observation_sha256='c' * 64,
            quiescence_evidence_sha256='d' * 64, snapshot_at=value['started_at'],
            restore_procedure_id=RESTORE_PROCEDURE_ID)
        raw = canonical_json(value)
        from deploy.update.compatibility import timestamp
        seal = BackupSeal(expected.attempt_id, 'operation-token', 'local-run', 'a' * 64,
            expected.target_manifest_hash, expected.source_version, hashlib.sha256(raw).hexdigest(),
            'b' * 64, 'c' * 64, 'd' * 64, timestamp(value['snapshot_at']), NOW)
        return value, raw, expected, seal

    def test_v2_binds_inventory_snapshot_and_original_seal_without_hash_cycle(self):
        value, raw, expected, seal = self.fixture()
        proof = validate_backup_proof(raw, expected, now=NOW, seal=seal)
        self.assertEqual(proof.inventory_sha256, seal.inventory_sha256)
        self.assertEqual(proof.snapshot_at, seal.snapshot_at)
        self.assertEqual(proof.schema_version, 2)
        self.assertNotIn('operation-token', repr(proof))
        for name in ('binding_sha256', 'inventory_sha256', 'source_observation_sha256', 'quiescence_evidence_sha256'):
            with self.subTest(name=name), self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(dict(value, **{name: 'f' * 64})), expected, now=NOW, seal=seal)

    def test_forward_snapshot_freshness_is_not_completion_freshness(self):
        value, _, expected, _ = self.fixture()
        value['completed_at'] = stamp(NOW)
        raw = canonical_json(value)
        with self.assertRaises(CompatibilityError) as caught:
            validate_backup_proof(raw, expected, now=NOW + timedelta(seconds=3600))
        self.assertEqual(caught.exception.reason_code, ReasonCode.BACKUP_EXPIRED)

    def test_old_genuine_snapshot_recovers_only_with_original_seal_and_times_unchanged(self):
        value, raw, expected, seal = self.fixture()
        old_now = NOW + timedelta(days=365)
        with self.assertRaises(CompatibilityError):
            validate_backup_proof(raw, expected, now=old_now)
        with self.assertRaises(CompatibilityError):
            validate_backup_proof(raw, expected, now=old_now, for_recovery=True)
        proof = validate_backup_proof(raw, expected, now=old_now, seal=seal, for_recovery=True)
        self.assertEqual(proof.raw, raw)
        self.assertEqual(proof.snapshot_at, seal.snapshot_at)
        with self.assertRaises(CompatibilityError):
            validate_backup_proof(raw, expected, now=old_now, seal=replace(seal, proof_sha256='0' * 64), for_recovery=True)

    def test_v2_requires_exact_domains_and_snapshot_order(self):
        value, _, expected, _ = self.fixture()
        for changes in ({'covered_domains': list(expected.covered_domains) + ['redis']},
                        {'snapshot_at': stamp(RESERVED - timedelta(seconds=1))},
                        {'snapshot_at': stamp(NOW)}, {'inventory_version': True}):
            with self.subTest(changes=changes), self.assertRaises(CompatibilityError):
                validate_backup_proof(canonical_json(dict(value, **changes)), expected, now=NOW)


class BackupProofFileTests(unittest.TestCase):
    def setUp(self):
        # Real local filesystem I/O, not mocked APFS/ext4 qualification.
        self.temporary = tempfile.TemporaryDirectory(prefix="s02-proof-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o700)
        self.fd = os.open(self.directory, os.O_RDONLY)
        self.addCleanup(os.close, self.fd)
        _, self.raw, self.expected = fixture()
        self.path = self.directory / "proof.json"
        self.path.write_bytes(self.raw)
        self.path.chmod(0o600)

    def read(self, **kwargs):
        options = dict(now=NOW, expected_uid=os.getuid())
        options.update(kwargs)
        return read_backup_proof(self.fd, self.path.name, self.expected, **options)

    def test_owned_regular_single_link_private_file(self):
        self.assertEqual(self.read().raw, self.raw)

    def test_public_mode_and_wrong_owner_refused(self):
        self.path.chmod(0o644)
        with self.assertRaises(CompatibilityError):
            self.read()
        self.path.chmod(0o600)
        with self.assertRaises(CompatibilityError):
            self.read(expected_uid=os.getuid() + 1)
        self.directory.chmod(0o755)
        with self.assertRaises(CompatibilityError):
            self.read()

    def test_symlink_hardlink_directory_and_fifo_refused(self):
        original = self.directory / "original.json"
        self.path.rename(original)
        self.path.symlink_to(original)
        with self.assertRaises(CompatibilityError):
            self.read()
        self.path.unlink()
        os.link(original, self.path)
        with self.assertRaises(CompatibilityError):
            self.read()
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaises(CompatibilityError):
            self.read()
        self.path.rmdir()
        os.mkfifo(self.path, 0o600)
        with self.assertRaises(CompatibilityError):
            self.read()

    def test_size_missing_and_path_traversal_refused_without_path_leak(self):
        self.path.write_bytes(b' ' * (MAX_BACKUP_BYTES + 1))
        with self.assertRaises(CompatibilityError):
            self.read()
        self.path.unlink()
        with self.assertRaises(CompatibilityError) as caught:
            self.read()
        self.assertEqual(str(caught.exception), "backup_proof_invalid")
        for name in ("../proof.json", "/private/proof.json", ".", "..", "nested/proof.json"):
            with self.assertRaises(CompatibilityError):
                read_backup_proof(self.fd, name, self.expected, now=NOW, expected_uid=os.getuid())


if __name__ == "__main__":
    unittest.main()
