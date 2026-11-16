"""The scenario editor's starting records, as actions on the environments viewset."""

from __future__ import annotations

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from simulate.serializers.harness_actors import editor_problems
from simulate.serializers.harness_starting_records import (
    HarnessStartingRecordChoiceSerializer,
    HarnessStartingRecordsSerializer,
)
from simulate.services import harness_starting_records
from tfc.utils.api_contracts import validated_request


def _choice_refused(errors: dict) -> Response:
    """A choice its serializer refuses, answered as the service refuses one: a problem per field."""
    refused = harness_starting_records.record_invalid(
        editor_problems(HarnessStartingRecordChoiceSerializer(), errors)
    )
    return Response(refused.as_dict(), status=status.HTTP_400_BAD_REQUEST)


class HarnessStartingRecordsMixin:
    """Read a scenario's starting records and choose which seeded row it starts from. Needs the viewset's `_job`,
    scoped to the request's organization and workspace, and its `_not_found`."""

    @validated_request(
        responses={200: HarnessStartingRecordsSerializer},
        operation_id="simulate_api_harness-environments_starting_records",
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"scenarios/(?P<scenario_key>[^/]{1,255})/starting-records",
    )
    def starting_records(self, request, pk=None, scenario_key=None):
        """The seeded rows this scenario's facts name, each with the other rows it could start from instead."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(harness_starting_records.starting_records(job, scenario_key))

    @validated_request(
        request_serializer=HarnessStartingRecordChoiceSerializer,
        responses={200: HarnessStartingRecordsSerializer},
        request_methods=["PUT"],
        reject_unknown_fields=True,
        validation_error_response=_choice_refused,
        operation_id="simulate_api_harness-environments_choose_starting_record",
    )
    @starting_records.mapping.put
    def choose_starting_record(self, request, pk=None, scenario_key=None):
        """Start this scenario from another seeded row, in the unsaved changes; Save proves it again."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        data = request.validated_data
        return Response(
            harness_starting_records.choose_starting_record(
                job,
                scenario_key,
                path=data["path"],
                table=data["table"],
                key=data["key"],
                user=request.user,
            )
        )
