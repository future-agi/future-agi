"""
DRF serializers for eval list API.

Replaces Pydantic models (EvalListRequest, EvalListFilters) so validation
errors are handled natively by DRF's exception handler — no per-view
try/catch needed.
"""

from rest_framework import serializers


class EvalListFiltersSerializer(serializers.Serializer):
    eval_type = serializers.ListField(
        child=serializers.ChoiceField(choices=["llm", "code", "agent"]),
        required=False,
        allow_empty=True,
    )
    eval_type_not = serializers.ListField(
        child=serializers.ChoiceField(choices=["llm", "code", "agent"]),
        required=False,
        allow_empty=True,
    )
    output_type = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["pass_fail", "percentage", "deterministic"]
        ),
        required=False,
        allow_empty=True,
    )
    output_type_not = serializers.ListField(
        child=serializers.ChoiceField(
            choices=["pass_fail", "percentage", "deterministic"]
        ),
        required=False,
        allow_empty=True,
    )
    template_type = serializers.ListField(
        child=serializers.ChoiceField(choices=["single", "composite"]),
        required=False,
        allow_empty=True,
    )
    template_type_not = serializers.ListField(
        child=serializers.ChoiceField(choices=["single", "composite"]),
        required=False,
        allow_empty=True,
    )
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    tags_not = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    created_by = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    created_by_not = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    names = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    names_not = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )


class RunConfigSerializer(serializers.Serializer):
    """Canonical `run_config` sub-shape used by every eval binding surface."""

    agent_mode = serializers.CharField(default="agent")
    check_internet = serializers.BooleanField(default=False)
    summary = serializers.CharField(default="concise")
    pass_threshold = serializers.FloatField(default=0.5)
    error_localizer_enabled = serializers.BooleanField(default=False)
    data_injection = serializers.JSONField(default=dict)
    knowledge_bases = serializers.JSONField(default=list)
    tools = serializers.JSONField(default=dict)

    def to_representation(self, instance):
        binding_config = instance or {}
        run_config = binding_config.get("run_config") or {}
        summary = run_config.get("summary", "concise")
        if isinstance(summary, dict):
            summary = summary.get("type", "concise")
        return {
            "agent_mode": run_config.get("agent_mode", "agent"),
            "check_internet": run_config.get("check_internet", False),
            "summary": summary,
            "pass_threshold": run_config.get("pass_threshold", 0.5),
            "error_localizer_enabled": self.context.get(
                "error_localizer_enabled", False
            ),
            "data_injection": run_config.get("data_injection", {}),
            "knowledge_bases": run_config.get("knowledge_bases", []),
            "tools": run_config.get("tools", {}),
        }


class EvalListRequestSerializer(serializers.Serializer):
    page = serializers.IntegerField(default=0, min_value=0)
    page_size = serializers.IntegerField(default=25, min_value=1, max_value=100)
    search = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, default=None
    )
    owner_filter = serializers.ChoiceField(
        choices=["all", "user", "system"], default="all"
    )
    filters = EvalListFiltersSerializer(required=False, allow_null=True, default=None)
    sort_by = serializers.ChoiceField(
        choices=["name", "updated_at", "created_at"], default="updated_at"
    )
    sort_order = serializers.ChoiceField(choices=["asc", "desc"], default="desc")
