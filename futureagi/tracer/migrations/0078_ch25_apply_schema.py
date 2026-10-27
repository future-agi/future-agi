"""
Django migration: applies the CH 25.3 schema files as part of `python manage.py migrate`.

This makes the operator UX: a single `manage.py migrate` brings up BOTH the
PG schema (the usual Django thing) AND the new CH `spans` table + sister
tables. No separate command needed.

How it works:
- `forwards`: imports tracer.services.clickhouse.v2.apply_schema and runs its
  main() with the bundled schema dir. Idempotent — re-running is a no-op.
- `backwards`: refuses to drop CH tables (they hold customer data). Operator
  must do that explicitly via `clickhouse-client -q "DROP TABLE spans"`.
  This matches the safety stance we take for any PG migration touching real
  customer data.

Skipping in test/CI:
- The forwards step is a no-op if CH connection is not configured (no
  CH25_HOST / CH_HOST). Tests that don't need CH pass through silently.
- Set FI_SKIP_CH25_MIGRATION=1 to skip explicitly (e.g., in environments
  where CH is provisioned out-of-band).
"""

import os

from django.db import migrations


def forwards(apps, schema_editor):
    """Apply the CH 25.3 schema. Idempotent."""
    if os.getenv("FI_SKIP_CH25_MIGRATION", "").lower() in ("1", "true", "yes"):
        print("FI_SKIP_CH25_MIGRATION=1 — skipping CH 25.3 schema application.")
        return

    try:
        from tracer.services.clickhouse.v2 import get_v2_config
        from tracer.services.clickhouse.v2 import apply_schema as v2_apply
    except ImportError as e:
        print(f"WARNING: CH 25.3 service modules not importable ({e}). Skipping schema apply.")
        print("         Run `python manage.py ch25_apply_schema` once dependencies are installed.")
        return

    cfg = get_v2_config()
    if not cfg.get("host"):
        print("WARNING: CH 25.3 host not configured (CH25_HOST / CH_HOST). Skipping schema apply.")
        print("         Run `python manage.py ch25_apply_schema` once configured.")
        return

    from pathlib import Path
    schema_dir = Path(__file__).resolve().parent.parent / "services" / "clickhouse" / "v2" / "schema"
    if not schema_dir.is_dir():
        raise RuntimeError(f"CH 25.3 schema directory not found at {schema_dir}")

    # Pass password via env (apply_schema honors CH_PASSWORD).
    os.environ.setdefault("CH_PASSWORD", cfg["password"])
    rc = v2_apply.main([
        "--schema-dir", str(schema_dir),
        "--ch-host", cfg["host"],
        "--ch-http-port", str(cfg["http_port"]),
        "--ch-user", cfg["user"],
        "--ch-database", cfg["database"],
    ])
    if rc == 2:
        # Drift detected — surface clearly so operator sees what to do.
        raise RuntimeError(
            "CH 25.3 schema drift detected. Re-run `python manage.py ch25_apply_schema --force` "
            "AFTER writing a DECISIONS log entry explaining the schema edit."
        )
    if rc != 0:
        raise RuntimeError(f"CH 25.3 schema apply failed with exit code {rc}. See structured logs.")


def backwards(apps, schema_editor):
    """Refuses to drop CH tables to protect customer data."""
    raise RuntimeError(
        "Reversing the CH 25.3 schema migration would drop tables holding customer "
        "data. If you really need to undo this, do it manually:\n"
        "  clickhouse-client -q 'DROP TABLE IF EXISTS spans SYNC'\n"
        "  clickhouse-client -q 'DROP TABLE IF EXISTS spans_v2_dead_letter SYNC'\n"
        "  clickhouse-client -q 'DROP TABLE IF EXISTS backfill_checkpoints SYNC'\n"
        "  clickhouse-client -q 'DROP TABLE IF EXISTS schema_versions SYNC'\n"
        "Then `python manage.py migrate tracer 0077` to mark this migration unapplied."
    )


class Migration(migrations.Migration):

    # Depends on the latest existing tracer migration (auto-detected by Django
    # graph at apply time; we name 0077 explicitly here for clarity).
    dependencies = [
        ("tracer", "0077_merge_20260514_1559"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
