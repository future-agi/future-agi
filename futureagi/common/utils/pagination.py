from django.core.paginator import Paginator
from django.db.models import QuerySet
from rest_framework.request import Request

DEFAULT_PAGE_SIZE = 10


def _positive_int(value, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def paginate_queryset(queryset: QuerySet, request: Request) -> tuple[list, dict]:
    """
    Paginate a queryset using ``page_number`` and ``page_size`` query params.

    Invalid input never raises: a non-numeric or non-positive ``page_number``
    falls back to the first page and a non-numeric or non-positive
    ``page_size`` falls back to ``DEFAULT_PAGE_SIZE``. Django's
    ``Paginator.get_page()`` clamps page numbers above ``total_pages`` to
    the last page.

    Args:
        queryset: The Django QuerySet to paginate.
        request: DRF request whose ``query_params`` supply ``page_number``
            (default 1) and ``page_size`` (default 10).
    """
    page_number = _positive_int(request.query_params.get("page_number"), 1)
    page_size = _positive_int(request.query_params.get("page_size"), DEFAULT_PAGE_SIZE)

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
