"""Tests for common.utils.pagination.paginate_queryset."""

from types import SimpleNamespace

from common.utils.pagination import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, paginate_queryset


def _request(**query_params):
    # DRF exposes query params as strings via request.query_params.
    return SimpleNamespace(query_params=query_params)


class _SliceRecordingSequence:
    """Sized sequence that records slice bounds.

    Django's Paginator pages via ``object_list[bottom:top]`` and never
    iterates the whole collection. Recording the slice proves a huge
    ``page_size`` did not ask for every row.
    """

    def __init__(self, length: int):
        self._length = length
        self.slices: list[slice] = []

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, key):
        if not isinstance(key, slice):
            raise TypeError("Paginator pages via slices")
        self.slices.append(key)
        start = 0 if key.start is None else key.start
        stop = self._length if key.stop is None else key.stop
        start = max(0, min(start, self._length))
        stop = max(start, min(stop, self._length))
        return list(range(start, stop))


def test_huge_page_size_is_clamped_to_max_and_does_not_return_whole_queryset():
    items = _SliceRecordingSequence(250)

    page, meta = paginate_queryset(items, _request(page_size="100000000"))

    assert meta["page_size"] == MAX_PAGE_SIZE
    assert meta["total_count"] == 250
    assert meta["total_pages"] == 3
    assert list(page) == list(range(MAX_PAGE_SIZE))
    assert len(page) == MAX_PAGE_SIZE

    assert items.slices
    page_slice = items.slices[-1]
    start = 0 if page_slice.start is None else page_slice.start
    stop = items._length if page_slice.stop is None else page_slice.stop
    assert stop - start == MAX_PAGE_SIZE


def test_page_size_at_max_is_not_reduced():
    items = list(range(250))
    page, meta = paginate_queryset(items, _request(page_size="100"))

    assert meta["page_size"] == MAX_PAGE_SIZE
    assert list(page) == list(range(MAX_PAGE_SIZE))


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
    # Only PageNotAnInteger maps to page 1; see the below-1 case for contrast.
    page, meta = paginate_queryset(ITEMS, _request(page_number="abc", page_size="10"))
    assert meta["page_number"] == 1
    assert list(page) == list(range(0, 10))


def test_below_one_page_number_returns_last_page():
    # A page number below 1 raises EmptyPage (not PageNotAnInteger), which
    # get_page clamps to num_pages -- i.e. the LAST page, not the first.
    page, meta = paginate_queryset(ITEMS, _request(page_number="0", page_size="10"))
    assert meta["page_number"] == 3
    assert list(page) == list(range(20, 25))


def test_non_numeric_page_size_falls_back_to_default():
    page, meta = paginate_queryset(ITEMS, _request(page_size="abc"))
    assert list(page) == ITEMS[:DEFAULT_PAGE_SIZE]
    assert meta["page_size"] == DEFAULT_PAGE_SIZE


def test_zero_page_size_falls_back_to_default():
    # Paginator(queryset, 0) raises ZeroDivisionError computing num_pages.
    page, meta = paginate_queryset(ITEMS, _request(page_size="0"))
    assert list(page) == ITEMS[:DEFAULT_PAGE_SIZE]
    assert meta["page_size"] == DEFAULT_PAGE_SIZE
    assert meta["total_count"] == 25
