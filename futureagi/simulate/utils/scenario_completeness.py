"""
Gate that prevents test execution when any selected scenario is incomplete
(still being generated, or generation failed). Used by all three execute
endpoints — chat, voice / agent_definition, and prompt simulation — so direct
API/SDK callers get the same 400 the UI button now blocks.
"""

from rest_framework import status
from rest_framework.response import Response

from model_hub.models.choices import StatusType
from simulate.models.scenarios import Scenarios


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

    return Response(
        {
            "error": "Scenarios incomplete",
            "detail": (
                f"{len(incomplete)} scenario(s) are still being generated or "
                "failed generation. Wait for them to complete before running "
                "a simulation."
            ),
            "scenarios": [
                {"id": str(s["id"]), "name": s["name"], "status": s["status"]}
                for s in incomplete
            ],
        },
        status=status.HTTP_400_BAD_REQUEST,
    )
