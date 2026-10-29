from types import SimpleNamespace

import pytest

from common.utils.pagination import DEFAULT_PAGE_SIZE, paginate_queryset

ITEMS = list(range(25))


def _paginate(**params):
    request = SimpleNamespace(query_params={k: str(v) for k, v in params.items()})
    return paginate_queryset(ITEMS, request)


def test_defaults_when_params_absent():
    rows, meta = _paginate()

    assert rows == ITEMS[:DEFAULT_PAGE_SIZE]
    assert meta["page_number"] == 1
    assert meta["page_size"] == DEFAULT_PAGE_SIZE
    assert meta["total_pages"] == 3


def test_valid_params_are_respected():
    rows, meta = _paginate(page_number=2, page_size=5)

    assert rows == ITEMS[5:10]
    assert (meta["page_number"], meta["page_size"]) == (2, 5)
    assert (meta["previous_page"], meta["next_page"]) == (1, 3)


@pytest.mark.parametrize("page_number", ["abc", "", "0", "-3", "1.5"])
def test_invalid_page_number_falls_back_to_first_page(page_number):
    rows, meta = _paginate(page_number=page_number)

    assert rows == ITEMS[:DEFAULT_PAGE_SIZE]
    assert meta["page_number"] == 1


@pytest.mark.parametrize("page_size", ["abc", "", "0", "-5", "2.5"])
def test_invalid_page_size_falls_back_to_default(page_size):
    rows, meta = _paginate(page_size=page_size)

    assert rows == ITEMS[:DEFAULT_PAGE_SIZE]
    assert meta["page_size"] == DEFAULT_PAGE_SIZE


def test_page_number_above_total_clamps_to_last_page():
    rows, meta = _paginate(page_number=99, page_size=10)

    assert rows == ITEMS[20:]
    assert meta["page_number"] == 3
    assert meta["next_page"] is None
