"""Which scenario ids an execute request may name (T6 #7e).

An execute request may run only scenarios that belong to the caller and that the
run test being executed includes. The execute serializers check this before any
execution is created, so whatever a ``TestExecution`` stores, and every runner
later reads back, is the run test's own. Every other id gets one answer: another
organization's scenario, one of the caller's scenarios the run test does not
include, a deleted scenario and an id that names nothing are refused with the
same 400 and a fixed body that names no id, so the answer says nothing about
which ids exist elsewhere.
"""

from rest_framework.exceptions import ErrorDetail

from simulate.models import Scenarios

SCENARIOS_NOT_IN_RUN_TEST = (
    "One or more selected scenarios are not part of this run test."
)


def scenarios_not_in_run_test_errors() -> dict:
    """The validation errors a refused selection answers with.

    Exactly what the serializers raise for ``scenario_ids``, so a view that
    re-checks a selection answers with the same body.
    """
    return {"scenario_ids": [ErrorDetail(SCENARIOS_NOT_IN_RUN_TEST, code="invalid")]}


def all_attached_to_run_test(scenario_ids, *, organization, run_test_id) -> bool:
    """Whether every id names a live scenario of ``organization`` in the run test.

    ``Scenarios.objects`` also limits the lookup to the workspace the request was
    authenticated into, as every scenario lookup does. The explicit organization
    filters are what hold when no workspace is bound: the scenario and the run
    test it is attached to must both belong to the caller's organization.
    """
    requested = {str(scenario_id) for scenario_id in scenario_ids}
    if not requested:
        return True
    attached = {
        str(scenario_id)
        for scenario_id in Scenarios.objects.filter(
            id__in=requested,
            organization=organization,
            deleted=False,
            run_tests__id=run_test_id,
            run_tests__organization=organization,
            run_tests__deleted=False,
        ).values_list("id", flat=True)
    }
    return requested <= attached
