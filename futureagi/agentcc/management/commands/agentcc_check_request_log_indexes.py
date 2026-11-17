"""Stop an update unless agentcc 0031's request-log indexes are all valid.

Read only. Once the schema phase has applied 0031, each index it adds must be
present and valid: an interrupted concurrent build leaves one INVALID, and a
missing one was never built. Run it as the step that decides whether the update
goes on:

  manage.py agentcc_check_request_log_indexes

The hosted Job runs the same check by phase name, read only and under the
operator lock: manage.py hosted_gateway_migrate verify-indexes.

One line per index, then the count:
  request-log index <name>: valid | invalid | missing
  request-log indexes: <n> of <m> valid
It exits 0 only when every one is valid; otherwise it exits 1 naming each index
that is not, and its state, followed by the remedy in fixed words. 0031 is not
atomic and is recorded only once all its builds finish. While it is not
recorded, rerunning the schema phase repairs its indexes: 0031 drops one an
interrupted build left invalid, keeps one already valid and builds the missing
ones. Once it is recorded, a rerun does not run it again. The index names are
read from 0031 itself.

It reads through default_direct when PG_DIRECT_HOST names it. On the hosted
profile it reads through nothing else: without default_direct it refuses (exit
1) before any query, so a pod without the direct connection never answers.
"""

import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.loader import MigrationLoader

from agentcc.hosted import deployment_profile

APP = "prism"
MIGRATION = "0031_request_log_v2_indexes"
TABLE = "agentcc_request_log"
REFUSED_WITHOUT_DIRECT = (
    "On the hosted profile this check reads only through default_direct; run it "
    "in the bootstrap/operator pod with PG_DIRECT_HOST set"
)

_STATE_SQL = """
SELECT i.indisvalid
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_class t ON t.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = %s AND t.relname = %s AND n.nspname = current_schema()
"""


def index_names(loader):
    """The names of the indexes 0031 adds, in its order: AddIndex or
    AddIndexConcurrently, directly or as state operations."""

    def walk(operations):
        for operation in operations:
            yield from walk(getattr(operation, "state_operations", ()))
            index = getattr(operation, "index", None)
            if index is not None:
                yield index.name

    return list(dict.fromkeys(walk(loader.get_migration(APP, MIGRATION).operations)))


def index_state(cursor, name):
    """ "valid", "invalid" or "missing" for ``name`` on the request log."""
    cursor.execute(_STATE_SQL, [name, TABLE])
    row = cursor.fetchone()
    if row is None:
        return "missing"
    return "valid" if row[0] else "invalid"


def remedy(applied):
    """The fixed-words remedy after the stop line, by whether 0031 is recorded."""
    if applied:
        return (
            " 0031 is recorded as applied, so rerunning the schema phase does not "
            "rebuild them. Stay quiesced; drop each index named above concurrently, "
            "if it exists, and build it again concurrently with 0031's definition; "
            "then rerun this check."
        )
    return (
        " 0031 is not recorded as applied, so rerunning the schema phase repairs "
        "them: it drops an index an interrupted build left invalid, keeps one "
        "already valid and builds the missing ones. Stay quiesced; rerun the "
        "schema phase, and then this check."
    )


def check_connection():
    """default_direct when PG_DIRECT_HOST names it, else default; on the hosted
    profile default_direct or a refusal, before any query."""
    direct = "default_direct" in connections.databases and bool(
        os.getenv("PG_DIRECT_HOST")
    )
    if not direct and deployment_profile() == "hosted":
        raise CommandError(REFUSED_WITHOUT_DIRECT)
    return connections["default_direct" if direct else "default"]


def check_request_log_indexes(connection, write):
    """Write one line per index and the count; raise the stop line unless every
    index 0031 adds is valid."""
    if connection.vendor != "postgresql":
        raise CommandError("This command needs PostgreSQL")
    loader = MigrationLoader(connection)
    names = index_names(loader)
    states = {}
    with connection.cursor() as cursor:
        for name in names:
            states[name] = index_state(cursor, name)
            write(f"request-log index {name}: {states[name]}")
    valid = sum(state == "valid" for state in states.values())
    write(f"request-log indexes: {valid} of {len(names)} valid")
    not_valid = [
        f"{name} {state}" for name, state in states.items() if state != "valid"
    ]
    if not names or not_valid:
        raise CommandError(
            "stop: request-log indexes not valid: "
            + (", ".join(not_valid) or f"{MIGRATION} names no index")
            + "."
            + remedy((APP, MIGRATION) in loader.applied_migrations)
        )


class Command(BaseCommand):
    help = (
        "Read only: exit 1 unless every request-log index agentcc 0031 adds is "
        "present and valid, naming each one that is not. On the hosted profile it "
        "reads only through default_direct (PG_DIRECT_HOST) and refuses without it."
    )

    def handle(self, *args, **options):
        check_request_log_indexes(check_connection(), self.stdout.write)
