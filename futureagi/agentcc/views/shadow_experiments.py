import structlog
from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_501_NOT_IMPLEMENTED
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from agentcc.models.shadow_experiment import AgentccShadowExperiment
from agentcc.models.shadow_result import AgentccShadowResult
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.shadow import (
    AgentccShadowExperimentSerializer,
    AgentccShadowResultSerializer,
)
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# docs-agentcc-711, item 3: no shadow experiment reaches the gateway. The org
# config push never reads experiments, and the gateway mirrors traffic only
# from its own config file's routing.mirror, for every org at once. An
# experiment created here mirrored nothing and collected no results, and
# pause, resume and complete answered "gateway_synced" for a push that
# carried nothing about it (or a failed one with "Mirror config will sync on
# next gateway restart", which no restart did). Create and the lifecycle
# actions now answer 501; reads, edits and deletes of stored experiments stay.
SHADOW_EXPERIMENTS_NOT_ACTIVE = "shadow_experiments_not_active"
SHADOW_EXPERIMENTS_NOT_ACTIVE_MESSAGE = (
    "Shadow experiments are not active on this gateway: no experiment "
    "mirrors traffic or collects results, so none can be created, paused, "
    "resumed or completed."
)


class AgentccShadowExperimentViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    """Shadow experiments: reads, edits and deletes of stored experiments.

    Not active: no experiment mirrors traffic or collects results, so create,
    pause, resume and complete answer 501 ``shadow_experiments_not_active``.
    """

    permission_classes = [IsAuthenticated]
    # Every write is an org Owner's or Admin's, as every other gateway config
    # write is (release round 6B, fix (g), as F9 did for guardrail policies);
    # past that gate, create, pause, resume and complete answer 501. Every
    # member keeps read access (list, retrieve, stats).
    gateway_admin_actions = frozenset(
        {
            "create",
            "update",
            "partial_update",
            "destroy",
            "pause",
            "resume",
            "complete",
        }
    )
    serializer_class = AgentccShadowExperimentSerializer
    queryset = AgentccShadowExperiment.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        qs = super().get_queryset().filter(deleted=False)
        status = self.request.query_params.get("status")
        if status:
            qs = qs.filter(status=status)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccShadowExperimentSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("shadow_experiment_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccShadowExperimentSerializer(instance).data
            )
        except Exception as e:
            logger.exception("shadow_experiment_retrieve_error", error=str(e))
            return self._gm.not_found("Experiment not found")

    def create(self, request, *args, **kwargs):
        """Not active: answers 501 ``shadow_experiments_not_active``."""
        return self._not_active()

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccShadowExperimentSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            # Ordinary PUT/PATCH must not bypass the lifecycle-action refusal.
            # Metadata saves may omit status or echo its stored value.
            if (
                serializer.validated_data.get("status", instance.status)
                != instance.status
            ):
                return self._not_active()
            experiment = serializer.save()
            return self._gm.success_response(
                AgentccShadowExperimentSerializer(experiment).data
            )
        except Exception as e:
            logger.exception("shadow_experiment_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            # Soft delete experiment and its results.
            instance.deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["deleted", "deleted_at", "updated_at"])
            AgentccShadowResult.no_workspace_objects.filter(experiment=instance).update(
                deleted=True
            )
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("shadow_experiment_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["patch"])
    def pause(self, request, pk=None):
        """Not active: answers 501 ``shadow_experiments_not_active``."""
        return self._not_active()

    @action(detail=True, methods=["patch"])
    def resume(self, request, pk=None):
        """Not active: answers 501 ``shadow_experiments_not_active``."""
        return self._not_active()

    @action(detail=True, methods=["patch"])
    def complete(self, request, pk=None):
        """Not active: answers 501 ``shadow_experiments_not_active``."""
        return self._not_active()

    def _not_active(self):
        return self._gm.custom_error_response(
            HTTP_501_NOT_IMPLEMENTED,
            SHADOW_EXPERIMENTS_NOT_ACTIVE_MESSAGE,
            code=SHADOW_EXPERIMENTS_NOT_ACTIVE,
        )

    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        """Aggregate metrics comparing production vs shadow for this experiment."""
        try:
            instance = self.get_object()
            results = AgentccShadowResult.no_workspace_objects.filter(
                experiment=instance, deleted=False
            )
            agg = results.aggregate(
                count=Count("id"),
                avg_source_latency=Avg("source_latency_ms"),
                avg_shadow_latency=Avg("shadow_latency_ms"),
                avg_source_tokens=Avg("source_tokens"),
                avg_shadow_tokens=Avg("shadow_tokens"),
                total_source_tokens=Sum("source_tokens"),
                total_shadow_tokens=Sum("shadow_tokens"),
            )
            error_count = results.exclude(shadow_error="").count()  # noqa: F841
            total = agg["count"] or 0
            source_errors = results.exclude(source_status_code=200).count()
            shadow_errors = results.exclude(shadow_status_code=200).count()

            stats = {
                "total_comparisons": total,
                "avg_source_latency_ms": round(
                    float(agg["avg_source_latency"] or 0), 1
                ),
                "avg_shadow_latency_ms": round(
                    float(agg["avg_shadow_latency"] or 0), 1
                ),
                "avg_source_tokens": round(float(agg["avg_source_tokens"] or 0), 1),
                "avg_shadow_tokens": round(float(agg["avg_shadow_tokens"] or 0), 1),
                "total_source_tokens": agg["total_source_tokens"] or 0,
                "total_shadow_tokens": agg["total_shadow_tokens"] or 0,
                "source_error_count": source_errors,
                "shadow_error_count": shadow_errors,
                "source_error_rate": (
                    round(source_errors / total * 100, 2) if total > 0 else 0
                ),
                "shadow_error_rate": (
                    round(shadow_errors / total * 100, 2) if total > 0 else 0
                ),
            }

            # Calculate deltas.
            if agg["avg_source_latency"] and agg["avg_shadow_latency"]:
                stats["latency_delta_pct"] = round(
                    (
                        float(agg["avg_shadow_latency"])
                        - float(agg["avg_source_latency"])
                    )
                    / float(agg["avg_source_latency"])
                    * 100,
                    1,
                )
            else:
                stats["latency_delta_pct"] = 0

            if agg["avg_source_tokens"] and agg["avg_shadow_tokens"]:
                stats["token_delta_pct"] = round(
                    (float(agg["avg_shadow_tokens"]) - float(agg["avg_source_tokens"]))
                    / float(agg["avg_source_tokens"])
                    * 100,
                    1,
                )
            else:
                stats["token_delta_pct"] = 0

            return self._gm.success_response(stats)
        except Exception as e:
            logger.exception("shadow_experiment_stats_error", error=str(e))
            return self._gm.bad_request(str(e))


class AgentccShadowResultViewSet(
    BaseModelViewSetMixinWithUserOrg, ReadOnlyModelViewSet
):
    """Read-only viewset for shadow results. Paginated."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccShadowResultSerializer
    queryset = AgentccShadowResult.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        qs = super().get_queryset().filter(deleted=False)
        experiment_id = self.request.query_params.get("experiment")
        if experiment_id:
            qs = qs.filter(experiment_id=experiment_id)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = AgentccShadowResultSerializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = AgentccShadowResultSerializer(queryset[:100], many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("shadow_result_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccShadowResultSerializer(instance).data
            )
        except Exception as e:
            logger.exception("shadow_result_retrieve_error", error=str(e))
            return self._gm.not_found("Shadow result not found")
