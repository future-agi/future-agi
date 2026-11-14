# Joins this branch's entity-tag chain with dev's eval put-back migration
# (PR #4093). Dependencies only; no operations.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0135_merge_entity_tag_assignment_and_eval_recalculation"),
        ("tracer", "0135_eval_put_back"),
    ]

    operations = []
