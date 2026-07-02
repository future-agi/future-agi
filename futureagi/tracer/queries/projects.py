"""Read-side queryset filters for the project list.

Houses the ``filters`` JSON → queryset translation for
``ProjectView.list_projects`` so the view stays thin and the logic is
unit-testable without HTTP (mirrors the other helpers in this package, e.g.
``end_users._apply_text_queryset_filter``).
"""

import json

import structlog
from django.db.models import BooleanField, QuerySet
from django.db.models.expressions import RawSQL

from tracer.models.project import Project
from tracer.utils.filters import normalize_filter_item

logger = structlog.get_logger(__name__)


def _tag_substring_filter(
    queryset: QuerySet[Project],
    value: str,
    *,
    negate: bool,
    alias: str,
) -> QuerySet[Project]:
    """Match projects whose JSONB ``tags`` array has any element containing
    ``value`` as a substring (case-insensitive).

    Matched per element via ``jsonb_array_elements_text`` so a substring can
    never span two tags or match the serialized array's artifacts: casting the
    whole array to text (``["prod", "critical"]``) would let the separator
    ``", "`` or the ``[`` / ``"`` characters match. The ``jsonb_typeof`` guard
    coerces non-array / null ``tags`` to ``[]`` so the unnest never errors.
    """
    # LIKE-escape so % / _ in the user value are matched literally.
    esc = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{esc}%"
    table = Project._meta.db_table
    cond = RawSQL(
        f'EXISTS (SELECT 1 FROM jsonb_array_elements_text('
        f'CASE WHEN jsonb_typeof("{table}".tags) = \'array\' '
        f'THEN "{table}".tags ELSE \'[]\'::jsonb END) AS _tag WHERE _tag ILIKE %s)',
        (pattern,),
        output_field=BooleanField(),
    )
    queryset = queryset.annotate(**{alias: cond})
    return (
        queryset.exclude(**{alias: True}) if negate else queryset.filter(**{alias: True})
    )


def apply_project_list_filters(
    queryset: QuerySet[Project], filters_param: str | None
) -> QuerySet[Project]:
    """Apply name / tag operator filters from a ``filters`` JSON array.

    Shape (one entry per active filter, the trace/span list convention)::

        [{"column_id": "name" | "tags",
          "filter_config": {"filter_op": "equals" | "contains"
                                          | "not_equals" | "not_contains",
                            "filter_value": "<str>"}}]

    name — equals: exact (case-insensitive); contains: substring.
    tags — equals: the project has that exact tag; contains: the project has a
           tag whose value contains the substring (matched per element).

    Best-effort: malformed JSON / unknown columns / non-string or blank values
    are skipped so the list still renders (never 500s on a bad filter param).
    """
    if not filters_param:
        return queryset
    try:
        filters = json.loads(filters_param)
    except (ValueError, TypeError):
        logger.warning("project_list_filters_malformed", value=str(filters_param)[:200])
        return queryset
    if not isinstance(filters, list):
        return queryset

    for idx, raw in enumerate(filters):
        item = normalize_filter_item(raw)
        column = item["column_id"]
        cfg = item["filter_config"]
        op = cfg.get("filter_op") or "contains"
        value = cfg.get("filter_value")
        if not column or not isinstance(value, str) or value == "":
            continue

        if column == "name":
            if op == "equals":
                queryset = queryset.filter(name__iexact=value)
            elif op == "not_equals":
                queryset = queryset.exclude(name__iexact=value)
            elif op == "not_contains":
                queryset = queryset.exclude(name__icontains=value)
            else:  # contains (default)
                queryset = queryset.filter(name__icontains=value)

        elif column == "tags":
            if op == "equals":
                queryset = queryset.filter(tags__contains=[value])
            elif op == "not_equals":
                queryset = queryset.exclude(tags__contains=[value])
            elif op in ("contains", "not_contains"):
                queryset = _tag_substring_filter(
                    queryset,
                    value,
                    negate=(op == "not_contains"),
                    alias=f"_tag_match_{idx}",
                )

    return queryset
