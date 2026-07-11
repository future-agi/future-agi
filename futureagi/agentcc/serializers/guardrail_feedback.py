from rest_framework import serializers

from agentcc.models.guardrail_feedback import AgentccGuardrailFeedback


class AgentccGuardrailFeedbackSerializer(serializers.ModelSerializer):
    """Human feedback on a single guardrail decision for a logged request —
    marks whether a check was correct, a false positive/negative, or unsure.
    Use it to label guardrail outcomes for tuning. Listed/read via
    list_agentcc_guardrail_feedbacks / get_agentcc_guardrail_feedback."""

    class Meta:
        model = AgentccGuardrailFeedback
        fields = [
            "id",
            "organization",
            "request_log",
            "check_name",
            "feedback",
            "comment",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "created_by",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "request_log": {
                "help_text": "UUID of the gateway request log this feedback is about (from list_agentcc_request_logs)."
            },
            "check_name": {
                "help_text": "Name of the guardrail check the feedback applies to."
            },
            "feedback": {
                "help_text": "Verdict: correct, false_positive, false_negative, or unsure."
            },
            "comment": {"help_text": "Optional free-text note explaining the verdict."},
            "created_by": {
                "help_text": "UUID of the user who submitted the feedback (read-only)."
            },
        }

    def validate_feedback(self, value):
        valid = [c[0] for c in AgentccGuardrailFeedback.FEEDBACK_CHOICES]
        if value not in valid:
            raise serializers.ValidationError(
                f"feedback must be one of: {', '.join(valid)}"
            )
        return value
