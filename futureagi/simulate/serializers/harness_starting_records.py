"""A scenario's starting records: the seeded rows its facts name, and the choice of another one."""

from __future__ import annotations

from rest_framework import serializers


class HarnessSeededRowSerializer(serializers.Serializer):
    key = serializers.JSONField(help_text="The row's key, a string or an integer.")
    fields = serializers.DictField(
        child=serializers.JSONField(),
        help_text="The row as the world is seeded with it.",
    )


class HarnessStartingRecordSerializer(serializers.Serializer):
    path = serializers.ListField(
        child=serializers.CharField(),
        help_text="Where the fact sits in the scenario's fixture, one segment per level (a list position as digits).",
    )
    label = serializers.CharField()
    table = serializers.CharField()
    key_field = serializers.CharField()
    key = serializers.JSONField()
    fields = serializers.DictField(child=serializers.JSONField())
    alternatives = HarnessSeededRowSerializer(
        many=True,
        help_text="Other rows of the table the scenario could start from instead, the first few.",
    )
    alternatives_total = serializers.IntegerField(min_value=0)


class HarnessRowsUnavailableSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=("no_seed_rows", "files_unreadable"))
    reason = serializers.CharField()


class HarnessStartingRecordsSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    origin = serializers.CharField(
        allow_null=True,
        help_text="Where the fixture's data comes from: seed, generated or mixed.",
    )
    editable = serializers.BooleanField(
        help_text="Whether a choice can be written to this environment's draft."
    )
    records = HarnessStartingRecordSerializer(many=True)
    rows_unavailable = HarnessRowsUnavailableSerializer(
        allow_null=True,
        help_text="Why the world's rows can't be read, or null when they can.",
    )


class HarnessStartingRecordChoiceSerializer(serializers.Serializer):
    path = serializers.ListField(
        child=serializers.CharField(max_length=255),
        min_length=1,
        max_length=32,
        help_text="The `path` of one of the scenario's starting records.",
    )
    table = serializers.CharField(max_length=255)
    key = serializers.CharField(
        max_length=1024, help_text="The key of the seeded row to start from instead."
    )
