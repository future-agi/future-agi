from types import SimpleNamespace

import pytest

from common.utils.pagination import DEFAULT_PAGE_SIZE, paginate_queryset


@pytest.mark.parametrize(
    "params, expected_page_size, expected_page",
    [
        ({"page_number": "abc"}, DEFAULT_PAGE_SIZE, list(range(10))),
        ({"page_size": "abc"}, DEFAULT_PAGE_SIZE, list(range(10))),
        ({"page_size": "0"}, DEFAULT_PAGE_SIZE, list(range(10))),
        ({"page_size": "-5"}, DEFAULT_PAGE_SIZE, list(range(10))),
        ({"page_number": "2", "page_size": "5"}, 5, list(range(5, 10))),
    ],
)
def test_paginate_queryset_handles_invalid_query_params(
    params, expected_page_size, expected_page
):
    page, metadata = paginate_queryset(
        list(range(12)), SimpleNamespace(query_params=params)
    )

    assert metadata["page_size"] == expected_page_size
    assert metadata["total_count"] == 12
    assert page == expected_page
