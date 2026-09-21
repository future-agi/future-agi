"""Options for the request-log Application, Service and Custom Tags filters."""

from dataclasses import dataclass

from django.db.models.fields.json import KeyTextTransform

from agentcc.models import AgentccRequestLog
from agentcc.models.request_log import RequestLogTag

TAG_SEPARATOR = ":"

# Options are read from the org's most recent requests, so the picker costs the
# same however much history the org has.
METADATA_VALUES_SAMPLE_SIZE = 10_000
METADATA_VALUES_LIMIT = 100


@dataclass(frozen=True)
class MetadataValues:
    application: list[str]
    service: list[str]
    tags: list[str]


def _distinct_values(queryset, key):
    """Values with a comma are skipped: the filter params are comma-separated."""
    return list(
        queryset.annotate(value=KeyTextTransform(key, "metadata"))
        .exclude(value__isnull=True)
        .exclude(value="")
        .exclude(value__contains=",")
        .order_by("value")
        .values_list("value", flat=True)
        .distinct()[:METADATA_VALUES_LIMIT]
    )


def get_metadata_values(queryset, tag_keys):
    """Tag values in the latest requests of an already org-scoped queryset."""
    recent_ids = queryset.order_by("-started_at").values("id")[
        :METADATA_VALUES_SAMPLE_SIZE
    ]
    recent = AgentccRequestLog.no_workspace_objects.filter(id__in=recent_ids)
    return MetadataValues(
        application=_distinct_values(recent, RequestLogTag.APPLICATION.value),
        service=_distinct_values(recent, RequestLogTag.SERVICE.value),
        tags=[
            f"{key}{TAG_SEPARATOR}{value}"
            for key in tag_keys
            for value in _distinct_values(recent, key)
        ],
    )
