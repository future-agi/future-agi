"""C-RESULTS: one bounded query contract for grid, matrix, full row and IDs."""
import json
import math

from rest_framework import serializers

from model_hub.serializers.results_runs import StrictQuerySerializer, RunSelectorField, identifier

OPS = {"eq", "ne", "lt", "lte", "gt", "gte", "=", "!=", "<", "<=", ">", ">="}
PILL_FIELDS = {
    "rows": {"ids", "label"}, "meta": {"key", "op", "value"},
    "split": {"values"}, "error": {"run"}, "flaky": {"run"},
    "score": {"metric", "op", "value", "run"},
    "fails": {"metric", "run"}, "latency": {"op", "value", "run"},
    "cost": {"op", "value", "run"},
}


def _nonfinite_filter(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def filters_value(value):
    try:
        pills = json.loads(value, parse_constant=_nonfinite_filter) if isinstance(value, str) else value
    except (ValueError, TypeError) as exc:
        raise serializers.ValidationError("Filters must be a JSON array.") from exc
    if not isinstance(pills, list) or len(pills) > 20:
        raise serializers.ValidationError("At most 20 filters are supported.")
    for p in pills:
        if (not isinstance(p, dict) or not isinstance(p.get("type"), str)
                or p["type"] not in PILL_FIELDS):
            raise serializers.ValidationError("Unsupported filter type in this checkpoint.")
        kind = p["type"]
        if set(p) - ({"type"} | PILL_FIELDS[kind]):
            raise serializers.ValidationError("Unsupported filter field.")
        if "run" in p and p["run"] not in ("any_run", "any_candidate"):
            p["run"] = identifier(p["run"])
        if kind == "rows":
            if not isinstance(p.get("ids"), list) or len(p["ids"]) > 10000:
                raise serializers.ValidationError("Rows requires at most 10000 IDs.")
            p["ids"] = [identifier(v) for v in p["ids"]]
        if kind == "split" and (not isinstance(p.get("values"), list) or
                not p["values"] or any(not isinstance(v, str) for v in p["values"])):
            raise serializers.ValidationError("Split requires string values.")
        if kind in {"meta", "score", "cost", "latency"}:
            if not isinstance(p.get("op"), str) or p["op"] not in OPS or "value" not in p:
                raise serializers.ValidationError("Unsupported comparison operator/value.")
        if kind in {"score", "cost", "latency"}:
            if type(p["value"]) not in (int, float) or not math.isfinite(p["value"]):
                raise serializers.ValidationError("A finite numeric comparison is required.")
        if kind == "meta" and (not isinstance(p.get("key"), str) or len(p["key"]) > 200):
            raise serializers.ValidationError("Metadata requires a bounded key.")
        if kind in {"score", "fails"}:
            p["metric"] = identifier(p.get("metric"))
    return pills


class DatasetResultsQuerySerializer(StrictQuerySerializer):
    runs = serializers.CharField(max_length=8 * 37)
    baseline = RunSelectorField(required=False, max_length=36)
    filters = serializers.CharField(default="[]", max_length=512000)
    show = serializers.ChoiceField(choices=["all", "errors", "failed", "flaky"], default="all")
    search = serializers.CharField(default="", allow_blank=True, max_length=200)
    sort = serializers.ChoiceField(choices=["order", "-order", "input", "-input", "regressed"], default="order")
    agg = serializers.ChoiceField(choices=["mean"], default="mean")
    mix_versions = serializers.BooleanField(default=False)
    shape = serializers.ChoiceField(choices=["grid", "matrix"], default="grid")
    measure = serializers.CharField(default="pass_at_1", max_length=36)
    page = serializers.IntegerField(default=0, min_value=0, max_value=1000000)
    page_size = serializers.IntegerField(default=1000, min_value=1, max_value=1000)
    example_id = serializers.UUIDField(required=False)
    full = serializers.BooleanField(default=False)
    ids_only = serializers.BooleanField(default=False)

    def validate_runs(self, value):
        ids = [identifier(v) for v in value.split(",")]
        if not 1 <= len(ids) <= 8 or len(ids) != len(set(ids)):
            raise serializers.ValidationError("Select 1-8 distinct runs.")
        return ids

    def validate_filters(self, value):
        return filters_value(value)

    def validate_measure(self, value):
        return value if value == "pass_at_1" else identifier(value)

    def validate(self, attrs):
        if attrs["shape"] == "matrix":
            if "page_size" not in self.initial_data:
                attrs["page_size"] = 500
            if attrs["page_size"] > 500:
                raise serializers.ValidationError("Matrix page_size is at most 500.")
        if attrs["full"] != ("example_id" in attrs):
            raise serializers.ValidationError("full=true requires example_id and vice versa.")
        if (attrs["full"] and attrs["ids_only"]) or (
                attrs["shape"] == "matrix" and (attrs["full"] or attrs["ids_only"])):
            raise serializers.ValidationError("Incompatible result shape flags.")
        return attrs


class ResultsPaginationSerializer(serializers.Serializer):
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    total = serializers.IntegerField()
    has_more = serializers.BooleanField()
    next = serializers.IntegerField(allow_null=True)


class ResultsScopeSerializer(serializers.Serializer):
    population = serializers.IntegerField()
    complete = serializers.BooleanField()
    reason = serializers.CharField(required=False, allow_null=True)
    selection_id = serializers.CharField()


class DatasetResultsResultSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    rows = serializers.ListField(child=serializers.DictField(), required=False)
    example_ids = serializers.ListField(child=serializers.UUIDField(), required=False)
    columns = serializers.DictField(required=False)
    summary = serializers.DictField()
    pagination = ResultsPaginationSerializer()
    scope = ResultsScopeSerializer()


class DatasetResultsResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    result = DatasetResultsResultSerializer()
