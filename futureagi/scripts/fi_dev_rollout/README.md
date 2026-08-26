# Retired CATALOG planner

The former six-table, span-only rollout planner is retired. `plan.py` is an
inert compatibility tombstone: every import or CLI execution fails before any
database, network, subprocess, schema, backfill, or activation operation.

Use the unified DEV rollout instead:

```bash
python manage.py ch25_property_catalog_dev_rollout --status
python manage.py ch25_property_catalog_dev_rollout --execute
```

The unified command retains the explicit DEV identity, acknowledgement,
workspace allowlist, isolated database, and no-production guards.
