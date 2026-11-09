"""Bounded jury definitions; executable code and remote URLs are not accepted."""

import math

from rest_framework import serializers

from model_hub.serializers.contracts import CompositeChildResultSerializer


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) - set(self.fields):
            raise serializers.ValidationError("Unknown jury fields.")
        return super().to_internal_value(data)


class JurySeatSerializer(StrictSerializer):
    id = serializers.RegexField(r"^[a-zA-Z0-9_-]{1,64}$")
    kind = serializers.ChoiceField(choices=("eval", "check"))
    child_id = serializers.UUIDField(required=False)
    model = serializers.CharField(required=False, allow_blank=True, max_length=200)
    level = serializers.ChoiceField(choices=("row", "step"), required=False)
    step_kind = serializers.ChoiceField(choices=("llm", "tool"), required=False)
    reduce = serializers.ChoiceField(
        choices=("all_pass", "mean", "min"), required=False
    )
    check_key = serializers.RegexField(r"^[a-zA-Z0-9_.-]{1,128}$", required=False)
    weight = serializers.FloatField(min_value=0.001, max_value=10, default=1)

    def validate(self, attrs):
        if not math.isfinite(attrs["weight"]):
            raise serializers.ValidationError("Weight must be finite.")
        if attrs["kind"] == "check":
            if not attrs.get("check_key") or set(attrs) - {
                "id",
                "kind",
                "check_key",
                "weight",
            }:
                raise serializers.ValidationError(
                    "Checks require only a key, identifier and weight."
                )
        else:
            if not attrs.get("child_id") or "check_key" in attrs:
                raise serializers.ValidationError(
                    "An evaluation seat requires a child template."
                )
            attrs["child_id"] = str(attrs["child_id"])
            attrs.setdefault("level", "row")
            if attrs["level"] == "step":
                if not attrs.get("step_kind") or not attrs.get("reduce"):
                    raise serializers.ValidationError(
                        "Step seats require a kind and reducer."
                    )
            elif "step_kind" in attrs or "reduce" in attrs:
                raise serializers.ValidationError(
                    "Row seats cannot declare step settings."
                )
        return attrs


class JuryDefinitionSerializer(StrictSerializer):
    seats = JurySeatSerializer(many=True, allow_empty=False, max_length=16)

    def validate_seats(self, seats):
        ids = [seat["id"] for seat in seats]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Seat identifiers must be unique.")
        return seats


class JuryDefinitionResponseSerializer(serializers.Serializer):
    result = JuryDefinitionSerializer()


class JuryReportRunSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    run_label = serializers.CharField()


class JuryReportSeatSerializer(serializers.Serializer):
    """A stored seat definition and its execution snapshot, not an input."""

    id = serializers.CharField()
    kind = serializers.ChoiceField(choices=("eval", "check"))
    child_id = serializers.UUIDField(required=False)
    model = serializers.CharField(required=False, allow_blank=True)
    level = serializers.ChoiceField(choices=("row", "step"), required=False)
    step_kind = serializers.ChoiceField(choices=("llm", "tool"), required=False)
    reduce = serializers.ChoiceField(
        choices=("all_pass", "mean", "min"), required=False
    )
    check_key = serializers.CharField(required=False)
    weight = serializers.FloatField()
    child_name = serializers.CharField()
    score = serializers.FloatField(allow_null=True)
    passed = serializers.BooleanField(allow_null=True)
    threshold = serializers.FloatField()
    status = serializers.CharField()
    reason = serializers.CharField(allow_blank=True, allow_null=True)
    error = serializers.CharField(allow_blank=True, allow_null=True)
    steps = CompositeChildResultSerializer(many=True)


class JuryPairSerializer(serializers.Serializer):
    a = serializers.CharField()
    b = serializers.CharField()
    n = serializers.IntegerField()
    agreement = serializers.FloatField(allow_null=True)
    ci = serializers.ListField(child=serializers.FloatField(), allow_null=True)
    kappa = serializers.FloatField(allow_null=True)
    low_sample = serializers.BooleanField()


class JurySeatSummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    judge_model = serializers.CharField(allow_null=True, allow_blank=True)
    n = serializers.IntegerField()
    pass_rate = serializers.FloatField(allow_null=True)


class JuryDatasetRowSerializer(serializers.Serializer):
    row_id = serializers.UUIDField()
    row_number = serializers.IntegerField()
    evaluation_id = serializers.UUIDField(allow_null=True)
    # A row's seats are the stored execution snapshots (3e's typed seat shape).
    seats = JuryReportSeatSerializer(many=True)
    complete = serializers.BooleanField()
    disagreement = serializers.BooleanField()
    aggregate_score = serializers.FloatField(allow_null=True)


class JuryReportSerializer(serializers.Serializer):
    runs = JuryReportRunSerializer(many=True)
    # Required for a run report; see validate(). A dataset report (kind
    # "dataset") with no stored run omits run_id and keeps seats per row.
    run_id = serializers.UUIDField(required=False, allow_null=True)
    seats = JuryReportSeatSerializer(many=True, required=False)
    # These fields exist only when a stored run has been selected.
    aggregate_score = serializers.FloatField(required=False, allow_null=True)
    aggregate_pass = serializers.BooleanField(required=False, allow_null=True)
    disagreement = serializers.BooleanField(required=False)
    complete = serializers.BooleanField(required=False)
    # shared-edit(F68): a stored dataset jury run (kind "dataset").
    kind = serializers.ChoiceField(choices=("dataset",), required=False)
    dataset_id = serializers.UUIDField(required=False)
    experiment_id = serializers.UUIDField(required=False, allow_null=True)
    version_id = serializers.UUIDField(required=False, allow_null=True)
    n = serializers.IntegerField(required=False)
    rows = JuryDatasetRowSerializer(many=True, required=False)
    children = JurySeatSummarySerializer(many=True, required=False)
    pairs = JuryPairSerializer(many=True, required=False)
    split_rows = serializers.ListField(child=serializers.UUIDField(), required=False)

    # DRF renders an absent nullable field as null, and only a dataset report
    # has these two, so a run report keeps its original shape.
    dataset_only_fields = ("experiment_id", "version_id")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("kind") != "dataset":
            for name in self.dataset_only_fields:
                data.pop(name, None)
        return data

    def validate(self, attrs):
        # A run report keeps the 3e envelope: run_id and seats are required.
        if attrs.get("kind") != "dataset":
            missing = [key for key in ("run_id", "seats") if key not in attrs]
            if missing:
                raise serializers.ValidationError(
                    {key: "This field is required." for key in missing}  # noqa: C420
                )
        return attrs


class JuryReportResponseSerializer(serializers.Serializer):
    result = JuryReportSerializer()


class JuryVersionQuerySerializer(serializers.Serializer):
    version_id = serializers.CharField(required=False, max_length=36)


class JuryReportQuerySerializer(serializers.Serializer):
    run_id = serializers.UUIDField(required=False)
    dataset_id = serializers.UUIDField(required=False)
