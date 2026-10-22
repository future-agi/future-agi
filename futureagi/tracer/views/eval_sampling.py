from rest_framework.decorators import action

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tracer.models.eval_task import EvalTask
from tracer.models.project import Project
from tracer.serializers.eval_sampling import (
    SamplingPreviewRequestSerializer,
    SamplingPreviewResponseSerializer,
)
from tracer.services.eval_tasks.sampling.policy import (
    InvalidSamplingPolicy,
    policy_task_filters,
    population_changed,
)
from tracer.services.eval_tasks.sampling.preview_guard import (
    PreviewGuardUnavailable,
    PreviewThrottled,
    preview_slot,
)
from tracer.services.eval_tasks.sampling.service import (
    SamplingPreviewTooLarge,
    preview_policy,
)
from tracer.services.filter_principal_context import (
    FilterPrincipalContextError,
    bind_request_my_annotations_principal,
)

_UNAVAILABLE = (
    "Sampling preview exceeded its read budget or is unavailable. "
    "Narrow the window and retry."
)


def _dependency_unavailable(exc: Exception) -> bool:
    """Only genuine read pressure or an unreachable dependency is a 503."""

    from tracer.selectors.eval_tasks.row_resolver import EvalTaskReadBudgetExceeded

    if isinstance(exc, EvalTaskReadBudgetExceeded | OSError | RuntimeError):
        return True
    try:
        from clickhouse_driver.errors import Error as ClickHouseDriverError
    except ImportError:  # pragma: no cover - the driver is a runtime dependency
        return False
    return isinstance(exc, ClickHouseDriverError)


def _error(message: str, status_code: int, **headers):
    from rest_framework.response import Response

    return Response(
        build_error_envelope(message, status_code=status_code),
        status=status_code,
        headers=headers or None,
    )


class SamplingPreviewMixin:
    def perform_update(self, serializer):
        # Generic PATCH/PUT is also a public edit path. A policy scope change
        # must not inherit the arrival watermark of a different population.
        current = serializer.instance.sampling_policy
        incoming = serializer.validated_data
        if population_changed(serializer.instance, incoming):
            from rest_framework.exceptions import ValidationError

            from tracer.models.observation_span import EvalLogger

            if EvalLogger.objects.filter(
                eval_task_id=str(serializer.instance.id)
            ).exists():
                raise ValidationError(
                    {
                        "sampling_policy": "Sampling population changes require Delete & rerun through update_eval_task, or duplicate the task to preserve prior results."
                    }
                )
        if (current is not None or incoming.get("sampling_policy") is not None) and any(
            field in incoming
            for field in ("sampling_policy", "filters", "project", "row_type")
        ):
            serializer.save(continuous_cursor=None)
            return
        super().perform_update(serializer)

    @action(detail=False, methods=["post"], url_path="sampling_preview")
    @validated_request(
        request_serializer=SamplingPreviewRequestSerializer,
        responses={
            200: SamplingPreviewResponseSerializer,
            400: ApiErrorResponseSerializer,
            429: ApiErrorResponseSerializer,
            500: ApiErrorResponseSerializer,
            503: ApiErrorResponseSerializer,
        },
    )
    def sampling_preview(self, request, *args, **kwargs):
        serializer = SamplingPreviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        project = (
            self._scope_project_queryset(Project.objects.all())
            .filter(id=data["project"])
            .first()
        )
        if project is None:
            return self._gm.bad_request("Project not found")
        eval_ids = list(dict.fromkeys(data["evals"]))
        if self._invalid_eval_ids_for_project(eval_ids, project.id):
            return self._gm.bad_request(
                "Evaluators must belong to the selected project."
            )
        try:
            filters = bind_request_my_annotations_principal(
                request, data.get("filters") or {}
            )
            # The policy window is the population's only event-time range.
            filters = policy_task_filters(filters)
        except (FilterPrincipalContextError, InvalidSamplingPolicy) as exc:
            return self._gm.bad_request(str(exc))
        filters["project_id"] = str(project.id)
        task = EvalTask(
            project=project,
            row_type=data["row_type"],
            run_type="continuous"
            if data["sampling_policy"]["mode"] == "live"
            else "historical",
            filters=filters,
            sampling_policy=data["sampling_policy"],
        )
        from tracer.selectors.eval_tasks.row_resolver import (
            EvalTaskSelectionRejected,
        )

        actor = f"{project.organization_id}:{getattr(request.user, 'pk', '')}"
        try:
            with preview_slot(actor):
                result = preview_policy(task, evaluator_count=len(eval_ids))
        except PreviewThrottled as exc:
            return _error(str(exc), 429, **{"Retry-After": str(exc.retry_after)})
        except PreviewGuardUnavailable:
            return _error(_UNAVAILABLE, 503)
        except (
            InvalidSamplingPolicy,
            SamplingPreviewTooLarge,
            EvalTaskSelectionRejected,
        ) as exc:
            # Validation, unsupported filters, oversized identities and read
            # ceilings are deterministic for this request: fix the request.
            return self._gm.bad_request(str(exc))
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("sampling.preview_failed")
            if _dependency_unavailable(exc):
                return _error(_UNAVAILABLE, 503)
            return _error("Sampling preview failed unexpectedly.", 500)
        return self._gm.success_response(result)
