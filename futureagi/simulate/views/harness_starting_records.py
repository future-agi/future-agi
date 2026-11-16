"""The scenario editor's starting records, as an action on the environments viewset."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response

from simulate.models import HostedHarnessJob
from simulate.serializers.harness_starting_records import (
    HarnessStartingRecordsSerializer,
)
from simulate.services import harness_starting_records
from tfc.utils.api_contracts import validated_request


class EnvironmentViewSetHost(Protocol):
    """What the mixin needs from the viewset it joins: the environment scoped to the request, and its 404."""

    def _job(self, request: Request, pk: str | None) -> HostedHarnessJob | None: ...

    def _not_found(self) -> Response: ...


# Typed against the host while checking; at run time the mixin adds nothing that could shadow the viewset's own.
if TYPE_CHECKING:
    _Host = EnvironmentViewSetHost
else:
    _Host = object


class HarnessStartingRecordsMixin(_Host):
    """Read a scenario's starting records, scoped by the host viewset's `_job`."""

    @validated_request(
        responses={200: HarnessStartingRecordsSerializer},
        operation_id="simulate_api_harness-environments_starting_records",
    )
    @action(
        detail=True,
        methods=["get"],
        url_path=r"scenarios/(?P<scenario_key>[^/]{1,255})/starting-records",
    )
    def starting_records(
        self, request: Request, pk: str | None = None, scenario_key: str = ""
    ) -> Response:
        """The seeded rows this scenario's facts name, each looked up by its key: held, or missing from the seed."""
        job = self._job(request, pk)
        if job is None:
            return self._not_found()
        return Response(harness_starting_records.starting_records(job, scenario_key))
