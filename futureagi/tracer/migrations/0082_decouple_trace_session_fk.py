# Decouple the Trace.session FK constraint (CH-derived-dimensions, P3b flip).
#
# Migration 0080 decoupled the telemetry FKs (span→end_user, span→trace,
# eval_logger→*) so CH-scale rows can carry ids whose PG parent row is gone. It
# MISSED ``Trace.session`` — the live DB still enforces
# ``tracer_trace_session_id_d7882692_fk_trace_session_id`` (deferred). The P3b
# flip stamps a DETERMINISTIC ``trace.session_id`` (DESIGN §3) for which no PG
# ``trace_session`` row is created post-flip, so that FK would raise IntegrityError
# at COMMIT for every net-new session. Dropping the constraint (no data change,
# the ``session`` index stays) makes the stamp viable and restores the symmetry
# with the span→end_user column the flip also stamps. The column itself is dropped
# at P4 (DESIGN §8).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tracer', '0081_trace_session_overlay'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trace',
            name='session',
            field=models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='traces', to='tracer.tracesession'),
        ),
    ]
