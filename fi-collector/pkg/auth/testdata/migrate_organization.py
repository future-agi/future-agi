"""Apply the repository's real Organization schema operations to a test database.

Only Organization is migrated here; the collector's other tables remain test
fixtures. Importing the migration modules directly avoids application startup
hooks and unrelated data migrations. No schema fields are duplicated here.
Run with Python containing Django and psycopg, through FI_TEST_MIGRATION_PYTHON.
"""

import importlib.util
import os
from graphlib import TopologicalSorter
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import UUID

import django
from django.conf import settings


def main():
    url = urlparse(os.environ["FI_TEST_ORGANIZATION_DSN"])
    database = url.path.removeprefix("/")
    if (
        url.scheme != "postgres"
        or url.hostname != "127.0.0.1"
        or not url.port
        or not database.startswith("routing_test_")
        or not database.removeprefix("routing_test_").isdigit()
    ):
        raise ValueError(
            "organization migration requires an isolated routing test database"
        )
    org_id = UUID(os.environ["FI_TEST_ORGANIZATION_ID"])
    settings.configure(
        INSTALLED_APPS=[],
        AUTH_USER_MODEL="accounts.User",
        USE_TZ=True,
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": database,
                "USER": unquote(url.username or ""),
                "PASSWORD": unquote(url.password or ""),
                "HOST": url.hostname,
                "PORT": url.port,
            }
        },
    )
    django.setup()
    from django.db import connection
    from django.db.migrations import Migration
    from django.db.migrations.state import ProjectState

    directory = Path(__file__).resolve().parents[4] / "futureagi/accounts/migrations"
    migrations = {}
    for path in sorted(directory.glob("[0-9]*.py")):
        spec = importlib.util.spec_from_file_location(
            "collector_migration_" + path.stem, path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        migrations[path.stem] = module.Migration(path.stem, "accounts")
    graph = {
        name: [
            dependency
            for app, dependency in migration.dependencies
            if app == "accounts"
        ]
        for name, migration in migrations.items()
    }
    state = ProjectState()
    for name in TopologicalSorter(graph).static_order():
        source = migrations[name]
        operations = [
            operation
            for operation in source.operations
            if getattr(operation, "model_name", getattr(operation, "name", "")).lower()
            == "organization"
        ]
        if not operations:
            continue
        migration = Migration(name, "accounts")
        migration.operations = operations
        with connection.schema_editor() as editor:
            state = migration.apply(state, editor)
        print(f"applied {name}: {len(operations)} organization schema operations")
    organization = state.apps.get_model("accounts", "Organization")
    organization.objects.create(id=org_id, name="synthetic-collector-organization")
    connection.close()


if __name__ == "__main__":
    main()
