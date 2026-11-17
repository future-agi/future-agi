"""Shared refusal for independently paginated reads and serializer fields.

The canonical cap and public error live in tfc.utils.pagination. Keep raw
parameter parsing at each caller so its existing malformed-input policy stays
intact; this preflight only rejects an integer above the shared ceiling.
"""

from rest_framework import serializers
from rest_framework.exceptions import NotFound

from tfc.utils.pagination import MAX_PAGE_SIZE, PageSizeTooLarge


def enforce_page_size(value):
    """Reject an oversized integer before queries, counts, or serialization."""
    try:
        requested = int(value)
    except (TypeError, ValueError):
        return
    if requested > MAX_PAGE_SIZE:
        raise PageSizeTooLarge()


class PageSizeIntegerField(serializers.IntegerField):
    """A schema-visible cap with the same typed refusal as the paginator."""

    def __init__(self, **kwargs):
        kwargs.setdefault("max_value", MAX_PAGE_SIZE)
        super().__init__(**kwargs)

    def to_internal_value(self, data):
        value = super().to_internal_value(data)
        enforce_page_size(value)
        return value


def page_bounds(page, page_size, total_count, *, first_page=0):
    """Return slice bounds; invalid manual pages use DRF's fixed 404.

    Empty collections still have a valid first page, just like the numbered
    paginator. This helper never changes a valid page's indexing convention.
    """
    enforce_page_size(page_size)
    start = (page - first_page) * page_size
    if (
        page_size <= 0
        or page < first_page
        or (start >= total_count and page != first_page)
    ):
        raise NotFound("Invalid page.")
    return start, start + page_size
