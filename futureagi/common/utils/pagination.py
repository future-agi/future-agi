from django.core.paginator import InvalidPage, Paginator
from django.db.models import QuerySet
from rest_framework.exceptions import NotFound
from rest_framework.request import Request

from common.utils.page_limits import enforce_page_size

DEFAULT_PAGE_SIZE = 10


def paginate_queryset(queryset: QuerySet, request: Request) -> tuple[list, dict]:
    """Paginate with one-based ``page_number`` and capped ``page_size``.

    Oversized requests are refused before counting or materializing rows.
    Invalid page numbers are 404s rather than silently serving another page.
    """
    enforce_page_size(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    page_number = request.query_params.get("page_number", 1)
    page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    if page_size <= 0:
        raise NotFound("Invalid page.")

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
