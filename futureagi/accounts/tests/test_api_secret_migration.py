"""Real Postgres upgrade: preserve every secret, admit only live personal keys.

Rows are written at the 0025 schema across two tenants and every key state.
Historical models exercise lookup/resealing at 0026 and storage at 0027; the
current authenticator runs only after every current migration is applied.
Retired types retain their hashes/ciphertext but cannot authenticate. The
reverse still restores recoverable secrets or refuses by name for one-way
hashes. Lock/batch probes live in test_api_secret_migration_locks.py.
"""

import base64
import importlib
import uuid

import pytest
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from rest_framework.test import APIClient

from accounts.api_key_lookup import find_api_key, recoverable_secret
from accounts.api_secrets import hash_secret
from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace
from accounts.services.workspace_membership import create_workspace_membership
from accounts.tests.api_secret_migration_probes import LIVE_SIBLINGS
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

BEFORE = (
    "accounts",
    "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more",
)
SCHEMA = ("accounts", "0026_orgapikey_secret_hash")
AFTER = ("accounts", "0027_seal_api_secrets")
HEALTH_URL = "/api/public/health"
OBSERVE_URL = "/accounts/omega/observe-target/"


def _migrate(target):
    # Forward steps also apply dev's other children of 0025, whose columns the
    # live models read (api_secret_migration_probes.LIVE_SIBLINGS, round 7).
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate([target] if target == BEFORE else [target, *LIVE_SIBLINGS])
    return executor.loader.project_state([target]).apps


def _migrate_to_latest():
    """Every app's leaf, as the rest of the session expects: BEFORE also
    unapplies 0026_host_event_outbox, which AFTER alone would not bring back,
    and the other apps' migrations that depend on accounts after 0025."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _tenant(label):
    org = Organization.objects.create(name=f"Migration tenant {label}")
    owner = User.objects.create_user(
        email=f"mig-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=f"Owner {label}",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.objects.create(
        name=f"WS {label}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=owner,
        organization=org,
        defaults={"role": OrganizationRoles.OWNER, "is_active": True},
    )
    create_workspace_membership(
        user=owner,
        workspace=workspace,
        organization_membership=membership,
        role=OrganizationRoles.WORKSPACE_MEMBER,
    )
    return org, owner, workspace


def _plaintext_count():
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM accounts_orgapikey WHERE secret_key <> ''")
        return cursor.fetchone()[0]


def _applied():
    return {
        name
        for app, name in MigrationRecorder(connection).applied_migrations()
        if app == "accounts"
    }


def _columns():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'accounts_orgapikey'"
        )
        return {row[0] for row in cursor.fetchall()}


@pytest.mark.django_db(transaction=True)
def test_0026_and_0027_seal_every_secret_and_only_personal_keys_keep_working():
    clear_workspace_context()
    tenants = [_tenant("a"), _tenant("b")]
    old_apps = _migrate(BEFORE)
    try:
        assert "secret_key_hash" not in _columns()
        LegacyKey = old_apps.get_model("accounts", "OrgApiKey")
        keys = []
        for org, owner, workspace in tenants:
            for kind, extra in (
                ("system", {}),
                ("user", {"user_id": owner.id}),
                ("user", {"user_id": owner.id, "workspace_id": workspace.id}),
                ("mcp", {"user_id": owner.id}),
                ("user-disabled", {"user_id": owner.id, "enabled": False}),
                ("user-deleted", {"user_id": owner.id, "deleted": True}),
            ):
                secret = uuid.uuid4().hex
                row = LegacyKey._base_manager.create(
                    name=f"{kind}-{uuid.uuid4().hex[:6]}",
                    api_key=uuid.uuid4().hex,
                    secret_key=secret,
                    organization_id=org.id,
                    type=kind.split("-")[0],
                    **extra,
                )
                keys.append((kind, row.api_key, secret, org, owner, workspace, extra))
        assert _plaintext_count() == len(keys) == 12

        schema_apps = _migrate(SCHEMA)
        SchemaKey = schema_apps.get_model("accounts", "OrgApiKey")
        # Do not run today's model/authenticator on yesterday's schema. This
        # phase pins the original low-level verified-secret reseal behaviour.
        assert "secret_key_hash" in _columns() and _plaintext_count() == 12
        assert "retired_at" not in _columns()
        assert not SchemaKey._base_manager.exclude(secret_key_hash="").exists()
        early = [k for k in keys if k[0] == "user" and "workspace_id" in k[6]]
        for _kind, api_key, secret, org, _owner, _workspace, _extra in early:
            scoped = SchemaKey._base_manager.filter(organization_id=org.pk)
            plaintext_before = _plaintext_count()
            assert find_api_key(scoped, api_key, secret + "x") is None
            assert _plaintext_count() == plaintext_before
            assert find_api_key(scoped, api_key, secret) is not None
            other = SchemaKey._base_manager.exclude(organization_id=org.pk)
            assert find_api_key(other, api_key, secret) is None
        assert _plaintext_count() == 12 - len(early) == 10

        sealed_apps = _migrate(AFTER)
        SealedKey = sealed_apps.get_model("accounts", "OrgApiKey")
        assert _plaintext_count() == 0
        assert "retired_at" not in _columns()
        sealed = {}
        for kind, api_key, secret, org, _owner, _workspace, extra in keys:
            stored = SealedKey._base_manager.get(api_key=api_key)
            assert stored.secret_key_hash == hash_secret(secret)
            assert bool(stored.secret_key_ciphertext) == (kind == "system")
            assert stored.organization_id == org.pk
            assert stored.user_id == extra.get("user_id")
            if kind == "system":
                assert recoverable_secret(stored) == secret
            sealed[api_key] = (stored.secret_key_hash, stored.secret_key_ciphertext)

        # Only now may current HTTP authentication select the retirement fields.
        _migrate_to_latest()
        assert {"retired_at", "retired_reason", "prior_enabled"} <= _columns()
        assert "0031_retire_org_keys" in _applied()
        from accounts.models import OrgApiKey

        client = APIClient()
        for kind, api_key, secret, org, _owner, _workspace, extra in keys:
            stored = OrgApiKey._base_manager.get(api_key=api_key)
            assert (stored.secret_key_hash, stored.secret_key_ciphertext) == sealed[
                api_key
            ]
            assert stored.organization_id == org.pk
            retired = kind in ("system", "mcp")
            if retired:
                assert not stored.enabled and stored.prior_enabled is True
                assert stored.retired_at is not None and stored.retired_reason == "733"
            else:
                assert stored.enabled == extra.get("enabled", True)
                assert stored.retired_at is None and stored.prior_enabled is None
            headers = {"HTTP_X_API_KEY": api_key, "HTTP_X_SECRET_KEY": secret}
            basic = base64.b64encode(f"{api_key}:{secret}".encode()).decode()
            wrong_basic = base64.b64encode(f"{api_key}:{secret}x".encode()).decode()
            for request, wrong in (
                (headers, {**headers, "HTTP_X_SECRET_KEY": secret + "x"}),
                (
                    {"HTTP_AUTHORIZATION": f"Basic {basic}"},
                    {"HTTP_AUTHORIZATION": f"Basic {wrong_basic}"},
                ),
            ):
                clear_workspace_context()
                resp = client.get(HEALTH_URL, **request)
                assert resp.status_code == (200 if kind == "user" else 401)
                if retired:
                    assert resp.json()["code"] == "api_key_retired"
                    assert resp.json()["create_url"] == "/dashboard/keys"
                elif kind != "user":
                    assert resp.json()["code"] != "api_key_retired"
                    assert "create_url" not in resp.json()
                clear_workspace_context()
                denied = client.get(HEALTH_URL, **wrong)
                assert denied.status_code == 401
                assert denied.json()["code"] != "api_key_retired"
                assert "create_url" not in denied.json()
            if kind == "system":
                assert stored.reveal_secret() == secret
            if kind == "user" and "workspace_id" in extra:
                clear_workspace_context()
                observe = client.get(
                    OBSERVE_URL, {"project_id": str(uuid.uuid4())}, **headers
                )
                # Authenticated and scoped: the absent project is a 404, never a 401.
                assert observe.status_code == 404

        # Reverse removes the later retirement schema first, then refuses at
        # 0027: undeleted user/MCP hashes cannot reconstruct their plaintext.
        with pytest.raises(IrreversibleError, match="one-way hash"):
            _migrate(BEFORE)
        assert "secret_key_hash" in _columns() and _plaintext_count() == 0
        assert {SCHEMA[1], AFTER[1]} <= _applied()
        assert "retired_at" not in _columns()

        # Use the historical model again after the partial reverse. Without
        # one-way keys, 0027 restores system plaintext from the preserved copy.
        SealedKey._base_manager.exclude(type="system").update(deleted=True)
        _migrate(BEFORE)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT api_key, secret_key FROM accounts_orgapikey WHERE type = 'system'"
            )
            restored = dict(cursor.fetchall())
        assert restored == {k[1]: k[2] for k in keys if k[0] == "system"}
    finally:
        _migrate_to_latest()
        assert _plaintext_count() == 0


SEALED_COLUMNS = ("secret_key_hash", "secret_key_hint", "secret_key_ciphertext")


@pytest.mark.django_db
def test_0026_adds_exactly_the_columns_the_model_declares():
    """0026's DDL is written by hand, one ALTER TABLE in its own transaction, so the
    table lock is held for one statement (test_api_secret_migration_locks.py).
    This pins it to what Django's own AddField emits for the model's fields (plus
    IF NOT EXISTS, which keeps it re-runnable), and to the columns the migrated
    test database has.
    """
    from accounts.models import OrgApiKey

    module = importlib.import_module("accounts.migrations.0026_orgapikey_secret_hash")
    with connection.schema_editor(collect_sql=True) as editor:
        expected = []
        for name in SEALED_COLUMNS:
            field = OrgApiKey._meta.get_field(name)
            definition, params = editor.column_sql(
                OrgApiKey, field, include_default=True
            )
            quoted = tuple(editor.quote_value(param) for param in params)
            expected.append(
                f"ADD COLUMN IF NOT EXISTS {editor.quote_name(field.column)}"
                f" {definition % quoted}"
            )
    added = " ".join(module._ADD_COLUMNS.split())
    assert added.count("ADD COLUMN") == len(expected), added
    for clause in expected:
        assert clause in added, (clause, added)
    dropped = " ".join(module._DROP_COLUMNS.split())
    assert dropped.count("DROP COLUMN") == len(SEALED_COLUMNS)
    for name in SEALED_COLUMNS:
        assert f'DROP COLUMN IF EXISTS "{name}" CASCADE' in dropped

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,"
            " pg_get_expr(d.adbin, d.adrelid) FROM pg_attribute a"
            " LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum"
            " WHERE a.attrelid = 'accounts_orgapikey'::regclass AND a.attname = ANY(%s)",
            [list(SEALED_COLUMNS)],
        )
        columns = {name: rest for name, *rest in cursor.fetchall()}
    assert columns == {
        "secret_key_hash": ["character varying(128)", True, "''::character varying"],
        "secret_key_hint": ["character varying(32)", True, "''::character varying"],
        "secret_key_ciphertext": ["text", True, "''::text"],
    }
