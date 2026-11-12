"""Data migrations must bypass read/write routers, including instance saves."""

import importlib
from contextlib import contextmanager

import pytest
from django.contrib.postgres.fields import ArrayField
from django.db import connections, models
from django.db.migrations.state import ModelState, ProjectState

ALIAS = "default_direct"
pytestmark = pytest.mark.django_db(transaction=True, databases={"default", ALIAS})


def migration(name):
    return importlib.import_module(f"simulate.migrations.{name}")


def state_model(name, fields):
    return ModelState(
        "simulate",
        name,
        [("id", models.AutoField(primary_key=True)), *fields],
        options={"db_table": f"migration_alias_test_{name.lower()}"},
    )


@contextmanager
def historical_tables(*states):
    """Render historical models, with no live save methods or signal handlers."""
    state = ProjectState()
    for model_state in states:
        state.add_model(model_state)
    historical = [state.apps.get_model("simulate", item.name) for item in states]
    connection = connections[ALIAS]
    with connection.schema_editor() as editor:
        for model in historical:
            editor.create_model(model)
    try:
        yield state.apps
    finally:
        with connection.schema_editor() as editor:
            for model in reversed(historical):
                editor.delete_model(model)


def forbid_default(execute, sql, params, many, context):
    raise AssertionError(
        "migration used the default connection instead of its schema editor"
    )


def run_data_step(function, apps):
    connection = connections[ALIAS]
    # The configured router sends even instance writes to default. Explicit
    # using= is therefore necessary on both the initial queryset and each save.
    with connections["default"].execute_wrapper(forbid_default):
        with connection.schema_editor() as editor:
            function(apps, editor)


def test_0047_reads_and_saves_only_the_schema_editor_alias():
    with historical_tables(
        state_model(
            "AgentDefinition",
            [
                ("language", models.CharField(max_length=2, null=True)),
                ("languages", ArrayField(models.CharField(max_length=2), null=True)),
            ],
        ),
        state_model(
            "AgentVersion",
            [
                ("configuration_snapshot", models.JSONField(default=dict)),
            ],
        ),
    ) as apps:
        definitions = apps.get_model("simulate", "AgentDefinition").objects.using(ALIAS)
        versions = apps.get_model("simulate", "AgentVersion").objects.using(ALIAS)
        localized = definitions.create(language="fr")
        empty = definitions.create(language=None)
        legacy = versions.create(configuration_snapshot={"language": "hi", "keep": 1})
        missing = versions.create(configuration_snapshot={"keep": 2})
        run_data_step(
            migration(
                "0047_agentdefinition_authentication_method_and_more"
            ).populate_languages_and_auth_method_in_configuration_snapshot,
            apps,
        )
        assert definitions.get(pk=localized.pk).languages == ["fr"]
        assert definitions.get(pk=empty.pk).languages is None
        # Preserve the historical singular/plural behavior; this fix is routing only.
        assert versions.get(pk=legacy.pk).configuration_snapshot == {
            "language": ["hi"],
            "keep": 1,
            "authentication_method": "api_key",
        }
        assert versions.get(pk=missing.pk).configuration_snapshot == {
            "languages": ["en"],
            "keep": 2,
            "authentication_method": "api_key",
        }


def test_0063_backfills_both_models_without_overwriting_existing_fields():
    common = [
        ("vapi_call_id", models.CharField(max_length=255, null=True)),
        ("service_provider_call_id", models.CharField(max_length=255, null=True)),
        ("vapi_call_data", models.JSONField(null=True)),
        ("provider_call_data", models.JSONField(null=True)),
    ]
    with historical_tables(
        state_model(
            "CallExecution",
            [
                *common,
                ("customer_vapi_call_id", models.CharField(max_length=255, null=True)),
                ("customer_call_id", models.CharField(max_length=255, null=True)),
            ],
        ),
        state_model(
            "CallExecutionSnapshot", [(name, field.clone()) for name, field in common]
        ),
    ) as apps:
        calls = apps.get_model("simulate", "CallExecution").objects.using(ALIAS)
        snapshots = apps.get_model("simulate", "CallExecutionSnapshot").objects.using(
            ALIAS
        )
        call = calls.create(
            vapi_call_id="old-call",
            customer_vapi_call_id="old-customer",
            vapi_call_data={"duration": 3},
        )
        snapshot = snapshots.create(
            vapi_call_id="old-snapshot", vapi_call_data={"ok": True}
        )
        retained = calls.create(
            vapi_call_id="unused",
            customer_vapi_call_id="unused",
            vapi_call_data={"unused": True},
            service_provider_call_id="new",
            customer_call_id="new-customer",
            provider_call_data={"retell": {}},
        )
        invalid = snapshots.create(vapi_call_data=["not-a-dict"])
        empty = calls.create(vapi_call_data={})
        function = migration(
            "0063_remove_callexecution_logurl_and_more"
        ).backfill_updated_fields_call_execution
        run_data_step(function, apps)
        migrated = calls.get(pk=call.pk)
        assert migrated.service_provider_call_id == "old-call"
        assert migrated.customer_call_id == "old-customer"
        assert migrated.provider_call_data == {"vapi": {"duration": 3}}
        snap = snapshots.get(pk=snapshot.pk)
        assert snap.service_provider_call_id == "old-snapshot"
        assert snap.provider_call_data == {"vapi": {"ok": True}}
        kept = calls.get(pk=retained.pk)
        assert (
            kept.service_provider_call_id,
            kept.customer_call_id,
            kept.provider_call_data,
        ) == (
            "new",
            "new-customer",
            {"retell": {}},
        )
        assert snapshots.get(pk=invalid.pk).provider_call_data is None
        assert calls.get(pk=empty.pk).provider_call_data is None
        before = (
            list(calls.order_by("pk").values()),
            list(snapshots.order_by("pk").values()),
        )
        run_data_step(function, apps)
        assert before == (
            list(calls.order_by("pk").values()),
            list(snapshots.order_by("pk").values()),
        )


def test_webhook_snapshot_steps_use_selected_alias():
    with historical_tables(
        state_model(
            "AgentVersion",
            [
                ("configuration_snapshot", models.JSONField(default=dict)),
            ],
        )
    ) as apps:
        versions = apps.get_model("simulate", "AgentVersion").objects.using(ALIAS)
        row = versions.create(configuration_snapshot={"keep": 1})
        run_data_step(
            migration("0044_agentdefinition_webhook_secret").backfill_webhook_secret,
            apps,
        )
        assert versions.get(pk=row.pk).configuration_snapshot == {
            "keep": 1,
            "webhook_secret": "",
        }
        versions.filter(pk=row.pk).update(
            configuration_snapshot={"keep": 1, "webhook_secret": "old"}
        )
        run_data_step(
            migration(
                "0046_remove_agentdefinition_webhook_secret"
            ).remove_webhook_secret_from_agent_version,
            apps,
        )
        assert versions.get(pk=row.pk).configuration_snapshot == {"keep": 1}


def test_harness_backfills_use_selected_alias_for_update_and_bulk_update():
    from django.utils import timezone

    with historical_tables(
        state_model(
            "HostedHarnessJob",
            [
                ("name", models.CharField(max_length=255, default="")),
                ("payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField()),
                ("terminal_at", models.DateTimeField(null=True)),
                ("content_updated_at", models.DateTimeField(null=True)),
            ],
        )
    ) as apps:
        jobs = apps.get_model("simulate", "HostedHarnessJob").objects.using(ALIAS)
        now = timezone.now()
        first = jobs.create(created_at=now, payload={"metadata": {"name": "Example"}})
        kept = jobs.create(
            created_at=now, terminal_at=now, name="Existing", content_updated_at=now
        )
        run_data_step(
            migration(
                "0087_hostedharnessjob_content_updated_at"
            ).backfill_content_updated_at,
            apps,
        )
        names = migration("0088_hostedharnessjob_name")
        # Force the full-batch and trailing-batch write paths without hundreds of rows.
        original = names._CHUNK
        try:
            names._CHUNK = 1
            run_data_step(names.backfill_names, apps)
        finally:
            names._CHUNK = original
        assert jobs.get(pk=first.pk).name == "Example"
        assert jobs.get(pk=first.pk).content_updated_at == now
        assert jobs.get(pk=kept.pk).name == "Existing"
        trailing = jobs.create(
            created_at=now, payload={"source": {"repository": "org/repo"}}
        )
        run_data_step(names.backfill_names, apps)
        assert jobs.get(pk=trailing.pk).name == "repo"


def test_filter_canonicalization_uses_selected_alias():
    with historical_tables(
        state_model(
            "SimulateEvalConfig",
            [
                ("filters", models.JSONField(default=list)),
            ],
        )
    ) as apps:
        configs = apps.get_model("simulate", "SimulateEvalConfig").objects.using(ALIAS)
        row = configs.create(
            filters=[{"columnId": "traceId", "filterConfig": {"filterOp": "is"}}]
        )
        run_data_step(
            migration(
                "0074_canonicalize_simulate_eval_config_filters"
            ).canonicalize_simulate_eval_config_filters,
            apps,
        )
        assert configs.get(pk=row.pk).filters == [
            {"column_id": "trace_id", "filter_config": {"filter_op": "equals"}},
        ]


def test_credential_create_link_and_snapshot_update_use_selected_alias(monkeypatch):
    monkeypatch.setattr(
        "agentcc.services.credential_manager.encrypt_token",
        lambda value: "encrypted-test-value",
    )
    with historical_tables(
        state_model(
            "AgentDefinition",
            [
                ("api_key", models.TextField(default="")),
                ("assistant_id", models.TextField(default="")),
                ("provider", models.TextField(default="vapi")),
            ],
        ),
        state_model(
            "AgentVersion",
            [
                (
                    "agent_definition",
                    models.ForeignKey(
                        "simulate.AgentDefinition", on_delete=models.CASCADE
                    ),
                ),
                ("version_number", models.IntegerField()),
                ("configuration_snapshot", models.JSONField(default=dict)),
            ],
        ),
        state_model(
            "ProviderCredentials",
            [
                (
                    "agent_definition",
                    models.OneToOneField(
                        "simulate.AgentDefinition", on_delete=models.CASCADE, null=True
                    ),
                ),
                (
                    "agent_version",
                    models.OneToOneField(
                        "simulate.AgentVersion", on_delete=models.CASCADE, null=True
                    ),
                ),
                ("provider_type", models.TextField()),
                ("api_key", models.TextField()),
                ("assistant_id", models.TextField()),
            ],
        ),
    ) as apps:
        definitions = apps.get_model("simulate", "AgentDefinition").objects.using(ALIAS)
        versions = apps.get_model("simulate", "AgentVersion").objects.using(ALIAS)
        credentials = apps.get_model("simulate", "ProviderCredentials").objects.using(
            ALIAS
        )
        agent = definitions.create(
            api_key="synthetic-input", assistant_id="assistant", provider="retell"
        )
        definitions.create()  # An agent with no key must not get a credential.
        older = versions.create(
            agent_definition=agent,
            version_number=1,
            configuration_snapshot={"api_key": "old", "keep": 1},
        )
        latest = versions.create(
            agent_definition=agent,
            version_number=2,
            configuration_snapshot={
                "livekit_api_key": "old",
                "livekit_api_secret": "old",
                "keep": 2,
            },
        )
        run_data_step(migration("0071_provider_credentials").backfill_credentials, apps)
        assert credentials.count() == 1
        credential = credentials.get()
        assert (
            credential.provider_type,
            credential.api_key,
            credential.assistant_id,
        ) == (
            "retell",
            "encrypted-test-value",
            "assistant",
        )
        backfill = migration("0076_backfill_provider_credentials")
        run_data_step(backfill.backfill_agent_version, apps)
        assert credentials.get().agent_version_id == latest.pk
        run_data_step(backfill.remove_credential_fields_from_snapshots, apps)
        assert versions.get(pk=older.pk).configuration_snapshot == {"keep": 1}
        assert versions.get(pk=latest.pk).configuration_snapshot == {"keep": 2}
