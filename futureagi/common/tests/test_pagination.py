"""Tests for common.utils.pagination.paginate_queryset."""

from types import SimpleNamespace

from common.utils.pagination import MAX_PAGE_SIZE, paginate_queryset


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
