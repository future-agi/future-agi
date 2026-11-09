"""Strict SemVer (no leading zeros) at intake, publish, references and the database (REVIEW: `1.0.03`
shared a sort key with `1.0.3`), and migration 0002 on a table that already holds such a row."""

import importlib
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.apps import apps
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from omega_hub import semver
from omega_hub.errors import HubError
from omega_hub.models import Entry, Submission, Version
from omega_hub.services import refs
from omega_hub.system_checks import legacy_versions
from omega_hub.tests.flows import publish
from omega_hub.tests.helpers import (
    approve_payload,
    entry_files,
    run_checks,
    submit,
    token_client,
)

pytestmark = pytest.mark.django_db

STRICT = ["0.0.0", "1.0.0", "1.10.0", "10.20.30", "999999.0.1"]
LOOSE = ["1.0.03", "01.0.9", "1.00.0", "1.0.0000001", "1000000.0.0", "1.0", "1.0.0-rc.1", "1.0.0\n", " 1.0.0", "v1.0.0"]
CHECKER_RULES = Path(refs.__file__).resolve().parents[2].parent / "services" / "omega-hub-checker" / "src" / "registry-rules.mjs"
MIGRATION = importlib.import_module("omega_hub.migrations.0002_version_strict_semver")


@pytest.mark.parametrize("value", STRICT)
def test_strict_versions_are_accepted(value):
    assert refs.is_semver(value)
    assert re.fullmatch(semver.SQL_REGEX[1:-1], value)


@pytest.mark.parametrize("value", LOOSE)
def test_leading_zeros_and_other_shapes_are_refused(value):
    assert not refs.is_semver(value)
    with pytest.raises(HubError):
        refs.semver_key(value)


def test_sort_keys_are_one_to_one_and_ordered():
    keys = [refs.semver_key(value) for value in ("1.0.9", "1.0.10", "1.2.0", "10.0.0")]
    assert keys == sorted(keys) and len(set(keys)) == 4


def test_a_reference_with_a_leading_zero_is_refused():
    assert refs.parse_ref("future-agi/first-agent@1.0.0") == ("future-agi", "first-agent", "1.0.0")
    assert refs.parse_ref("future-agi/first-agent") == ("future-agi", "first-agent", None)
    for ref in ("future-agi/first-agent@1.0.03", "future-agi/first-agent@01.0.0"):
        with pytest.raises(HubError):
            refs.parse_ref(ref)


def test_the_checker_u1_pattern_is_djangos():
    source = CHECKER_RULES.read_text(encoding="utf-8")
    assert f'const PART = "{semver.PART}";' in source
    assert MIGRATION.STRICT == semver.SQL_REGEX


def test_intake_refuses_a_hint_version_with_a_leading_zero(world):
    files = entry_files("first-agent", manifest={"version": "1.0.03"})
    response = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.03")
    assert response.status_code == 400 and response.json()["error"]["code"] == "OMEGA_HUB_INVALID"
    assert "leading zeros" in response.json()["error"]["message"]
    assert not Submission.objects.exists()


def test_publish_refuses_a_loose_version_an_old_checker_passed(world):
    """A report from a checker that predates strict U1 can pass `1.0.03`: approval must not publish it."""
    files = entry_files("first-agent", manifest={"version": "1.0.03"})
    created = submit(token_client(world.token_a), "future-agi", files, name="first-agent")
    assert created.status_code == 201, created.content
    _, report, result = run_checks(files)
    assert result["submission"] == "in_review"
    disclosure = json.loads(files["omega.hub.json"])["disclosure"]
    decided = world.b.post(f"/omega-hub/v1/review/submissions/{created.json()['id']}/decision",
                           approve_payload(report, disclosure=disclosure), format="json")
    assert decided.status_code == 400 and decided.json()["error"]["code"] == "OMEGA_HUB_INVALID"
    assert not Version.objects.exists()
    assert Submission.objects.get(pk=created.json()["id"]).status == Submission.Status.IN_REVIEW


def _store_legacy_version(entry: Entry, value: str, *, later_by: int = 1) -> Version:
    """Insert a row the way it was stored before 0002, then put the constraint back exactly as 0002 does."""
    latest = entry.latest_version
    _alter_table(MIGRATION.drop_constraint)
    legacy = Version.objects.create(
        entry=entry, version=value, semver_key=".".join(part.zfill(6) for part in value.split(".")),
        manifest={**latest.manifest, "version": value}, readme=latest.readme, files=latest.files, digest=latest.digest,
        bundle_key=latest.bundle_key, bundle_sha256=latest.bundle_sha256, bundle_bytes=latest.bundle_bytes,
        capabilities=latest.capabilities, check_summary=latest.check_summary, disclosure=latest.disclosure,
        rules_version=latest.rules_version, submission=Submission.objects.create(
            publisher=entry.publisher, submitted_by=latest.approved_by, channel="cli", raw_key=f"raw/legacy-{value}",
            raw_sha256="0" * 64, raw_bytes=1, name=entry.name, version=value, status=Submission.Status.APPROVED),
        approved_by=latest.approved_by, approved_at=timezone.now() + timedelta(seconds=later_by),
    )
    _alter_table(MIGRATION.add_constraint)  # NOT VALID: succeeds although `legacy` breaks the rule
    return legacy


def _alter_table(operation):
    """Run a 0002 operation inside the test's transaction: the deferred FK checks the inserts queued
    must fire first, or PostgreSQL refuses ALTER TABLE ("pending trigger events")."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    with connection.schema_editor() as editor:
        operation(apps, editor)
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")


def test_migration_0002_is_safe_over_a_legacy_row_and_holds_new_rows(world):
    entry = publish(world, "first-agent")
    legacy = _store_legacy_version(entry, "1.0.03")
    with connection.cursor() as cursor:
        cursor.execute("SELECT convalidated FROM pg_constraint WHERE conname = %s", [MIGRATION.NAME])
        assert cursor.fetchone() == (False,)
    # New rows are held to the rule at once.
    with pytest.raises(IntegrityError), transaction.atomic():
        Version.objects.filter(pk=legacy.pk).update(version="01.0.4")
    # The legacy row stays readable, and the database check lists it.
    assert Version.objects.filter(pk=legacy.pk, version="1.0.03").exists()
    warnings = legacy_versions(databases=["default"])
    assert [warning.id for warning in warnings] == ["omega_hub.W002"]
    assert "future-agi/first-agent@1.0.03" in warnings[0].msg


def test_a_legacy_row_still_blocks_its_strict_twin_and_latest_is_stable(world):
    entry = publish(world, "first-agent")
    _store_legacy_version(entry, "1.0.03")
    # `1.0.3` would share 1.0.03's sort key: refused at intake and in the checker's context.
    files = entry_files("first-agent", manifest={"version": "1.0.3"})
    refused = submit(token_client(world.token_a), "future-agi", files, name="first-agent", version="1.0.3")
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "OMEGA_HUB_VERSION_EXISTS"
    assert refs.version_taken({"entry": entry}, "1.0.3") and refs.version_taken({"entry": entry}, "1.0.03")
    assert not refs.version_taken({"entry": entry}, "1.0.4")


def test_two_rows_on_one_sort_key_resolve_latest_to_the_newer_approval(world):
    """Only rows stored before 0002 can tie; `latest` must not flip between them."""
    entry = publish(world, "first-agent")
    _store_legacy_version(entry, "1.0.3", later_by=5)
    _store_legacy_version(entry, "1.0.03", later_by=10)
    assert [refs.newest_live_version(entry).version for _ in range(3)] == ["1.0.03"] * 3
    messages = [warning.msg for warning in legacy_versions(databases=["default"])]
    assert any("first-agent@1.0.03" in msg for msg in messages)
    assert any("first-agent (2 versions share 000001.000000.000003)" in msg for msg in messages)


def test_the_database_check_is_quiet_on_a_clean_registry(world):
    publish(world, "first-agent")
    assert legacy_versions(databases=["default"]) == []
    assert legacy_versions(databases=None) == []
