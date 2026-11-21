"""Set up pytest-django's real database before measuring any shard test.

For dump qualification, record the state immediately before post_migrate in a
fresh migrate and in a restore. Comparing here avoids treating freshly generated
UUIDs and timestamps in signal handlers as lost migration data. Both runs must
then execute the normal signal handlers and satisfy the same seed witnesses.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management.commands import migrate
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


@pytest.fixture(scope="session", autouse=True)
def _record_seed_boundary():
    path = os.getenv("CI_PRESEED_DIGEST")
    if not path:
        yield
        return

    original = migrate.emit_post_migrate_signal

    def record(*args, **kwargs):
        db = connection.settings_dict["NAME"]
        if db not in {"test_tfc_test", "test_ci_roundtrip", "test_ci_public"}:
            raise ValueError("expected the isolated pytest database")
        digest = subprocess.check_output(
            [
                sys.executable,
                "-I",
                str(Path(__file__).with_name("db_dump.py")),
                "seed-digest",
                "test_user",
                db,
                "docker",
                "compose",
                "-f",
                "docker-compose.test.yml",
                "-p",
                "futureagi-test",
                "exec",
                "-T",
                "test-db",
            ],
            timeout=150,
        )
        # Refuse an absent or ambiguous capture; never store application rows.
        with Path(path).open("xb") as target:
            target.write(digest)
        return original(*args, **kwargs)

    with patch.object(migrate, "emit_post_migrate_signal", record):
        yield
    assert Path(path).is_file(), "pytest did not run the expected migrate"


@pytest.mark.django_db
def test_the_test_database_is_migrated_from_zero():
    from django.apps import apps
    from django.contrib.auth.models import Permission
    from django.contrib.contenttypes.models import ContentType

    from agent_playground.models.node_template import NodeTemplate
    from agent_playground.templates import get_all_templates

    executor = MigrationExecutor(connection)
    assert executor.loader.applied_migrations
    assert not executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert ContentType.objects.filter(app_label="auth", model="permission").exists()
    assert Permission.objects.filter(
        content_type__app_label="auth",
        content_type__model="permission",
        codename="add_permission",
    ).exists()
    assert set(get_all_templates()) <= set(
        NodeTemplate.objects.filter(deleted=False).values_list("name", flat=True)
    )
    # usage.0023 writes entitlements *during* migrate, not in post_migrate.
    # A schema-only shard must not pass by observing just permissions/templates.
    if apps.is_installed("ee.usage"):
        entitlement = apps.get_model("usage", "PlanEntitlement")
        assert entitlement.objects.filter(
            plan="free",
            feature="has_scim",
            organization__isnull=True,
            value_bool=True,
            deleted=False,
        ).exists()
