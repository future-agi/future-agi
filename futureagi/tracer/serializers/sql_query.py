from rest_framework import serializers

from tracer.models.sql_query import SavedSQLQuery


class SQLProjectSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()


class SQLListSerializer(SQLProjectSerializer):
    offset = serializers.IntegerField(min_value=0, max_value=10000, default=0)


class SQLQueryInputSerializer(SQLProjectSerializer):
    sql = serializers.CharField(max_length=32768, trim_whitespace=False)
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()


class SQLRunSerializer(SQLQueryInputSerializer):
    reuse = serializers.BooleanField(default=True)


class SQLResultQuerySerializer(SQLProjectSerializer):
    offset = serializers.IntegerField(min_value=0, max_value=10000, default=0)
    limit = serializers.IntegerField(
        min_value=1,
        max_value=1000,
        required=False,
        help_text="Optional bounded result page; omitted REST requests retain the full snapshot. MCP defaults to 100. Wide rows are explicitly previewed; download for full cells.",
    )


class SQLRunSavedSerializer(SQLProjectSerializer):
    saved_query_id = serializers.UUIDField()
    start = serializers.DateTimeField(required=False)
    end = serializers.DateTimeField(required=False)


class SQLExportSerializer(SQLProjectSerializer):
    file_format = serializers.ChoiceField(choices=("csv", "parquet"))


class SQLChartSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=("table", "bar"), default="table")
    x = serializers.CharField(max_length=128, required=False)
    y = serializers.CharField(max_length=128, required=False)


class SQLSaveSerializer(SQLProjectSerializer):
    name = serializers.CharField(max_length=160)
    sql = serializers.CharField(max_length=32768, trim_whitespace=False)
    visibility = serializers.ChoiceField(
        choices=("private", "project"), default="private"
    )
    chart = SQLChartSerializer(default=dict)
    window_hours = serializers.IntegerField(min_value=1, max_value=744, default=24)


class SQLSavedSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedSQLQuery
        fields = (
            "id",
            "project_id",
            "name",
            "sql",
            "visibility",
            "chart",
            "window_hours",
            "created_at",
            "created_by_id",
        )
        read_only_fields = fields


class SQLSavedListSerializer(serializers.Serializer):
    items = SQLSavedSerializer(many=True)
    has_more = serializers.BooleanField()


class SQLColumnSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField()


class SQLDatasetSerializer(serializers.Serializer):
    available = serializers.BooleanField()
    unavailable_reason = serializers.CharField(allow_blank=True)
    name = serializers.CharField()
    description = serializers.CharField()
    columns = SQLColumnSerializer(many=True)


class SQLExampleSerializer(serializers.Serializer):
    id = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    sql = serializers.CharField()
    datasets = serializers.ListField(child=serializers.CharField())


class SQLCatalogSerializer(serializers.Serializer):
    identity_semantics = serializers.CharField()
    version = serializers.IntegerField()
    datasets = SQLDatasetSerializer(many=True)
    examples = SQLExampleSerializer(many=True)
    max_rows = serializers.IntegerField()
    max_window_days = serializers.IntegerField()
    can_save = serializers.BooleanField()


class SQLResultSerializer(serializers.Serializer):
    columns = SQLColumnSerializer(many=True, required=False)
    rows = serializers.ListField(
        child=serializers.ListField(child=serializers.JSONField()), required=False
    )
    truncated = serializers.BooleanField(required=False)
    row_count = serializers.IntegerField(required=False)
    result_bytes = serializers.IntegerField(required=False)
    elapsed_ms = serializers.IntegerField(required=False)
    offset = serializers.IntegerField(required=False)
    next_offset = serializers.IntegerField(required=False, allow_null=True)
    has_more = serializers.BooleanField(required=False)
    page_row_count = serializers.IntegerField(required=False)
    previewed = serializers.BooleanField(required=False)
    retrieval_note = serializers.CharField(required=False, allow_blank=True)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Optional nullable fields otherwise appear as null even for an empty
        # result. Keep nonterminal/expired snapshots and unpaged REST compatible.
        if "next_offset" not in instance:
            data.pop("next_offset", None)
        return data


class SQLExecutionSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    project_id = serializers.UUIDField()
    sql = serializers.CharField()
    state = serializers.ChoiceField(
        choices=("queued", "running", "succeeded", "failed", "cancelled")
    )
    created_at = serializers.DateTimeField()
    expires_at = serializers.DateTimeField()
    finished_at = serializers.DateTimeField(allow_null=True)
    reused = serializers.BooleanField(required=False)
    error = serializers.CharField(allow_blank=True)
    result = SQLResultSerializer()
    expired = serializers.BooleanField()


class SQLEstimateSourceSerializer(serializers.Serializer):
    dataset = serializers.CharField()
    estimated = serializers.BooleanField()
    parts = serializers.IntegerField(allow_null=True)
    marks = serializers.IntegerField(allow_null=True)
    max_rows = serializers.IntegerField(allow_null=True)
    reason = serializers.CharField(allow_blank=True)


class SQLEstimateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=("ok", "unavailable"))
    reason = serializers.CharField(allow_blank=True)
    sources = SQLEstimateSourceSerializer(many=True)
    max_rows_to_read = serializers.IntegerField()
    exceeds_scan_bound = serializers.BooleanField(allow_null=True)
    elapsed_ms = serializers.IntegerField()


class SQLHistorySerializer(serializers.Serializer):
    items = SQLExecutionSerializer(many=True)
    has_more = serializers.BooleanField()


class SQLWidgetSerializer(SQLProjectSerializer):
    dashboard_id = serializers.UUIDField()


class SQLWidgetResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    dashboard_id = serializers.UUIDField()


class SQLErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
