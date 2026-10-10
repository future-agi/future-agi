# Merge the saved-view revision leaf with dev's grouping redirect leaf.
#
# 0112_savedview_revision_savedviewtaborder and 0114_backfill_group_redirects
# both descend from 0111_grouping_budget_wait and do not touch the same models.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0112_savedview_revision_savedviewtaborder"),
        ("tracer", "0114_backfill_group_redirects"),
    ]

    operations = []
