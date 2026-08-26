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

    Args:
        queryset: The Django QuerySet to paginate.
        request: DRF request whose ``query_params`` supply ``page_number``
            (default 1) and ``page_size`` (default 10).
    """
    page_number = int(request.query_params.get("page_number", 1))
    try:
        page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    # Bound the client-controlled page size so a single request cannot force
    # the whole table into one response (#2304).
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))

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
