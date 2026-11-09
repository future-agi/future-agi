"""F06 selected-run control admission boundary (default OFF).

EXPERIMENT_RUN_CONTROL_ENABLED comes from the env var of the same name and
defaults to False. It is read per API request, so turning it on is an env
change plus an API restart, never a rebuild. Only an explicit True opens the
three selected-run entries: resume POST, run-control GET and a stop POST that
names run_ids. OFF they answer 404 ``run_control_not_enabled`` before tenant
lookups, Temporal queries or signals, or writes. The generated schema always
documents /resume/ and /run-control/ with the operator opt-in requirement;
documentation does not open the runtime gate.

Not gated, by design: the empty-body whole-experiment stop
(services/experiment_legacy_stop.py) and the workflow, worker and run-state
modules. Their bind and completion writes are ordinary-run bookkeeping, and
the only non-test sender of set_run_stopped (services/experiment_run_control.py)
is reachable only through the three gated entries, so no run can pause while
OFF. OFF is admission policy: it neither resumes nor rewrites a run paused
while ON, and the classic whole stop still cancels such a run.
"""

from django.conf import settings
from rest_framework.exceptions import NotFound

ERROR_CODE = "run_control_not_enabled"
MESSAGE = "Run control is not available."


class RunControlNotEnabled(NotFound):
    """Narrowing refusal: 404 with code run_control_not_enabled."""

    default_detail = MESSAGE
    default_code = ERROR_CODE


def run_control_enabled() -> bool:
    return getattr(settings, "EXPERIMENT_RUN_CONTROL_ENABLED", False) is True


def ensure_run_control_enabled():
    if not run_control_enabled():
        raise RunControlNotEnabled()
