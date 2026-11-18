from typing import Any

from rest_framework.pagination import PageNumberPagination


class ExtendedPageNumberPagination(PageNumberPagination):
    page_size = 10  # Default page size
    page_size_query_param = "limit"

    def get_paginated_response(self, data, total_queries=None):
        response = super().get_paginated_response(data)
        total_pages = self.page.paginator.num_pages
        current_page = self.page.number
        response.data["total_pages"] = total_pages
        response.data["current_page"] = current_page
        if total_queries:
            response.data["total_queries"] = total_queries

        return response


class _RequiredFirst:
    """The required row followed by the ordered rest, as one sliceable list.

    Paginating this, not the rest alone, keeps every row on exactly one page, the required
    one on page 1, and the count the same on every page.
    """

    def __init__(self, required: Any, rest: Any) -> None:
        self._required = required
        self._rest = rest
        self._rest_count: int | None = None

    def __len__(self) -> int:
        if self._rest_count is None:
            self._rest_count = self._rest.count()
        return self._rest_count + 1

    def __getitem__(self, key: slice) -> list[Any]:
        start, stop, _ = key.indices(len(self))
        if start == 0:
            return [self._required, *self._rest[: max(stop - 1, 0)]]
        return list(self._rest[start - 1 : stop - 1])


def paginate_required_first(
    paginator: PageNumberPagination, rest: Any, required: Any, request: Any
) -> list[Any]:
    """One page of ``rest``, with ``required`` (when given) pinned to the top of page 1.

    ``rest`` must already leave ``required`` out. The paginator's count includes it.
    """
    rows: Any = _RequiredFirst(required, rest) if required is not None else rest
    return paginator.paginate_queryset(rows, request) or []
