# Joins this branch's entity-tag chain with dev's eval recalculation chain
# (PR #3845). Dependencies only; no operations.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0134_merge_entity_tag_assignment_and_sampling"),
        ("tracer", "0134_merge_eval_recalculation_and_sampling"),
    ]

    operations = []
