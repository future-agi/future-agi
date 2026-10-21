from rest_framework.decorators import action

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tracer.models.eval_task import EvalTask
from tracer.models.project import Project
from tracer.serializers.eval_sampling import (
    SamplingPreviewRequestSerializer,
    SamplingPreviewResponseSerializer,
)
from tracer.services.eval_tasks.sampling.policy import InvalidSamplingPolicy
from tracer.services.eval_tasks.sampling.service import (
    SamplingPreviewTooLarge,
    preview_policy,
)
from tracer.services.filter_principal_context import (
    bind_request_my_annotations_principal,
)


class SamplingPreviewMixin:
    def perform_update(self, serializer):
        # Generic PATCH/PUT is also a public edit path. A policy scope change
        # must not inherit the arrival watermark of a different population.
        current = serializer.instance.sampling_policy
        incoming = serializer.validated_data
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
        filters = bind_request_my_annotations_principal(
            request, data.get("filters") or {}
        )
        filters["project_id"] = str(project.id)
        task = EvalTask(
            project=project,
            row_type=data["row_type"],
            filters=filters,
            sampling_policy=data["sampling_policy"],
        )
        try:
            result = preview_policy(task, evaluator_count=len(eval_ids))
        except (InvalidSamplingPolicy, SamplingPreviewTooLarge) as exc:
            return self._gm.bad_request(str(exc))
        except Exception:
            import logging

            logging.getLogger(__name__).exception("sampling.preview_failed")
            from rest_framework.response import Response

            return Response(
                {
                    "status": False,
                    "error": "Sampling preview exceeded its read budget or is unavailable. Narrow the window and retry.",
                },
                status=503,
            )
        return self._gm.success_response(result)
