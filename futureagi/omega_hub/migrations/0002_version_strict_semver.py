# Strict SemVer for Version.version (no leading zeros; REVIEW finding: `1.0.03` shared a sort key
# with `1.0.3`). Hand-written to be safe on a database that already holds such rows:
#
#   1. `report_legacy_rows` lists any stored version that is not strict SemVer, and any entry with
#      two versions on one `semver_key`, and changes nothing: a version string is never renamed
#      (SPEC §2.3). `manage.py check --database default` (omega_hub.W002) lists them later too.
#   2. On PostgreSQL the check constraint is added NOT VALID: new and updated rows are held to it at
#      once, existing rows are not scanned, so the migration cannot fail on them. Once a maintainer
#      has yanked or removed them, a later migration can `VALIDATE CONSTRAINT`. Other backends get
#      the plain constraint. Django's state records the constraint either way.

from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope

STRICT = r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$"
NAME = "omega_hub_version_strict_semver"


def constraint():
    return models.CheckConstraint(condition=models.Q(version__regex=STRICT), name=NAME)


def report_legacy_rows(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        Version = apps.get_model("omega_hub", "Version")
        rows = Version.objects.using((schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS))
        loose = list(rows.exclude(version__regex=STRICT).values_list("entry_id", "version")[:20])
        shared = list(rows.values("entry_id", "semver_key").annotate(n=models.Count("id")).filter(n__gt=1)[:20])
        if loose or shared:
            print(f"\n  omega_hub 0002: {len(loose)} stored version(s) are not strict SemVer and {len(shared)} entr(y/ies) "
                  "have two versions on one sort key. They stay readable; new rows are held to strict SemVer. "
                  "Run `manage.py check --database default` for the list (omega_hub.W002).")


def add_constraint(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        Version = apps.get_model("omega_hub", "Version")
        if schema_editor.connection.vendor != "postgresql":
            schema_editor.add_constraint(Version, constraint())
            return
        table = schema_editor.quote_name(Version._meta.db_table)
        # STRICT is a constant with no quote in it; standard_conforming_strings keeps its backslashes.
        schema_editor.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {schema_editor.quote_name(NAME)} "
            f"CHECK ((version)::text ~ '{STRICT}') NOT VALID")


def drop_constraint(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        Version = apps.get_model("omega_hub", "Version")
        if schema_editor.connection.vendor != "postgresql":
            schema_editor.remove_constraint(Version, constraint())
            return
        table = schema_editor.quote_name(Version._meta.db_table)
        schema_editor.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {schema_editor.quote_name(NAME)}")


class Migration(migrations.Migration):

    dependencies = [
        ("omega_hub", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(report_legacy_rows, migrations.RunPython.noop, elidable=True),
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.AddConstraint(model_name="version", constraint=constraint())],
            database_operations=[migrations.RunPython(add_constraint, drop_constraint)],
        ),
    ]
