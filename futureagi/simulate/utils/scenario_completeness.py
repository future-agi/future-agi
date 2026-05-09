"""
Gate that prevents test execution when any selected scenario is incomplete
(still being generated, or generation failed). Used by all three execute
endpoints — chat, voice / agent_definition, and prompt simulation — so direct
API/SDK callers get the same 400 the UI button now blocks.
"""

from model_hub.models.choices import StatusType
from simulate.models.scenarios import Scenarios
from tfc.utils.general_methods import GeneralMethods


def check_scenarios_incomplete(scenario_ids):
    """
    Return None if every scenario in `scenario_ids` has status == "Completed".
    Otherwise return a 400 Response listing the incomplete scenarios.

    `scenario_ids` may be a list of UUIDs or strings; empty/None is treated as
    complete (the caller is responsible for the "at least one scenario" check).
    """
    if not scenario_ids:
        return None

    incomplete = list(
        Scenarios.objects.filter(id__in=scenario_ids, deleted=False)
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
