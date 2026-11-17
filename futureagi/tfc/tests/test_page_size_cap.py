"""PAGINATION-MAXPAGE: reject oversized pages, never silently truncate."""

from unittest.mock import MagicMock

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from tfc.utils import pagination

# Freeze the survey-backed cap in the tests-only revision too (no new import
# required to collect the red tests against the old implementation).
CAP = 5000


class PageView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        paginator = pagination.ExtendedPageNumberPagination()
        page = paginator.paginate_queryset(range(CAP + 1), request)
        return paginator.get_paginated_response(list(page))


def _get(limit):
    return PageView.as_view()(APIRequestFactory().get("/pages/", {"limit": limit}))


def test_page_size_cap_is_named_and_survey_backed():
    assert pagination.MAX_PAGE_SIZE == CAP
    assert pagination.ExtendedPageNumberPagination.max_page_size == CAP


@pytest.mark.parametrize("limit", [CAP + 1, 10000000, "+5001", " 5001 "])
def test_oversized_page_is_typed_400(limit):
    response = _get(limit)
    assert response.status_code == 400
    assert response.data["code"] == "page_size_too_large"
    assert response.data["type"] == "validation_error"
    assert (
        response.data["message"]
        == "The maximum page size is 5000. Fetch more pages to retrieve additional results."
    )


def test_oversized_page_is_rejected_before_queryset_access():
    queryset = MagicMock()
    request = Request(APIRequestFactory().get("/pages/", {"limit": CAP + 1}))
    from rest_framework.exceptions import APIException

    with pytest.raises(APIException) as raised:
        pagination.ExtendedPageNumberPagination().paginate_queryset(queryset, request)
    assert raised.value.status_code == 400
    assert raised.value.get_codes() == "page_size_too_large"
    assert queryset.mock_calls == []


@pytest.mark.parametrize("limit", [1, 10, 1000, CAP])
def test_allowed_page_size_is_not_truncated(limit):
    response = _get(limit)
    assert response.status_code == 200
    assert len(response.data["results"]) == limit
    assert response.data["count"] == CAP + 1
    assert response.data["next"] is not None


@pytest.mark.parametrize("limit", ["", "nope", "1.5", "0", "-1"])
def test_invalid_or_nonpositive_limit_keeps_default(limit):
    response = _get(limit)
    assert response.status_code == 200
    assert len(response.data["results"]) == 10
