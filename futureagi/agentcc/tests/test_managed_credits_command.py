"""The managed credit management commands: ``agentcc_managed_credits``
writes ledger entries, and ``agentcc_export_ledger`` writes the entries plus a
hash manifest."""

import csv
import hashlib
import json
import sys
from datetime import UTC, datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from agentcc.services.gateway_client import GatewayClientError

CREDITS_MODULE = "ee.usage.services.managed_credits"
GATEWAY_ADMIN = "agentcc.services.gateway_admin.credits.GatewayAdminClient"
GATEWAY_STATE = {
    "org_id": "ignored",
    "granted_total_micros": 25_000_000,
    "granted_version": 1,
    "spent_micros": 1_250_000,
    "held_micros": 40_000,
    "available_micros": 23_710_000,
    "balance_usd": 23.71,
    "markup_percent": 5,
    "outbox_pending": 2,
    "ledger": "redis",
}


def _credits(*args):
    out = StringIO()
    call_command("agentcc_managed_credits", *args, stdout=out)
    return out.getvalue()


def _export(*args):
    out = StringIO()
    call_command("agentcc_export_ledger", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def gateway():
    def answer(method, path, json=None, params=None):
        if method == "GET":
            return GATEWAY_STATE
        return {
            "applied": True,
            "granted_total_micros": json["granted_total_micros"],
            "granted_version": json["version"],
            "spent_micros": json["settled_spend_floor_micros"],
            "held_micros": 0,
            "available_micros": json["granted_total_micros"],
        }

    with patch(GATEWAY_ADMIN) as client_class:
        client_class.return_value.request.side_effect = answer
        yield client_class.return_value


@pytest.fixture
def usage_schema_current(transactional_db):
    """Bring ``usage`` back to its latest migration before a committing test.

    ``test_deployment_telemetry_migration.py`` leaves ``usage`` at 0026 for the
    rest of the session, without the ledger tables.
    """
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes("usage")
    if executor.migration_plan(leaves):
        executor.migrate(leaves)


@pytest.fixture
def ledger():
    """The ledger models, imported per test so this module collects in OSS."""
    from ee.usage.models import managed_credits

    return managed_credits


def _only_entry(ledger, org):
    return ledger.ManagedCreditEntry.objects.get(organization=org)


@pytest.mark.integration
@pytest.mark.parametrize(
    "command", ["agentcc_managed_credits", "agentcc_export_ledger"]
)
def test_without_the_ledger_service_billing_is_unavailable(
    organization, tmp_path, command
):
    args = [str(organization.pk)]
    if command == "agentcc_export_ledger":
        args += ["--out", str(tmp_path / "out")]

    with patch.dict(sys.modules, {CREDITS_MODULE: None}):
        with pytest.raises(CommandError, match="billing_unavailable"):
            call_command(command, *args, stdout=StringIO())

    assert not (tmp_path / "out").exists()


@pytest.mark.integration
@pytest.mark.requires_ee
class TestManagedCreditsCommand:
    # Committed for real, as from a shell: the push runs when the grant commits,
    # before the command reports.
    @pytest.mark.django_db(transaction=True)
    def test_grant_writes_an_entry_and_pushes_on_commit(
        self, usage_schema_current, organization, ledger, gateway
    ):
        output = _credits(str(organization.pk), "--grant", "25", "--note", "pilot")

        entry = _only_entry(ledger, organization)
        assert (entry.kind, entry.amount_micros, entry.note) == (
            "grant",
            25_000_000,
            "pilot",
        )
        assert entry.idempotency_key.startswith(f"grant:{organization.pk}:cli-")
        puts = [c for c in gateway.request.call_args_list if c.args[0] == "PUT"]
        assert len(puts) == 1
        assert puts[0].kwargs["json"]["granted_total_micros"] == 25_000_000
        assert "granted $25.000000" in output
        assert "gateway acked version 1" in output

    def test_two_cli_grants_are_two_entries(self, organization, ledger):
        _credits(str(organization.pk), "--grant", "1", "--note", "a")
        _credits(str(organization.pk), "--grant", "1", "--note", "a")

        account = ledger.ManagedCreditAccount.objects.get(organization=organization)
        assert account.granted_total_micros == 2_000_000

    def test_adjust_writes_a_signed_adjustment(self, organization, ledger):
        output = _credits(
            str(organization.pk), "--adjust", "-1.5", "--reason", "duplicate charge"
        )

        entry = _only_entry(ledger, organization)
        assert (entry.kind, entry.amount_micros, entry.note) == (
            "adjustment",
            -1_500_000,
            "duplicate charge",
        )
        assert "adjusted -$1.500000" in output
        # Inside the test transaction nothing commits, so nothing was pushed.
        assert "gateway push pending" in output
        # The output says what repairs the gateway's copy and promises no retry.
        assert "reconciler" not in output
        assert "not retried" in output
        assert "the next grant or adjustment" in output

    def test_usd_rounds_half_up_to_the_micro(self, organization, ledger):
        _credits(str(organization.pk), "--grant", "0.0000005", "--note", "x")

        assert _only_entry(ledger, organization).amount_micros == 1

    @pytest.mark.parametrize(
        "args",
        [
            ["--grant", "25"],
            ["--grant", "25", "--note", "  "],
            ["--adjust", "-1"],
            ["--grant", "abc", "--note", "x"],
            ["--grant", "nan", "--note", "x"],
            ["--grant", "inf", "--note", "x"],
            ["--grant", "-5", "--note", "x"],
            ["--grant", "0.0000004", "--note", "x"],
            ["--adjust", "0", "--reason", "x"],
            ["--grant", "1", "--adjust", "1", "--note", "x"],
        ],
        ids=[
            "grant-no-note",
            "blank-note",
            "adjust-no-reason",
            "not-a-number",
            "nan",
            "inf",
            "negative-grant",
            "rounds-to-zero",
            "zero-adjust",
            "two-actions",
        ],
    )
    def test_invalid_arguments_write_nothing(self, organization, ledger, args):
        with pytest.raises(CommandError):
            _credits(str(organization.pk), *args)

        assert not ledger.ManagedCreditEntry.objects.exists()

    # "--set" must not be read as an abbreviation of Django's own "--settings".
    @pytest.mark.parametrize("flag", ["--add", "--set"])
    def test_add_and_set_flags_are_refused(self, organization, ledger, gateway, flag):
        with pytest.raises(CommandError, match="unrecognized arguments"):
            _credits(str(organization.pk), flag, "5")

        gateway.request.assert_not_called()
        assert not ledger.ManagedCreditEntry.objects.exists()

    def test_show_prints_ledger_and_gateway_state(self, organization, ledger, gateway):
        _credits(str(organization.pk), "--grant", "25", "--note", "pilot")

        output = _credits(str(organization.pk), "--show")

        assert "ledger: granted $25.000000" in output
        assert "settled spend $0.000000" in output
        assert "version 1 (gateway acked 0)" in output
        assert "gateway: granted $25.000000 (version 1)" in output
        assert "spent $1.250000" in output and "held $0.040000" in output
        assert "outbox pending 2" in output
        assert "grant" in output and "pilot" in output
        gateway.request.assert_called_with("GET", f"/-/orgs/{organization.pk}/credits")

    def test_show_is_the_default_and_survives_a_gateway_error(
        self, organization, ledger, gateway
    ):
        gateway.request.side_effect = GatewayClientError("Gateway returned 404", 404)

        output = _credits(str(organization.pk))

        assert "ledger: granted $0.000000" in output
        assert "gateway: unavailable (Gateway returned 404)" in output
        # A read never creates the account.
        assert not ledger.ManagedCreditAccount.objects.filter(
            organization=organization
        ).exists()

    @pytest.mark.parametrize("org_id", ["00000000-0000-0000-0000-000000000000", "acme"])
    def test_unknown_org(self, db, ledger, org_id):
        with pytest.raises(CommandError, match="No organization"):
            _credits(org_id, "--show")


@pytest.mark.integration
@pytest.mark.requires_ee
class TestExportLedger:
    @pytest.fixture
    def entries(self, organization, ledger):
        def entry(kind, amount, key, occurred_at, **fields):
            return ledger.ManagedCreditEntry.objects.create(
                organization=organization,
                kind=kind,
                amount_micros=amount,
                idempotency_key=key,
                occurred_at=occurred_at,
                **fields,
            )

        day = datetime(2026, 10, 5, 12, tzinfo=UTC)
        attempts = [{"attempt_id": "req_1.1", "billed_micros": 11_815}]
        return [
            entry("grant", 10_000_000, "k-grant", day, note="pilot"),
            entry(
                "spend",
                -11_815,
                "spend:req_1",
                day.replace(hour=13),
                request_id="req_1",
                attempts=attempts,
            ),
            entry("spend", -185, "spend:req_2", day.replace(hour=14)),
            entry(
                "adjustment", -1_000, "k-adjust", day.replace(day=6), note='=SUM("x")'
            ),
            # Outside --from 2026-10-01 --to 2026-11-01.
            entry("grant", 5, "k-early", datetime(2026, 9, 30, tzinfo=UTC)),
            entry("grant", 7, "k-late", datetime(2026, 11, 1, tzinfo=UTC)),
        ]

    def test_export_manifest_hash_matches(self, organization, entries, tmp_path):
        out = tmp_path / "ledger"

        _export(
            str(organization.pk),
            "--from", "2026-10-01",
            "--to", "2026-11-01",
            "--format", "jsonl",
            "--out", str(out),
        )  # fmt: skip

        exported = (out / "entries.jsonl").read_bytes()
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["files"] == {
            "entries.jsonl": hashlib.sha256(exported).hexdigest()
        }
        assert manifest["entry_count"] == 4
        assert manifest["sum_by_kind"] == {
            "topup": 0,
            "grant": 10_000_000,
            "spend": -12_000,
            "refund": 0,
            "adjustment": -1_000,
            "expiry": 0,
        }
        assert (manifest["org_id"], manifest["from"], manifest["to"]) == (
            str(organization.pk),
            "2026-10-01T00:00:00+00:00",
            "2026-11-01T00:00:00+00:00",
        )
        rows = [json.loads(line) for line in exported.decode().splitlines()]
        assert [row["idempotency_key"] for row in rows] == [
            "k-grant",
            "spend:req_1",
            "spend:req_2",
            "k-adjust",
        ]
        assert rows[0]["id"] == str(entries[0].pk)
        assert rows[0]["occurred_at"] == "2026-10-05T12:00:00+00:00"
        assert rows[1]["amount_micros"] == -11_815
        assert rows[1]["attempts"] == [
            {"attempt_id": "req_1.1", "billed_micros": 11_815}
        ]

        # Any change to the exported bytes does not match the manifest.
        tampered = exported.replace(b"-11815", b"-11816")
        assert (
            hashlib.sha256(tampered).hexdigest() != manifest["files"]["entries.jsonl"]
        )

    def test_csv_export_keeps_numbers_and_defangs_formulas(
        self, organization, entries, tmp_path
    ):
        out = tmp_path / "csv"

        _export(str(organization.pk), "--format", "csv", "--out", str(out))

        data = (out / "entries.csv").read_bytes()
        manifest = json.loads((out / "manifest.json").read_text())
        assert manifest["files"] == {"entries.csv": hashlib.sha256(data).hexdigest()}
        assert manifest["entry_count"] == 6
        assert (manifest["from"], manifest["to"]) == (None, None)
        rows = list(csv.DictReader(StringIO(data.decode())))
        by_key = {row["idempotency_key"]: row for row in rows}
        assert by_key["spend:req_1"]["amount_micros"] == "-11815"
        assert by_key["k-adjust"]["note"] == '\'=SUM("x")'
        assert (
            json.loads(by_key["spend:req_1"]["attempts"])[0]["attempt_id"] == "req_1.1"
        )

    def test_export_refuses_to_overwrite(self, organization, entries, tmp_path):
        out = tmp_path / "again"
        _export(str(organization.pk), "--out", str(out))
        before = (out / "manifest.json").read_bytes()

        with pytest.raises(CommandError, match="already exists"):
            _export(str(organization.pk), "--out", str(out))

        assert (out / "manifest.json").read_bytes() == before

    @pytest.mark.parametrize(
        "args",
        [
            ["--from", "yesterday"],
            ["--to", "2026-13-01"],
            ["--from", "2026-11-01", "--to", "2026-10-01"],
            ["--format", "xml"],
        ],
        ids=["bad-date", "impossible-date", "empty-range", "bad-format"],
    )
    def test_invalid_export_arguments(self, organization, ledger, tmp_path, args):
        with pytest.raises(CommandError):
            _export(str(organization.pk), "--out", str(tmp_path / "x"), *args)

        assert not (tmp_path / "x").exists()
