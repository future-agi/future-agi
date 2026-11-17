from django.core.paginator import InvalidPage, Paginator
from django.db.models import QuerySet
from rest_framework.exceptions import NotFound
from rest_framework.request import Request

from common.utils.page_limits import enforce_page_size

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


def paginate_queryset(queryset: QuerySet, request: Request) -> tuple[list, dict]:
    """Paginate with one-based ``page_number`` and capped ``page_size``.

    Oversized requests are refused before counting or materializing rows.
    Invalid page numbers are 404s rather than silently serving another page.
    A ``page_size`` within the shared cap but above ``MAX_PAGE_SIZE`` is
    clamped to ``MAX_PAGE_SIZE``, so a list never returns more than 100 rows.

    Args:
        queryset: The Django QuerySet to paginate.
        request: DRF request whose ``query_params`` supply ``page_number``
            (default 1) and ``page_size`` (default 10, max 100).
    """
    enforce_page_size(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    page_number = request.query_params.get("page_number", 1)
    page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    if page_size <= 0:
        raise NotFound("Invalid page.")
    page_size = min(page_size, MAX_PAGE_SIZE)

    paginator = Paginator(queryset, page_size)
    try:
        page = paginator.page(page_number)
    except InvalidPage:
        raise NotFound("Invalid page.") from None

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
