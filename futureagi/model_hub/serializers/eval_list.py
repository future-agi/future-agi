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
    tags = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    created_by = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    names = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )


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
