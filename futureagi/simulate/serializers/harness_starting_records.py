"""A scenario's starting records: the seeded rows its facts name, read-only."""

from __future__ import annotations

from rest_framework import serializers

from simulate.services.harness_starting_records import SUMMARY_VALUES


class HarnessStartingRecordSerializer(serializers.Serializer):
    path = serializers.ListField(
        child=serializers.CharField(),
        help_text="Where the fact sits in the scenario's fixture, one segment per level (a list position as digits).",
    )
    label = serializers.CharField(  # type: ignore[assignment]  # the API field is named label, as Field's own
        help_text="The path as one name."
    )
    table = serializers.CharField()
    key_field = serializers.CharField()
    key = serializers.CharField(help_text="The fact's value, as text.")
    status = serializers.ChoiceField(
        choices=("seeded", "missing"),
        help_text="seeded: the world's seed holds the row. missing: it does not, and note says so.",
    )
    summary = serializers.CharField(
        allow_null=True,
        help_text=f"The row in one line: its key, then up to {SUMMARY_VALUES} of its other values; null when missing.",
    )
    fields = serializers.DictField(  # type: ignore[assignment]  # the API field is named fields, as Serializer's own
        child=serializers.JSONField(),
        allow_null=True,
        help_text="The row as the world is seeded with it.",
    )
    note = serializers.CharField(
        allow_null=True, help_text="Why the record is missing, in words to show."
    )


class HarnessRowsUnavailableSerializer(serializers.Serializer):
    code = serializers.ChoiceField(choices=("no_seed_rows", "files_unreadable"))
    reason = serializers.CharField()


class HarnessStartingRecordsSerializer(serializers.Serializer):
    scenario_key = serializers.CharField()
    origin = serializers.CharField(
        allow_null=True,
        help_text="Where the fixture's data comes from: seed, generated or mixed.",
    )
    records = HarnessStartingRecordSerializer(many=True)
    setup_note = serializers.CharField(
        allow_null=True,
        help_text="Set when the scenario's setup changes the world before each run, so the rows are as seeded.",
    )
    rows_unavailable = HarnessRowsUnavailableSerializer(
        allow_null=True,
        help_text="Why the world's rows can't be read, or null when they can.",
    )
