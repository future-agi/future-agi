"""`manage.py migrate --noinput`, stopped where it would send post_migrate.

.ci/e2e-db-control.sh runs this text as `python -c` in the stack's own image,
from the folder that holds manage.py, so nothing of .ci/ has to be in the
image. Migrate runs as the stack's own migrate does, with the same command
line; only the post_migrate signal is not sent. The scratch database is left
with every migration applied and no receiver run: the point the backend's
dump is taken at too (.ci/migrated_db.py).

The receivers seed rows from code that no migration names (pricing and rate
limits, node templates, prompt labels, content types and permissions). In
the dump they would need every such file in the key. Left out, they come
from the code under test: a leg that restores the dump boots with its
stack's own migrate, which applies nothing and still sends post_migrate.
"""

import runpy
import sys
from unittest import mock

from django.core.management.commands import migrate

reached = []


def stop(*args, **kwargs):
    reached.append(True)


# Before Django starts: the apps read the command line when they load.
sys.argv = ["manage.py", "migrate", "--noinput"]
with mock.patch.object(migrate, "emit_post_migrate_signal", stop):
    runpy.run_path("manage.py", run_name="__main__")
if reached != [True]:
    sys.exit("migrate ended without reaching post_migrate")
