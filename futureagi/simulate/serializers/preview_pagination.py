from django.conf import settings
from rest_framework import serializers

SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE = settings.SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE
SIMULATION_PREVIEW_MAX_PAGE_SIZE = settings.SIMULATION_PREVIEW_MAX_PAGE_SIZE


class SimulationPreviewCursorQuerySerializer(serializers.Serializer):
    cursor = serializers.CharField(
        required=False,
        allow_blank=False,
        max_length=4096,
    )
    page_size = serializers.IntegerField(
        required=False,
        default=SIMULATION_PREVIEW_DEFAULT_PAGE_SIZE,
        min_value=1,
        max_value=SIMULATION_PREVIEW_MAX_PAGE_SIZE,
    )


class SimulationCallPreviewCursorQuerySerializer(
    SimulationPreviewCursorQuerySerializer
):
    run_test_id = serializers.UUIDField(
        help_text=(
            "Run-test scope selected by the preview. The execution and every "
            "signed continuation must belong to this run test."
        )
    )


class SimulationPreviewItemSerializer(serializers.Serializer):
    # These serializers validate compact response payloads as ``data=...``.
    # read_only fields are skipped by DRF input validation, which previously
    # made both runtime response checks and the generated required set vacuous.
    id = serializers.UUIDField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()


class SimulationPreviewPageSerializer(serializers.Serializer):
    results = SimulationPreviewItemSerializer(many=True)
    next_cursor = serializers.CharField(allow_null=True, allow_blank=False)
    has_more = serializers.BooleanField()
    snapshot_total = serializers.IntegerField(min_value=0)
    loaded_through = serializers.IntegerField(min_value=0)
    complete = serializers.BooleanField()
    exact = serializers.BooleanField()
    snapshot_at = serializers.DateTimeField()


class SimulationPreviewErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    restart_required = serializers.BooleanField(required=False)
