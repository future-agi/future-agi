"""Cloud-only operator-path rehearsal, synthetic databases and local services.

Run: G18_SYNTHETIC_REHEARSAL=1 python -m agentcc.tests.hosted_operator_rehearsal
Timings are command/fixture-database elapsed times, NOT production lock holds.
"""

import json

from cryptography.fernet import Fernet
from psycopg import sql

from agentcc.hosted_migration_progress import DEFAULT_SCHEMA_DEADLINE_SECONDS
from agentcc.tests.gateway_secret_fixtures import encrypted_sections, secret_sections
from agentcc.tests.hosted_rehearsal_process import DEFAULT_OPERATOR_TIMEOUT_SECONDS
from agentcc.tests.hosted_rehearsal_support import Rehearsal

VOLUME_MATRIX = ((2, 3), (4, 25), (4, 101))


def seed_configs(rehearsal, apps, org_count, versions, *, encrypted=False):
    org_model = apps.get_model("accounts", "Organization")
    config_model = apps.get_model("prism", "AgentccOrgConfig")
    orgs = []
    for org_no in range(org_count):
        org = org_model.objects.using("default_direct").create(
            name=f"Synthetic {org_no}"
        )
        orgs.append(org)
        sections = (
            encrypted_sections(rehearsal.source) if encrypted else secret_sections()
        )
        config_model.objects.using("default_direct").bulk_create(
            [
                config_model(
                    organization_id=org.pk,
                    version=version,
                    is_active=version == versions,
                    deleted=version == 1,
                    routing={"strategy": "round-robin"},
                    **sections,
                )
                for version in range(1, versions + 1)
            ]
        )
    return orgs


def config_snapshot(apps):
    model = apps.get_model("prism", "AgentccOrgConfig")
    return list(model._base_manager.using("default_direct").order_by("pk").values())


def assert_config_semantics(apps, source):
    from agentcc.services.org_config_secrets import iter_secret_slots

    expected = secret_sections()
    expected_values = {slot.pointer: slot.value for slot in iter_secret_slots(expected)}
    for row in (
        apps.get_model("prism", "AgentccOrgConfig")
        ._base_manager.using("default_direct")
        .all()
    ):
        slots = list(iter_secret_slots({name: getattr(row, name) for name in expected}))
        assert len(slots) == len(expected_values)
        for slot in slots:
            assert (
                json.loads(Fernet(source.encode()).decrypt(slot.value[5:].encode()))[
                    "v"
                ]
                == expected_values[slot.pointer]
            )
        assert row.routing == {"strategy": "round-robin"}
        assert (
            row.alerting["channels"][1]["url"] == "https://sink.invalid/hooks/on-call"
        )


def migration_rehearsal(
    orgs,
    versions,
    *,
    schema_deadline_seconds=DEFAULT_SCHEMA_DEADLINE_SECONDS,
    operator_timeout_seconds=DEFAULT_OPERATOR_TIMEOUT_SECONDS,
):
    with Rehearsal(
        schema_deadline_seconds, operator_timeout_seconds=operator_timeout_seconds
    ) as rehearsal:
        rehearsal.run("schema", expected=1, changes={"SERVICE_TYPE": "backend"})
        rehearsal.run("schema", expected=1, changes={"PG_DIRECT_HOST": ""})
        # Empty install plan is mutation-free, including django_migrations.
        rehearsal.run("schema")
        with rehearsal.sql_connection() as db:
            assert (
                db.execute("SELECT to_regclass('public.django_migrations')").fetchone()[
                    0
                ]
                is None
            )
        apps = rehearsal.fixture_state("0029_org_config_managed_models")
        table = apps.get_model("prism", "AgentccRequestLog")._meta.db_table
        with rehearsal.sql_connection() as holder:
            holder.execute(
                sql.SQL("LOCK TABLE {} IN ROW EXCLUSIVE MODE").format(
                    sql.Identifier(table)
                )
            )
            output = rehearsal.apply("schema", expected=1)
            assert "Hosted phase failed" in output
        rehearsal.apply("schema")
        apps = rehearsal.fixture_state("0046_org_config_db_defaults")
        seed_configs(rehearsal, apps, orgs, versions)
        with rehearsal.sql_connection() as db:
            defaults = db.execute(
                "SELECT column_default FROM information_schema.columns WHERE table_name='agentcc_org_config' AND column_name IN ('identity','tools','managed_models')"
            ).fetchall()
            assert len(defaults) == 3 and all(row[0] for row in defaults)
        before = config_snapshot(apps)
        rehearsal.run("backfill")
        assert config_snapshot(apps) == before
        rehearsal.run(
            "backfill",
            "--apply",
            "--quiesced",
            "--compatible-runtime-revision",
            "wrong",
            expected=1,
        )
        with rehearsal.sql_connection() as holder:
            holder.execute(
                "SELECT pg_advisory_lock(hashtext('futureagi-hosted-phases'))"
            )
            assert "holds the lock" in rehearsal.apply("backfill", expected=1)
        rehearsal.apply("backfill", expected=1, changes={"G18_INTERRUPT_0047": "1"})
        interrupted = config_snapshot(apps)
        assert (
            sum(
                row["cache"]["redis"]["password"].startswith("enc::")
                for row in interrupted
            )
            == 2
        )
        rehearsal.apply("backfill")
        rehearsal.run("check")
        apps = rehearsal.fixture_state()
        assert_config_semantics(apps, rehearsal.source)
        stable = config_snapshot(apps)
        rehearsal.apply("backfill")
        assert config_snapshot(apps) == stable
        with rehearsal.sql_connection() as db:
            applied = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM django_migrations WHERE app='prism'"
                )
            }
            assert {
                "0048_r7_batch_jobs",
                "0048_r7_email_alert_state",
                "0049_merge_r7",
                "0050_encrypt_org_config_secret_aliases",
                "0051_encrypt_alert_channel_urls",
            } <= applied
        return {
            "orgs": orgs,
            "versions": versions,
            "rows": orgs * versions,
            "timings": rehearsal.timings,
        }


def seed_encrypted_stores(rehearsal, apps):
    def binary():
        return Fernet(rehearsal.source.encode()).encrypt(
            b'{"v":"synthetic-transition-value"}'
        )

    def wrapped():
        return "enc::" + binary().decode()

    organizations = list(
        apps.get_model("accounts", "Organization").objects.using("default_direct").all()
    )

    def create(model_name, **values):
        return (
            apps.get_model("prism", model_name)
            .objects.using("default_direct")
            .create(**values)
        )

    for number, org in enumerate(organizations):
        create(
            "AgentccProviderCredential",
            organization_id=org.pk,
            provider_name="synthetic",
            encrypted_credentials=binary(),
            deleted=number == 0,
        )
    org = organizations[0]
    create(
        "AgentccGuardrailPolicy",
        organization_id=org.pk,
        name="encrypted",
        encrypted_check_configs=binary(),
    )
    create(
        "AgentccGuardrailPolicy",
        organization_id=org.pk,
        name="nullable",
        encrypted_check_configs=None,
    )
    create(
        "AgentccEmailAlert",
        organization_id=org.pk,
        name="encrypted",
        encrypted_config=binary(),
    )
    create(
        "AgentccEmailAlert", organization_id=org.pk, name="empty", encrypted_config=b""
    )
    create(
        "AgentccManagedUpstreamKey",
        organization_id=org.pk,
        provider_id="synthetic",
        key_hash="synthetic",
        encrypted_key=wrapped(),
        status="revoked",
    )
    create(
        "AgentccMCPCredential",
        organization_id=org.pk,
        server_id="synthetic",
        kind="oauth",
        encrypted_secret=wrapped(),
        encrypted_refresh=wrapped(),
        status="revoked",
    )
    create(
        "AgentccMCPCredential",
        organization_id=org.pk,
        server_id="empty",
        kind="oauth",
        encrypted_secret=wrapped(),
        encrypted_refresh=None,
    )
    create(
        "AgentccMCPOAuthSigningKey",
        kid="synthetic",
        encrypted_private=wrapped(),
        status="retired",
    )


def inventory_snapshot():
    from agentcc.services.gateway_key_inventory import inventory

    return {
        store.model.__name__: list(
            store.model._base_manager.using("default_direct").order_by("pk").values()
        )
        for store in inventory()
    }


def key_rehearsal(
    *,
    installed_fixture=False,
    schema_deadline_seconds=DEFAULT_SCHEMA_DEADLINE_SECONDS,
    operator_timeout_seconds=DEFAULT_OPERATOR_TIMEOUT_SECONDS,
):
    from agentcc.tests.hosted_operator_contention import assert_operator_exclusion

    with Rehearsal(
        schema_deadline_seconds, operator_timeout_seconds=operator_timeout_seconds
    ) as rehearsal:
        if installed_fixture:
            apps = rehearsal.fixture_state(latest=True)
            seed_configs(rehearsal, apps, 3, 3, encrypted=True)
        else:
            rehearsal.apply("schema")
            apps = rehearsal.fixture_state("0046_org_config_db_defaults")
            seed_configs(rehearsal, apps, 3, 3)
            rehearsal.apply("backfill")
            apps = rehearsal.fixture_state()
        assert_operator_exclusion(rehearsal)
        seed_encrypted_stores(rehearsal, apps)
        before = inventory_snapshot()
        rehearsal.key()
        assert before == inventory_snapshot()
        rehearsal.key(
            "key-transition",
            "--apply",
            expected=1,
            changes={"AGENTCC_GATEWAY_TARGET_ENCRYPTION_KEY": "malformed"},
        )
        assert before == inventory_snapshot()
        from agentcc.models import AgentccGuardrailPolicy

        policy = (
            AgentccGuardrailPolicy._base_manager.using("default_direct")
            .filter(name="encrypted")
            .get()
        )
        original = bytes(policy.encrypted_check_configs)
        AgentccGuardrailPolicy._base_manager.using("default_direct").filter(
            pk=policy.pk
        ).update(encrypted_check_configs=b"corrupt")
        corrupted = inventory_snapshot()
        rehearsal.key("key-transition", "--apply", expected=1)
        assert corrupted == inventory_snapshot()  # provider rows not partially written
        AgentccGuardrailPolicy._base_manager.using("default_direct").filter(
            pk=policy.pk
        ).update(encrypted_check_configs=original)
        rehearsal.key(
            "key-transition",
            "--apply",
            "--key-batch-size",
            "2",
            expected=1,
            changes={"G18_INTERRUPT_KEY_UPDATE": "1"},
        )
        assert (
            before == inventory_snapshot()
        )  # first row update rolled back with second
        with rehearsal.sql_connection() as holder:
            holder.execute(
                "SELECT pg_advisory_lock(hashtext('futureagi-hosted-phases'))"
            )
            assert "holds the lock" in rehearsal.key(
                "key-transition", "--apply", expected=1
            )
        rehearsal.key(
            "key-transition",
            "--apply",
            "--key-batch-size",
            "1",
            "--key-max-batches",
            "1",
            expected=1,
        )
        mixed = inventory_snapshot()
        assert before != mixed
        rehearsal.key(
            "key-verify",
            expected=1,
            changes={"INTEGRATION_ENCRYPTION_KEY": rehearsal.target},
        )
        rehearsal.key("key-transition", "--apply", "--key-batch-size", "2")
        after = inventory_snapshot()
        rehearsal.key("key-transition", "--apply")
        assert after == inventory_snapshot()
        rehearsal.key(
            "key-verify",
            changes={
                "INTEGRATION_ENCRYPTION_KEY": rehearsal.target,
                "AGENTCC_GATEWAY_SOURCE_ENCRYPTION_KEY": "",
            },
        )
        # No non-secret field or organization/version/active pointer changed.
        from agentcc.services.gateway_key_inventory import inventory

        for store in inventory():
            for old, new in zip(
                before[store.model.__name__], after[store.model.__name__], strict=True
            ):
                assert {k: v for k, v in old.items() if k not in store.fields} == {
                    k: v for k, v in new.items() if k not in store.fields
                }
        assert_config_semantics(apps, rehearsal.target)
        for service in ("backend", "worker", "bootstrap"):
            output = rehearsal.child_probe(
                "continuity",
                SERVICE_TYPE=service,
                STARTUP_DB_MUTATION_MODE="disabled",
                INTEGRATION_ENCRYPTION_KEY=rehearsal.target,
            )
            assert "gateway-continuity-ok" in output
        return {
            "inventory": "all-seven-stores",
            "installed_fixture": installed_fixture,
            "timings": rehearsal.timings,
        }


def main():
    from agentcc.tests.hosted_rehearsal_cases import run_all

    run_all()


if __name__ == "__main__":
    main()
