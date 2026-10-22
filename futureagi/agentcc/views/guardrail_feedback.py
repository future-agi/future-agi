from collections import defaultdict

import structlog
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.guardrail_feedback import AgentccGuardrailFeedback
from agentcc.serializers.guardrail_feedback import AgentccGuardrailFeedbackSerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# What an Edit may change. Which request and which check an answer is about
# stay as written, so every answer keeps the org check create applies.
_EDITABLE_FIELDS = ("feedback", "comment")
_FIXED_FIELDS = ("request_log", "check_name")

_TRUE = ("1", "true", "yes")


def _latest_answers(queryset):
    """Each reviewer's latest answer per (request log, check), as
    (check_name, feedback) rows.

    One reviewer's answer can be stored more than once; only the latest
    counts. Answers whose reviewer was
    deleted (created_by is SET_NULL) cannot be told apart, so each counts.
    """
    reviewed = (
        queryset.filter(created_by__isnull=False)
        .order_by("request_log_id", "check_name", "created_by_id", "-updated_at")
        .distinct("request_log_id", "check_name", "created_by_id")
        .values("check_name", "feedback")
    )
    orphaned = queryset.filter(created_by__isnull=True).values("check_name", "feedback")
    return list(reviewed) + list(orphaned)


class AgentccGuardrailFeedbackViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """Feedback on guardrail decisions. Org-scoped."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccGuardrailFeedbackSerializer
    queryset = AgentccGuardrailFeedback.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        qs = super().get_queryset()
        # Optional filters
        check_name = self.request.query_params.get("check_name")
        if check_name:
            qs = qs.filter(check_name=check_name)
        feedback_type = self.request.query_params.get("feedback")
        if feedback_type:
            qs = qs.filter(feedback=feedback_type)
        request_log_id = self.request.query_params.get("request_log_id")
        if request_log_id:
            qs = qs.filter(request_log_id=request_log_id)
        # The request drawer loads the signed-in reviewer's own answer.
        mine = (self.request.query_params.get("mine") or "").strip().lower()
        if mine in _TRUE:
            qs = qs.filter(created_by=self.request.user)
        return qs

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccGuardrailFeedbackSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("guardrail_feedback_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccGuardrailFeedbackSerializer(instance).data
            )
        except Exception as e:
            logger.exception("guardrail_feedback_retrieve_error", error=str(e))
            return self._gm.not_found("Feedback not found")

    def create(self, request, *args, **kwargs):
        try:
            serializer = AgentccGuardrailFeedbackSerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")

            # Validate request_log belongs to same org
            request_log = serializer.validated_data.get("request_log")
            if request_log and request_log.organization_id != org.id:
                return self._gm.bad_request(
                    "Request log does not belong to your organization"
                )

            # One answer per (request log, check, reviewer): a second answer
            # from the same reviewer replaces the first.
            existing = (
                super()
                .get_queryset()
                .filter(
                    request_log=request_log,
                    check_name=serializer.validated_data.get("check_name"),
                    created_by=request.user,
                )
                .order_by("-updated_at")
                .first()
            )
            if existing is not None:
                feedback = self._save_answer(existing, serializer.validated_data)
            else:
                feedback = serializer.save(
                    organization=org,
                    created_by=request.user,
                )
            return self._gm.success_response(
                AgentccGuardrailFeedbackSerializer(feedback).data
            )
        except Exception as e:
            logger.exception("guardrail_feedback_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    @staticmethod
    def _save_answer(instance, data):
        for field in _EDITABLE_FIELDS:
            if field in data:
                setattr(instance, field, data[field])
        instance.save(update_fields=[*_EDITABLE_FIELDS, "updated_at"])
        return instance

    def update(self, request, *args, **kwargs):
        """Change a saved answer: its feedback and comment only, and only by
        the reviewer who gave it."""
        instance = self.get_object()
        try:
            if instance.created_by_id is None or (
                instance.created_by_id != request.user.id
            ):
                return self._gm.custom_error_response(
                    403,
                    "Only the reviewer who gave this answer can change it.",
                    code="feedback_not_yours",
                )
            data = request.data if isinstance(request.data, dict) else {}
            current = {
                "request_log": str(instance.request_log_id),
                "check_name": instance.check_name,
            }
            moved = [
                field
                for field in _FIXED_FIELDS
                if field in data and str(data[field]) != current[field]
            ]
            if moved:
                return self._gm.custom_error_response(
                    400,
                    "An answer stays on its request and check; only feedback "
                    f"and comment can change (sent: {', '.join(moved)}).",
                    code="feedback_target_fixed",
                )
            serializer = AgentccGuardrailFeedbackSerializer(
                instance,
                data={f: data[f] for f in _EDITABLE_FIELDS if f in data},
                partial=True,
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            feedback = self._save_answer(instance, serializer.validated_data)
            return self._gm.success_response(
                AgentccGuardrailFeedbackSerializer(feedback).data
            )
        except Exception as e:
            logger.exception("guardrail_feedback_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.deleted = True
            instance.save(update_fields=["deleted", "updated_at"])
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("guardrail_feedback_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Aggregate feedback stats per check_name: one answer per
        (request log, check, reviewer), the reviewer's latest."""
        try:
            qs = self.get_queryset()
            feedback_data = _latest_answers(qs)

            stats = defaultdict(
                lambda: {
                    "total": 0,
                    "correct": 0,
                    "false_positive": 0,
                    "false_negative": 0,
                    "unsure": 0,
                }
            )

            for row in feedback_data:
                name = row["check_name"]
                fb = row["feedback"]
                stats[name]["total"] += 1
                if fb in stats[name]:
                    stats[name][fb] += 1

            result = []
            for name, data in sorted(stats.items()):
                total = data["total"]
                result.append(
                    {
                        "check_name": name,
                        "total_feedback": total,
                        "correct_count": data["correct"],
                        "correct_rate": (
                            round(data["correct"] / total * 100, 2)
                            if total > 0
                            else 0.0
                        ),
                        "false_positive_count": data["false_positive"],
                        "false_positive_rate": (
                            round(data["false_positive"] / total * 100, 2)
                            if total > 0
                            else 0.0
                        ),
                        "false_negative_count": data["false_negative"],
                        "false_negative_rate": (
                            round(data["false_negative"] / total * 100, 2)
                            if total > 0
                            else 0.0
                        ),
                        "unsure_count": data["unsure"],
                    }
                )

            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("guardrail_feedback_summary_error", error=str(e))
            return self._gm.bad_request(str(e))
