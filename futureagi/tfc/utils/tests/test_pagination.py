"""A list paged with one required row pinned to the top of page one (Dileep, #4150)."""

import pytest
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from tfc.utils.pagination import ExtendedPageNumberPagination, paginate_required_first


class _Rows(list):
    """A list that counts like a queryset, so the helper can be paged without a database."""

    def count(self):
        return len(self)


def _page(rest, required, *, page, limit):
    request = Request(APIRequestFactory().get("/", {"page": page, "limit": limit}))
    paginator = ExtendedPageNumberPagination()
    return paginate_required_first(paginator, _Rows(rest), required, request), paginator


@pytest.mark.parametrize("rest_size", range(0, 25))
@pytest.mark.parametrize("limit", range(1, 8))
def test_every_row_is_on_exactly_one_page_and_the_required_one_leads(rest_size, limit):
    rest = [f"row-{i}" for i in range(rest_size)]
    first, paginator = _page(rest, "required", page=1, limit=limit)
    seen = list(first)
    for page in range(2, paginator.page.paginator.num_pages + 1):
        more, _ = _page(rest, "required", page=page, limit=limit)
        seen.extend(more)

    assert first[0] == "required"
    assert sorted(seen) == sorted(["required", *rest])
    assert paginator.page.paginator.count == rest_size + 1


def test_without_a_required_row_it_pages_the_rest_as_given():
    page, paginator = _page(["a", "b", "c"], None, page=1, limit=2)

    assert page == ["a", "b"]
    assert paginator.page.paginator.count == 3
