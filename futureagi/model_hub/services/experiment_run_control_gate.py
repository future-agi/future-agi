"""Selected-run control is available unless the deployment turns it off.

An unset or blank EXPERIMENT_RUN_CONTROL_ENABLED enables resume POST,
run-control GET and a stop POST that names run_ids. A nonblank environment
value uses the parsed setting; only True enables these entries. Explicit
settings overrides are respected too. Changing the environment requires an
API restart, never a rebuild.

When explicitly disabled, the three entries answer 404
``run_control_not_enabled`` before tenant lookups, Temporal queries or
signals, or writes. The schema documents them regardless of runtime admission.

The empty-body whole-experiment stop and workflow bookkeeping are unchanged.
Disabling admission neither resumes nor rewrites a paused run, and the classic
whole stop still cancels such a run.
"""

from rest_framework.exceptions import NotFound

from tfc.utils.default_on import on_unless_turned_off

ERROR_CODE = "run_control_not_enabled"
MESSAGE = "Run control is not available."


class RunControlNotEnabled(NotFound):
    """Narrowing refusal: 404 with code run_control_not_enabled."""

    default_detail = MESSAGE
    default_code = ERROR_CODE


def run_control_enabled() -> bool:
    return on_unless_turned_off("EXPERIMENT_RUN_CONTROL_ENABLED")


def ensure_run_control_enabled():
    if not run_control_enabled():
        raise RunControlNotEnabled()
