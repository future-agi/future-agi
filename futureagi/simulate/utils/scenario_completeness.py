"""
Gate that prevents test execution unless every selected scenario is attached
to the run-test and has status == "Completed". Used by all three execute
endpoints — chat, voice / agent_definition, and prompt simulation — so direct
API/SDK callers get the same 400 the UI button now blocks.

Empty / None `scenario_ids` is itself a 400: a run requires at least one
scenario.

The completeness check scopes through `run_test.scenarios`, so
cross-organization UUIDs and scenarios not attached to the run-test are
silently ignored by the gate (they're handled — or rejected — downstream by
the executor).
"""

from model_hub.models.choices import StatusType
from tfc.utils.general_methods import GeneralMethods


def check_scenarios_incomplete(scenario_ids, run_test):
    """
    Return None when every scenario in `scenario_ids` is attached to
    `run_test` and has status == "Completed". Otherwise return a 400
    Response.

    `scenario_ids` may be a list of UUIDs or strings. Empty / None is itself
    a 400 — a run needs at least one scenario.
    """
    if not scenario_ids:
        return GeneralMethods().bad_request(
            {
                "error": "No scenarios",
                "detail": "At least one scenario is required to execute the test.",
                "scenarios": [],
            }
        )

    incomplete = list(
        run_test.scenarios.filter(id__in=scenario_ids, deleted=False)
        .exclude(status=StatusType.COMPLETED.value)
        .values("id", "name", "status")
    )

    if not incomplete:
        return None

    return GeneralMethods().bad_request(
        {
            "error": "Scenarios incomplete",
            "detail": (
                f"{len(incomplete)} scenario(s) are not completed. Wait for "
                "them to finish or remove them from the selection."
            ),
            "scenarios": [
                {"id": str(s["id"]), "name": s["name"], "status": s["status"]}
                for s in incomplete
            ],
        }
    )
