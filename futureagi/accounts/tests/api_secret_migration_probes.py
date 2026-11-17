"""Probes for the API-secret migration tests (READINESS L03): not a test module.

``test_api_secret_migration_locks.py`` and ``test_api_secret_seal_failures.py``
run the real accounts migrations through ``MigrationExecutor`` on real Postgres
and, from a second connection, do what a concurrent request does.
"""

import json
import os
import uuid
from typing import NamedTuple

import psycopg
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

from accounts.models import Organization, User
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

BEFORE = (
    "accounts",
    "0025_gcpmarketplaceprocessedevent_gcpmarketplaceaccount_and_more",
)
SCHEMA = ("accounts", "0026_orgapikey_secret_hash")
SEAL = ("accounts", "0027_seal_api_secrets")
# dev's other children of 0025 (integration round 7): the API-key type and
# simulation-agent columns and the auth-token scope. The live models read their
# columns, so every forward step below applies them with its own target; going
# back to BEFORE unapplies them with the rest.
LIVE_SIBLINGS = (
    ("accounts", "0026_authtoken_scope"),
    ("accounts", "0027_orgapikey_simulation_agent_id"),
)
# The first query of every API-key request (find_api_key; fi-collector's ValidateKey).
LOOKUP = (
    "SELECT secret_key, secret_key_hash FROM accounts_orgapikey"
    " WHERE api_key = %s AND enabled AND NOT deleted"
)


def database():
    """The default database's own connection, the object ``manage.py migrate``
    builds its executor on: the migrations here run through an executor built
    the same way, never on the ``django.db.connection`` proxy object.
    """
    return connections[DEFAULT_DB_ALIAS]


def migration_executor():
    executor = MigrationExecutor(database())
    executor.loader.build_graph()
    return executor


def forward_plan(executor, targets):
    """The unapplied migrations the targets need, in Django's order.

    Forward steps only: for a target that is already applied, Django plans a
    walk back through every app that builds on it.
    """
    graph = executor.loader.graph
    applied_keys, plan = set(executor.loader.applied_migrations), []
    for target in targets:
        for key in graph.forwards_plan(target):
            if key not in applied_keys:
                applied_keys.add(key)
                plan.append((graph.nodes[key], False))
    return plan


def walk_back_plan(executor):
    """Every applied accounts migration after BEFORE, newest first.

    The order Django gives a walk back to BEFORE, with every other app's
    migrations kept applied: these tests are about the accounts migrations,
    and some migrations of other apps are not reversible.
    """
    graph = executor.loader.graph
    applied_keys, plan = set(executor.loader.applied_migrations), []
    for child in sorted(graph.node_map[BEFORE].children):
        if child[0] != BEFORE[0]:
            continue
        for key in graph.backwards_plan(child):
            if key[0] == BEFORE[0] and key in applied_keys:
                applied_keys.discard(key)
                plan.append((graph.nodes[key], True))
    return plan


def migrate_to(target):
    executor = migration_executor()
    if target == BEFORE:
        plan = walk_back_plan(executor)
    else:
        plan = forward_plan(executor, [target, *LIVE_SIBLINGS])
    executor.migrate([target], plan=plan)
    return executor.loader.project_state([target]).apps


def migrate_to_latest():
    """Every accounts leaf: the schema the rest of a test session expects.

    Migrating to BEFORE also unapplies the line's other child of 0025
    (0026_host_event_outbox, joined by 0028_merge_20261019_1400), and migrating
    to SEAL alone would leave it unapplied for whichever tests run next on this
    database. A test restores with this, never with SEAL.
    """
    executor = migration_executor()
    leaves = executor.loader.graph.leaf_nodes()
    executor.migrate(leaves, plan=forward_plan(executor, leaves))


def applied(target) -> bool:
    return target in MigrationRecorder(database()).applied_migrations()


def plaintext_ids() -> set:
    with database().cursor() as cursor:
        cursor.execute("SELECT id FROM accounts_orgapikey WHERE secret_key <> ''")
        return {row[0] for row in cursor.fetchall()}


def two_tenants():
    tenants = []
    for label in ("a", "b"):
        org = Organization.objects.create(name=f"Seal tenant {label}")
        owner = User.objects.create_user(
            email=f"seal-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
            password="testpassword123",
            name=f"Owner {label}",
            organization=org,
            organization_role=OrganizationRoles.OWNER,
        )
        tenants.append((org, owner))
    clear_workspace_context()
    return tenants


class Seeded(NamedTuple):
    id: uuid.UUID
    api_key: str
    secret: str
    kind: str


def seed_plaintext_keys(apps, tenants, per_tenant) -> list[Seeded]:
    """Plaintext keys at the 0025 schema, as the previous release stored them.

    Each tenant gets a system key, an MCP key and user keys; returned in primary
    key order, the order the batches take them in.
    """
    LegacyKey = apps.get_model("accounts", "OrgApiKey")
    rows = []
    for org, owner in tenants:
        for i in range(per_tenant):
            kind = "system" if i == 0 else "mcp" if i == 1 else "user"
            rows.append(
                LegacyKey(
                    id=uuid.uuid4(),
                    name=f"{kind}-{i}",
                    api_key=uuid.uuid4().hex,
                    secret_key=uuid.uuid4().hex,
                    organization_id=org.id,
                    type=kind,
                    user_id=None if kind == "system" else owner.id,
                )
            )
    LegacyKey._base_manager.bulk_create(rows, batch_size=1000)
    return sorted(Seeded(r.id, r.api_key, r.secret_key, r.type) for r in rows)


def probe_connection(lock_timeout="200ms"):
    """A second backend, as a concurrent request has: autocommit, bounded waits."""
    settings = database().settings_dict
    probe = psycopg.connect(
        host=settings["HOST"],
        port=settings["PORT"],
        dbname=settings["NAME"],
        user=settings["USER"],
        password=settings["PASSWORD"],
        autocommit=True,
    )
    probe.execute(f"SET lock_timeout = '{lock_timeout}'")
    probe.execute("SET statement_timeout = '30s'")
    return probe


def lookup(probe, api_key):
    """("answered", sealed?) or ("waited", None) if it needed a lock it could not get."""
    try:
        secret_key, secret_key_hash = probe.execute(LOOKUP, [api_key]).fetchone()
    except psycopg.errors.LockNotAvailable:
        return "waited", None
    return "answered", secret_key == "" and secret_key_hash != ""


def reseal_from_outside(probe, key, hint) -> None:
    """What a request's re-seal on use writes, from another connection.

    The hint is a marker, so a test can tell whether a later write replaced it.
    """
    from accounts.api_secrets import hash_secret

    probe.execute(
        "UPDATE accounts_orgapikey SET secret_key = '', secret_key_hash = %s,"
        " secret_key_hint = %s WHERE id = %s",
        [hash_secret(key.secret), hint, key.id],
    )


def table_locks(probe, pid) -> set:
    """The table-level lock modes ``pid`` holds on accounts_orgapikey."""
    rows = probe.execute(
        "SELECT mode FROM pg_locks WHERE pid = %s AND locktype = 'relation'"
        " AND relation = 'accounts_orgapikey'::regclass",
        [pid],
    ).fetchall()
    return {mode for (mode,) in rows}


def backend_state(probe, pid) -> str:
    query = "SELECT state FROM pg_stat_activity WHERE pid = %s"
    return probe.execute(query, [pid]).fetchone()[0]


def observe(probe, keys):
    """What a concurrent request sees right now, and what the migration holds."""
    pid = database().connection.info.backend_pid
    return {
        "lookups": {name: lookup(probe, key.api_key) for name, key in keys},
        "migration_table_locks": table_locks(probe, pid),
        "migration_state": backend_state(probe, pid),
    }


def write_receipt(name, observed):
    """API_SECRET_LOCK_RECEIPT=<path> keeps what the probes saw (evidence runs)."""
    path = os.environ.get("API_SECRET_LOCK_RECEIPT")
    if not path:
        return
    receipts = {}
    if os.path.exists(path):
        with open(path) as handle:
            receipts = json.load(handle)
    receipts[name] = observed
    with open(path, "w") as handle:
        json.dump(receipts, handle, indent=2, sort_keys=True, default=sorted)
