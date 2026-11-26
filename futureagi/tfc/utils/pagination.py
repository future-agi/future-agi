from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class ExtendedPageNumberPagination(PageNumberPagination):
    page_size = 10  # Default page size
    page_size_query_param = "limit"

    def get_paginated_response(self, data, total_queries=None) -> Response:
        response = super().get_paginated_response(data)
        total_pages = self.page.paginator.num_pages
        current_page = self.page.number
        response.data["total_pages"] = total_pages
        response.data["current_page"] = current_page
        if total_queries:
            response.data["total_queries"] = total_queries

        return response
