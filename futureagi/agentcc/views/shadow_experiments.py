import structlog
from django.db.models import Avg, Count, Sum
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from agentcc.models import AgentccOrgConfig
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

_GATEWAY_SYNC_WARNING = (
    "Status updated but gateway sync failed. "
    "Mirror config will sync on next gateway restart."
)


class AgentccShadowExperimentViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    """CRUD for shadow experiments with pause/resume/complete lifecycle actions.

    Writes (create, update, partial update, destroy, pause, resume, complete)
    are an org Owner's or Admin's; list, retrieve and stats are open to every
    member of the org."""

    permission_classes = [IsAuthenticated]
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
        # Entitlement check: can this org create more shadow experiments?
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")

        from agentcc.models.shadow_experiment import AgentccShadowExperiment
        from tfc.ee_gating import EEResource, check_ee_can_create

        current_count = AgentccShadowExperiment.objects.filter(
            organization=org, deleted=False
        ).count()
        check_ee_can_create(
            EEResource.SHADOW_EXPERIMENTS,
            org_id=str(org.id),
            current_count=current_count,
        )

        try:
            serializer = AgentccShadowExperimentSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            experiment = serializer.save(
                created_by=request.user,
                organization=org,
            )
            return self._gm.success_response(
                AgentccShadowExperimentSerializer(experiment).data
            )
        except Exception as e:
            logger.exception("shadow_experiment_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccShadowExperimentSerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
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
        """Pause an active experiment."""
        try:
            instance = self.get_object()
            if instance.status != AgentccShadowExperiment.STATUS_ACTIVE:
                return self._gm.bad_request("Only active experiments can be paused")
            instance.status = AgentccShadowExperiment.STATUS_PAUSED
            instance.save(update_fields=["status", "updated_at"])

            synced = self._push_org_config(instance.organization)
            data = AgentccShadowExperimentSerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("shadow_experiment_pause_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["patch"])
    def resume(self, request, pk=None):
        """Resume a paused experiment."""
        try:
            instance = self.get_object()
            if instance.status != AgentccShadowExperiment.STATUS_PAUSED:
                return self._gm.bad_request("Only paused experiments can be resumed")
            instance.status = AgentccShadowExperiment.STATUS_ACTIVE
            instance.save(update_fields=["status", "updated_at"])

            synced = self._push_org_config(instance.organization)
            data = AgentccShadowExperimentSerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("shadow_experiment_resume_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["patch"])
    def complete(self, request, pk=None):
        """Complete an experiment (no more results will be collected)."""
        try:
            instance = self.get_object()
            if instance.status == AgentccShadowExperiment.STATUS_COMPLETED:
                return self._gm.bad_request("Experiment is already completed")
            instance.status = AgentccShadowExperiment.STATUS_COMPLETED
            instance.save(update_fields=["status", "updated_at"])

            synced = self._push_org_config(instance.organization)
            data = AgentccShadowExperimentSerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("shadow_experiment_complete_error", error=str(e))
            return self._gm.bad_request(str(e))

    def _push_org_config(self, org):
        """Push org config to gateway so mirror rules pick up experiment status."""
        try:
            config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            if not config:
                return True  # No config to push
            from agentcc.services.config_push import push_org_config

            return push_org_config(str(org.id), config)
        except Exception as e:
            logger.warning(
                "shadow_experiment_config_push_failed",
                org_id=str(org.id),
                error=str(e),
            )
            return False

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
