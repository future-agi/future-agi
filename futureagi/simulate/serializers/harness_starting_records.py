"""A scenario's starting records: the seeded rows its facts name, read-only."""

from __future__ import annotations

from rest_framework import serializers

from simulate.services.harness_starting_records import CHOICES_MAX, SUMMARY_VALUES


class HarnessSeededRowChoiceSerializer(serializers.Serializer):
    key = serializers.CharField(
        help_text="The row's key as text, as a rework request sends it back."
    )
    summary = serializers.CharField(
        help_text=f"The row in one line: its key, then up to {SUMMARY_VALUES} of its other values."
    )


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
    choices = HarnessSeededRowChoiceSerializer(
        many=True,
        help_text=f"Other seeded rows of the table the scenario could start from instead, the first {CHOICES_MAX}.",
    )


class HarnessStartingRecordReworkSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="The builder request's id.")
    state = serializers.ChoiceField(
        choices=("pending", "waiting", "proven", "refused"),
        help_text=(
            "pending: the builder is working on it. waiting: the builder asks the person to confirm its change in "
            "its chat (confirm_message_id). proven: the scenario now starts from the record and its proof holds, in "
            "the unsaved changes. refused: it did not, and reason says why."
        ),
    )
    table = serializers.CharField()
    key_field = serializers.CharField()
    key = serializers.CharField()
    reason = serializers.CharField(
        allow_null=True, help_text="Why it was refused, in words to show."
    )
    requested_at = serializers.DateTimeField()
    confirm_message_id = serializers.UUIDField(
        allow_null=True,
        help_text="While waiting: the builder chat's message asking the person to confirm; null otherwise.",
    )
    builder_reply = serializers.CharField(
        allow_null=True,
        help_text="On a refusal the builder explained: its reply as written, in Markdown; null otherwise.",
    )
    reply_message_id = serializers.UUIDField(
        allow_null=True, help_text="The builder chat's message holding that reply."
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
    rework = HarnessStartingRecordReworkSerializer(
        allow_null=True,
        help_text="The latest request to start this scenario from another record, or null if none was made.",
    )


class HarnessStartingRecordReworkWriteSerializer(serializers.Serializer):
    path = serializers.ListField(
        child=serializers.CharField(max_length=255),
        min_length=1,
        max_length=32,
        help_text="The `path` of the starting record to change.",
    )
    table = serializers.CharField(max_length=255)
    key = serializers.CharField(
        max_length=1024, help_text="The key of the seeded row to start from instead."
    )
