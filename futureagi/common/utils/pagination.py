from django.core.paginator import Paginator
from django.db.models import QuerySet
from rest_framework.request import Request

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


def paginate_queryset(queryset: QuerySet, request: Request) -> tuple[list, dict]:
    """
    Paginate a queryset using ``page_number`` and ``page_size`` query params.

    Uses Django's ``Paginator.get_page()`` which clamps out-of-range page
    numbers: values above ``total_pages`` return the last page, values
    below 1 (or non-numeric) return the first page.

    ``page_size`` is silently clamped to ``MAX_PAGE_SIZE`` so a client cannot
    force an unbounded response.

    Args:
        queryset: The Django QuerySet to paginate.
        request: DRF request whose ``query_params`` supply ``page_number``
            (default 1) and ``page_size`` (default 10, max 100).
    """
    page_number = int(request.query_params.get("page_number", 1))
    page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    page_size = min(page_size, MAX_PAGE_SIZE)

    paginator = Paginator(queryset, page_size)
    page = paginator.get_page(page_number)

    total_pages = paginator.num_pages

    metadata = {
        "total_count": paginator.count,
        "page_number": page.number,
        "page_size": page_size,
        "total_pages": total_pages,
        "next_page": page.number + 1 if page.has_next() else None,
        "previous_page": page.number - 1 if page.has_previous() else None,
    }

    return page.object_list, metadata
