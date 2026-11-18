"""Merge migration: 0078_ch25_apply_schema + 0078_canonicalize_persisted_filter_contracts.

The two 0078 files were created in parallel branches:
  - 0078_ch25_apply_schema (this migration tree) added the CH 25.3 schema runner.
  - 0078_canonicalize_persisted_filter_contracts (sibling branch) reshapes
    persisted filter JSON in PG.

They don't touch overlapping state — one runs SQL against CH, the other
backfills a PG model — so a no-op merge is safe.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0078_ch25_apply_schema"),
        ("tracer", "0078_canonicalize_persisted_filter_contracts"),
    ]

    operations = []
