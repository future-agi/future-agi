"""Tests for common.utils.pagination.paginate_queryset.

The function's docstring promises that ``Paginator.get_page()`` clamps
out-of-range and non-numeric page numbers to a valid page. These tests pin
that documented contract for the query-param values a client can actually
send (all strings), including the malformed ones.
"""

from types import SimpleNamespace

from common.utils.pagination import DEFAULT_PAGE_SIZE, paginate_queryset


def _request(**query_params):
    # DRF exposes query params as strings via request.query_params.
    return SimpleNamespace(query_params=query_params)


ITEMS = list(range(25))  # Paginator accepts any sized sequence, no DB needed.


def test_valid_params_return_requested_page():
    page, meta = paginate_queryset(ITEMS, _request(page_number="2", page_size="10"))
    assert list(page) == list(range(10, 20))
    assert meta["page_number"] == 2
    assert meta["page_size"] == 10
    assert meta["total_count"] == 25
    assert meta["total_pages"] == 3


def test_out_of_range_page_number_returns_last_page():
    page, meta = paginate_queryset(ITEMS, _request(page_number="999", page_size="10"))
    assert meta["page_number"] == 3  # get_page clamps high pages to the last page
    assert list(page) == list(range(20, 25))


def test_non_numeric_page_number_returns_first_page():
    # Docstring: "values below 1 (or non-numeric) return the first page".
    page, meta = paginate_queryset(ITEMS, _request(page_number="abc", page_size="10"))
    assert meta["page_number"] == 1
    assert list(page) == list(range(0, 10))


def test_non_numeric_page_size_falls_back_to_default():
    page, meta = paginate_queryset(ITEMS, _request(page_size="abc"))
    assert meta["page_size"] == DEFAULT_PAGE_SIZE


def test_zero_page_size_falls_back_to_default():
    # Paginator(queryset, 0) raises ZeroDivisionError computing num_pages.
    page, meta = paginate_queryset(ITEMS, _request(page_size="0"))
    assert meta["page_size"] == DEFAULT_PAGE_SIZE
    assert meta["total_count"] == 25
