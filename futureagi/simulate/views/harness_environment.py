from __future__ import annotations

import math

from django.db import transaction
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from simulate.models import HostedHarnessJob, HostedHarnessScenario
from simulate.serializers.harness_environment import (
    HarnessEnvironmentDetailSerializer,
    HarnessEnvironmentListResponseSerializer,
    HarnessEnvironmentRenameSerializer,
    HarnessRunCreateResponseSerializer,
    HarnessRunCreateSerializer,
)
from simulate.services.harness_provider import (
    _organization,
    _scope_jobs,
    get_harness_provider,
)
from tfc.utils.api_contracts import validated_request


class HarnessEnvironmentPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "limit"
    max_page_size = 100

    def get_paginated_response(self, data):
        count = self.page.paginator.count
        return Response(
            {
                "count": count,
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "total_pages": math.ceil(count / self.get_page_size(self.request))
                if count
                else 0,
                "current_page": self.page.number,
                "results": data,
            }
        )


def _outputs(job: HostedHarnessJob) -> dict[str, object]:
    rows = list(job.normalized_stage_outputs.order_by("created_at"))
    if rows:
        return {row.kind: row.data for row in rows}
    return {
        row.get("kind"): row.get("data")
        for row in (job.stage_outputs or [])
        if isinstance(row, dict) and row.get("kind")
    }


def _environment_name(job: HostedHarnessJob) -> str:
    metadata = (job.payload or {}).get("metadata") or {}
    return str(
        metadata.get("name")
        or metadata.get("agent_name")
        or (job.run_test.name if job.run_test_id else "")
        or f"Environment {str(job.id)[:8]}"
    )


def _environment_status(job: HostedHarnessJob) -> str:
    if job.state == HostedHarnessJob.State.COMPLETED:
        return "ready"
    if job.state in {HostedHarnessJob.State.FAILED, HostedHarnessJob.State.CANCELED}:
        return "failed"
    return "building"


def _agent_type(job: HostedHarnessJob, contract: dict | None = None) -> str:
    modality = str((contract or {}).get("modality") or "").lower()
    connector = str(((job.payload or {}).get("agent") or {}).get("connector") or "")
    return (
        "voice"
        if modality == "voice" or connector in {"livekit", "vapi", "retell"}
        else "chat"
    )


def _list_row(job: HostedHarnessJob) -> dict:
    outputs = _outputs(job)
    contract = (
        outputs.get("contract") if isinstance(outputs.get("contract"), dict) else {}
    )
    return {
        "id": str(job.id),
        "name": _environment_name(job),
        "description": contract.get("one_liner"),
        "domain": contract.get("domain"),
        "status": _environment_status(job),
        "agent_type": _agent_type(job, contract),
        "tools_count": len(contract.get("tools") or []),
        "scenario_count": job.scenario_registrations.filter(deleted=False).count(),
        "sub_goals_count": len(contract.get("sub_goals") or []),
        "runs_count": job.simulation_runs.filter(deleted=False).count(),
        "created_at": job.created_at,
        "last_updated": job.updated_at,
    }


def _detail(job: HostedHarnessJob) -> dict:
    outputs = _outputs(job)
    contract = (
        outputs.get("contract") if isinstance(outputs.get("contract"), dict) else None
    )
    world = (
        outputs.get("environment")
        if isinstance(outputs.get("environment"), dict)
        else None
    )
    authored = (
        outputs.get("scenarios") if isinstance(outputs.get("scenarios"), list) else []
    )
    authored_by_key = {
        str(item.get("scenario_key")): item
        for item in authored
        if isinstance(item, dict) and item.get("scenario_key")
    }
    registrations = list(
        HostedHarnessScenario.no_workspace_objects.filter(job=job)
        .select_related("scenario", "dataset_row")
        .order_by("created_at", "id")
    )
    scenarios = []
    for registration in registrations:
        item = dict(authored_by_key.get(registration.scenario_key) or {})
        item.update(
            {
                "scenario_key": registration.scenario_key,
                "scenario_id": str(registration.scenario_id),
                "name": item.get("name") or getattr(registration.scenario, "name", ""),
                "status": "registered",
                "call_execution_id": None,
            }
        )
        scenarios.append(item)

    evals = []
    if job.run_test_id:
        evals = [
            {
                "id": str(config.id),
                "name": config.name or config.eval_template.name,
                "description": getattr(config.eval_template, "description", "") or "",
                "runnable": True,
            }
            for config in job.run_test.simulate_eval_configs.filter(
                deleted=False
            ).select_related("eval_template")
        ]
    latest_run = (
        job.simulation_runs.filter(deleted=False).order_by("-created_at").first()
    )
    overview = {
        "id": str(job.id),
        "name": _environment_name(job),
        "description": (contract or {}).get("one_liner"),
        "domain": (contract or {}).get("domain"),
        "status": job.current_stage,
        "agent_type": _agent_type(job, contract),
        "scenario_count": len(scenarios),
        "tools_count": len((contract or {}).get("tools") or []),
        "sub_goals_count": len((contract or {}).get("sub_goals") or []),
        "runs_count": job.simulation_runs.filter(deleted=False).count(),
        "evaluations_count": len(evals),
        "created_at": job.created_at.isoformat(),
        "last_updated": job.updated_at.isoformat(),
        "run": {
            "run_test_id": str(job.run_test_id) if job.run_test_id else None,
            "test_execution_id": (
                str(latest_run.test_execution_id)
                if latest_run and latest_run.test_execution_id
                else None
            ),
            "simulation_url": None,
        },
    }
    return {
        "id": str(job.id),
        "overview": overview,
        "contract": contract,
        "world": world,
        "scenarios": scenarios,
        "evaluations": {"selected": evals},
        "settings": {
            "source": (job.payload or {}).get("source") or {},
            "agent": (job.payload or {}).get("agent") or {},
            "runtime": (job.payload or {}).get("runtime") or {},
        },
    }


class HarnessEnvironmentViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    pagination_class = HarnessEnvironmentPagination

    def _queryset(self, request):
        queryset = HostedHarnessJob.no_workspace_objects.filter(
            organization=_organization(request),
            environment__isnull=True,
            deleted=False,
        ).select_related("run_test")
        return _scope_jobs(queryset, request)

    def _environment(self, request, pk):
        return self._queryset(request).filter(id=pk).first()

    @swagger_auto_schema(responses={200: HarnessEnvironmentListResponseSerializer})
    def list(self, request):
        paginator = self.pagination_class()
        queryset = self._queryset(request).order_by("-created_at")
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response([_list_row(job) for job in page])

    @swagger_auto_schema(responses={200: HarnessEnvironmentDetailSerializer})
    def retrieve(self, request, pk=None):
        job = self._environment(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(_detail(job))

    @validated_request(
        request_serializer=HarnessEnvironmentRenameSerializer,
        responses={200: HarnessEnvironmentDetailSerializer},
        reject_unknown_fields=True,
    )
    def partial_update(self, request, pk=None):
        with transaction.atomic():
            job = self._environment(request, pk)
            if job is None:
                return Response(
                    {"detail": "Environment not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            payload = dict(job.payload or {})
            metadata = dict(payload.get("metadata") or {})
            metadata["name"] = request.validated_data["name"]
            payload["metadata"] = metadata
            job.payload = payload
            job.save(update_fields=["payload", "updated_at"])
            if job.run_test_id:
                job.run_test.name = request.validated_data["name"]
                job.run_test.save(update_fields=["name", "updated_at"])
        return Response(_detail(job))

    def destroy(self, request, pk=None):
        job = self._environment(request, pk)
        if job is None:
            return Response(
                {"detail": "Environment not found"}, status=status.HTTP_404_NOT_FOUND
            )
        terminal = {
            HostedHarnessJob.State.COMPLETED,
            HostedHarnessJob.State.FAILED,
            HostedHarnessJob.State.CANCELED,
        }
        if (
            job.state not in terminal
            or job.simulation_runs.exclude(state__in=terminal).exists()
        ):
            return Response(
                {
                    "detail": "Cancel active authoring and Runs before deleting the environment"
                },
                status=status.HTTP_409_CONFLICT,
            )
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @validated_request(
        request_serializer=HarnessRunCreateSerializer,
        responses={202: HarnessRunCreateResponseSerializer},
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def run(self, request, pk=None):
        return get_harness_provider().run(request, pk)
