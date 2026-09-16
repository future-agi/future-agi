from __future__ import annotations

from rest_framework import serializers

from simulate.services.harness_environment import (
    AGENT_TYPE_CHAT,
    AGENT_TYPE_VOICE,
    STATUS_BUILDING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
)


class HarnessEnvironmentListQuerySerializer(serializers.Serializer):
    """Paging for the environments list.

    ``limit`` is named to match ``ExtendedPageNumberPagination``'s own query
    parameter so the two cannot disagree about what a page is.
    """

    page = serializers.IntegerField(required=False, min_value=1)
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100)


class HarnessEnvironmentSerializer(serializers.Serializer):
    """One row of the environments list.

    ``description`` and ``tools_count`` are null until authoring has produced a
    contract; they are reported as absent rather than as an empty string or a
    zero, which would read as "this environment has no tools".
    """

    id = serializers.UUIDField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    source_kind = serializers.CharField()
    agent_type = serializers.ChoiceField(choices=(AGENT_TYPE_VOICE, AGENT_TYPE_CHAT))
    status = serializers.ChoiceField(
        choices=(STATUS_BUILDING, STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED)
    )
    # The fine-grained pipeline stage behind ``status``, so a caller that draws a
    # progress bar does not have to infer it from the four-state pill.
    stage = serializers.CharField()
    scenario_count = serializers.IntegerField()
    tools_count = serializers.IntegerField(allow_null=True)
    last_updated = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()


class HarnessEnvironmentListResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    total_pages = serializers.IntegerField()
    current_page = serializers.IntegerField()
    results = HarnessEnvironmentSerializer(many=True)


class HarnessEnvironmentRunResponseSerializer(serializers.Serializer):
    """What starting a simulation returns.

    The trigger is accepted, not completed: execution is a separate concern and
    the caller polls the job for progress.
    """

    environment_id = serializers.UUIDField()
    job_id = serializers.UUIDField()
    run_id = serializers.UUIDField()
    state = serializers.CharField()
    stage = serializers.CharField()
