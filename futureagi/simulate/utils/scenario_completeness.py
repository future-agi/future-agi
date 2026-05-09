"""
Gate that prevents test execution when any selected scenario is incomplete
(still being generated, or generation failed). Used by all three execute
endpoints — chat, voice / agent_definition, and prompt simulation — so direct
API/SDK callers get the same 400 the UI button now blocks.

The check scopes through `run_test.scenarios`, so cross-organization UUIDs and
scenarios not attached to the run-test are silently ignored by the gate
(they're handled — or rejected — downstream by the executor).
"""

from model_hub.models.choices import StatusType
from tfc.utils.general_methods import GeneralMethods


def check_scenarios_incomplete(scenario_ids, run_test):
    """
    Return None if every scenario in `scenario_ids` that's attached to
    `run_test` has status == "Completed". Otherwise return a 400 Response
    listing the offenders.

    `scenario_ids` may be a list of UUIDs or strings; empty/None is treated
    as complete (the caller is responsible for the "at least one scenario"
    check).
    """
    if not scenario_ids:
        return None

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
