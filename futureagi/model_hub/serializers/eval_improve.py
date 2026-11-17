"""The Agreement tab's candidate and edited-draft request contract."""

import math
from uuid import UUID

from rest_framework import serializers


class EvalImproveEdit(serializers.Serializer):
    kind = serializers.ChoiceField(choices=["add", "remove"])
    clause = serializers.CharField(max_length=4000)
    source = serializers.CharField(required=False, max_length=40)
    def get_fields(self):
        fields = super().get_fields()
        # `from` is a Python keyword. Provenance is display-only: it never
        # selects data or supplies labels to the judge.
        fields["from"] = serializers.JSONField(required=False)
        return fields


class EvalImproveRequest(serializers.Serializer):
    source_key = serializers.CharField(max_length=100)
    source_template_id = serializers.UUIDField(required=False)
    draft_share = serializers.FloatField()
    seed = serializers.CharField(max_length=128)
    edits = EvalImproveEdit(many=True, required=False, max_length=10)
    instructions = serializers.CharField(required=False, max_length=16000)

    def validate_source_key(self, value):
        try:
            binding, label = value.split(":")
            return f"{UUID(binding)}:{UUID(label)}"
        except (ValueError, AttributeError) as exc:
            raise serializers.ValidationError("Choose a label source.") from exc

    def validate_draft_share(self, value):
        if not math.isfinite(value) or not 0 < value < 100:
            raise serializers.ValidationError("Draft share must be between 0 and 100.")
        return value

    def validate(self, attrs):
        if "edits" in attrs and not attrs["edits"] and "instructions" not in attrs:
            raise serializers.ValidationError("Select an edit or enter draft instructions.")
        return attrs


class EvalImproveAgreement(serializers.Serializer):
    draft = serializers.FloatField()
    check = serializers.FloatField()
    all = serializers.FloatField()
    stored = serializers.FloatField(required=False)


class EvalImproveCurrent(serializers.Serializer):
    from_saved = serializers.BooleanField()
    version = serializers.IntegerField(allow_null=True)
    judge_model = serializers.CharField()


class EvalImproveSplit(serializers.Serializer):
    draft_rows = serializers.ListField(child=serializers.UUIDField())
    check_rows = serializers.ListField(child=serializers.UUIDField())
    seed = serializers.CharField()
    draft_share = serializers.FloatField()


class EvalImprovePair(serializers.Serializer):
    row_id = serializers.UUIDField()
    human = serializers.JSONField()
    judge = serializers.JSONField()


class EvalImproveCandidate(EvalImproveEdit):
    instructions = serializers.CharField()
    agreement = EvalImproveAgreement()
    disagreements = EvalImprovePair(many=True)


class EvalImproveDraft(serializers.Serializer):
    instructions = serializers.CharField()


class EvalImproveVerdict(serializers.Serializer):
    row_id = serializers.UUIDField()
    row_label = serializers.CharField()
    part = serializers.ChoiceField(choices=["draft", "check"])
    human = serializers.JSONField()
    stored = serializers.JSONField()
    before = serializers.JSONField()
    after = serializers.JSONField()


class EvalImproveFlip(EvalImproveVerdict):
    toward_reviewer = serializers.BooleanField()


class EvalImproveResult(serializers.Serializer):
    current = EvalImproveCurrent()
    split = EvalImproveSplit()
    draft_notes = serializers.IntegerField()
    before = EvalImproveAgreement()
    candidates = EvalImproveCandidate(many=True, required=False)
    draft = EvalImproveDraft(required=False)
    after = EvalImproveAgreement(required=False)
    verdicts = EvalImproveVerdict(many=True, required=False)
    flips = EvalImproveFlip(many=True, required=False)
    disagreements = EvalImprovePair(many=True, required=False)


class EvalImproveResponse(serializers.Serializer):
    status = serializers.BooleanField()
    result = EvalImproveResult()
