from rest_framework.exceptions import APIException
from rest_framework.pagination import PageNumberPagination

# First-party pages top out at 1,000 rows; keep at least four times that
# headroom while bounding a shared worker's per-request row materialization.
MAX_PAGE_SIZE = 5000


class PageSizeTooLarge(APIException):
    status_code = 400
    default_code = "page_size_too_large"
    default_detail = (
        f"The maximum page size is {MAX_PAGE_SIZE}. "
        "Fetch more pages to retrieve additional results."
    )


class ExtendedPageNumberPagination(PageNumberPagination):
    page_size = 10  # Default page size
    page_size_query_param = "limit"
    max_page_size = MAX_PAGE_SIZE

    def get_page_size(self, request):
        # DRF's max_page_size silently clamps: reject first instead. Delegate
        # invalid/nonpositive values to DRF so their defaulting stays unchanged.
        try:
            requested = int(request.query_params[self.page_size_query_param])
        except (KeyError, TypeError, ValueError):
            return super().get_page_size(request)
        if requested > MAX_PAGE_SIZE:
            raise PageSizeTooLarge()
        return super().get_page_size(request)

    def get_paginated_response(self, data, total_queries=None):
        response = super().get_paginated_response(data)
        total_pages = self.page.paginator.num_pages
        current_page = self.page.number
        response.data["total_pages"] = total_pages
        response.data["current_page"] = current_page
        if total_queries:
            response.data["total_queries"] = total_queries

        return response
