"""Raw project inserts survive the sampling contact-attribute column rollout.

Collectors and older pods insert tracer_project rows outside the Django ORM and
do not know ``sampling_contact_attribute``. The column must carry a database
default from the very migration that adds it (0117_cohort_sampling), so there is
no window, even if the follow-up migration is delayed, where the column is
NOT NULL without a default.
"""

from django.db import connection
from django.db.migrations import AddField, AlterField
from django.db.migrations.loader import MigrationLoader

from tracer.models.project import Project

ADDS = ("tracer", "0117_cohort_sampling")
FOLLOW_UP = ("tracer", "0117_sampling_project_db_default")


def _operation(loader, key, kind):
    migration = loader.graph.nodes[key]
    matches = [
        op
        for op in migration.operations
        if isinstance(op, kind)
        and op.model_name == "project"
        and op.name == "sampling_contact_attribute"
    ]
    assert len(matches) == 1, key
    return matches[0]


def test_column_is_added_with_its_database_default():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    field = _operation(loader, ADDS, AddField).field
    assert (field.default, field.db_default, field.null) == ("", "", False)


def test_follow_up_migration_is_a_schema_no_op():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    added = _operation(loader, ADDS, AddField).field
    altered = _operation(loader, FOLLOW_UP, AlterField).field
    # Identical deconstruction: Django emits no SQL for this AlterField, and
    # graphs that already recorded it stay consistent.
    assert added.deconstruct()[1:] == altered.deconstruct()[1:]
    model_field = Project._meta.get_field("sampling_contact_attribute")
    assert model_field.deconstruct()[1:] == added.deconstruct()[1:]


def test_database_column_has_a_default_for_raw_inserts(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'tracer_project' "
            "AND column_name = 'sampling_contact_attribute'"
        )
        default, nullable = cursor.fetchone()
    assert default is not None
    assert nullable == "NO"
