from django.core.paginator import Paginator
from django.db.models import QuerySet
from rest_framework.request import Request

DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


def paginate_queryset(queryset: QuerySet, request: Request) -> tuple[list, dict]:
    """
    Paginate a queryset using ``page_number`` and ``page_size`` query params.

    Django's ``Paginator.get_page()`` returns the first page for non-numeric
    page numbers and the last page for out-of-range numbers, including zero
    and negative numbers. Missing page numbers default to the first page.

    Missing, non-numeric, or non-positive page sizes use ``DEFAULT_PAGE_SIZE``.
    Larger requests are capped at ``MAX_PAGE_SIZE``.
    """
    page_number = request.query_params.get("page_number", 1)
    try:
        page_size = int(request.query_params.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    if page_size < 1:
        page_size = DEFAULT_PAGE_SIZE
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
