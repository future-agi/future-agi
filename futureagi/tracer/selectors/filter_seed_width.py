"""Row-budgeted adaptive slice widths for bounded candidate-seed acquisition.

A seed whose statement cost tracks the *rows* its slice contains cannot be
bounded by a fixed wall-clock ceiling: a dense hour and a sparse week differ by
two orders of magnitude in read rows, so any single hour count is either a
useless cap on the dense end or a scan that never reaches data on the sparse
end. Such a builder declares a policy instead, and the bounded filter selector
resizes each following slice from the rows the previous seed statement actually
read.

This moves only the acquisition boundary. Predicates, ordering, the exact
latest-state classifier, publication and the signed cursor payload are
untouched; slices stay contiguous and half-open, so a narrower slice defers
older history to the next adjacent slice rather than skipping it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

_HOUR = timedelta(hours=1)


def _snap_whole_hour_power_of_two(width: timedelta, *, round_up: bool) -> timedelta:
    """Snap a width of an hour or more onto the whole-hour power-of-two lattice.

    Widths below an hour are returned unchanged: the sub-hour lattice is the
    halving sequence (30m, 15m) and has no whole-hour representation. Callers
    round up only a width that is already a lower bound, and down only a width
    that is already an upper bound, so neither direction loses its guarantee.
    """

    if width <= _HOUR:
        return width
    hours = 1
    while (hours * 2) * _HOUR <= width:
        hours *= 2
    if round_up and hours * _HOUR < width:
        hours *= 2
    return hours * _HOUR


@dataclass(frozen=True)
class FilterSeedWidthPolicy:
    """The row budget one seed lane admits per statement, and its width lattice.

    ``target_read_rows`` is the rows a single seed statement should read.
    ``initial_width`` is the width of the first statement of a read, before any
    measurement exists. ``unsignalled_cap`` is the only ceiling that applies
    while no statement of this read has reported read rows — a transport
    without native progress, or the first slice of a read.

    ``min_width`` is the width this lane refuses to narrow below. It is a
    declaration, not a derived quantity: a seed whose child witness is
    time-unbounded pays a flat per-statement term that does not shrink with
    the slice, so below some width a statement pays the same cost for a
    fraction of the coverage, and only the declaring lane knows where that is.
    Each lane must therefore state its own floor. Halving applies to every
    width above the floor — a slice that grew on sparse history and then ran
    into dense data walks back down to it — and a width already *below* the
    floor is left alone rather than lifted to it, because a sub-floor width is
    always something another mechanism proved necessary (a keyset resume, a
    read-budget retry, a root-time-discovery hour).

    Widths at or above an hour are whole-hour powers of two (1h, 2h, 4h, ...),
    which is the granularity the ``toStartOfHour`` primary-key prefix can prune
    on; the request-width clamp is deliberately exempt, because the oldest
    slice is truncated at the request start in any case.
    """

    initial_width: timedelta
    target_read_rows: int
    min_width: timedelta
    unsignalled_cap: timedelta = timedelta(hours=4)

    def __post_init__(self) -> None:
        if self.target_read_rows <= 0:
            raise ValueError("seed width policy requires a positive row target")
        if not (
            timedelta(0) < self.min_width <= self.initial_width <= self.unsignalled_cap
        ):
            raise ValueError("seed width policy widths must be positive and ordered")

    def next_width(
        self,
        width: timedelta,
        read_rows: int | None,
        *,
        request_width: timedelta,
    ) -> timedelta:
        """Return the next slice width from the last seed statement's read rows.

        A statement that read less than a quarter of the budget doubles, one
        that overran the budget halves, and anything between leaves the width
        alone so a correctly sized scan does not oscillate. A statement that
        reported no progress cannot justify more than the unsignalled cap.

        The halving branch only ever narrows. A width at or below the floor is
        returned as it stands (clamped to the request), never lifted to the
        floor: widths below the floor are reached by mechanisms that proved
        that exact interval necessary — a cursor keyset resuming at a
        microsecond boundary, the read-budget retry's five-minute reset, the
        single hour a root-time-discovery hit pins — and widening one of those
        because its statement was expensive would be the wrong direction.
        """

        if read_rows is None:
            return min(
                _snap_whole_hour_power_of_two(width * 2, round_up=True),
                self.unsignalled_cap,
            )
        if read_rows < self.target_read_rows // 4:
            return min(
                _snap_whole_hour_power_of_two(width * 2, round_up=True),
                request_width,
            )
        if read_rows > self.target_read_rows:
            if width <= self.min_width:
                return min(width, request_width)
            return max(
                _snap_whole_hour_power_of_two(width / 2, round_up=False),
                self.min_width,
            )
        return width

    def unsignalled_width(self, width: timedelta) -> timedelta:
        """Clamp a width proposed before any statement reported its read rows.

        The selector's non-cursor lane inflates the first slice so the whole
        request window is scheduled inside one request's attempt count. That
        inflation is a lower bound and may land off the lattice, so it rounds
        up before the cap applies.
        """

        return min(
            _snap_whole_hour_power_of_two(width, round_up=True),
            self.unsignalled_cap,
        )

    def carried_width(self, width: timedelta) -> timedelta:
        """Clamp a slice width carried in from a previous HTTP hop.

        A carried width is a hint from a read that has measured nothing in this
        request, so it may shrink but never widen: a cursor signed before this
        policy shipped can carry a slice many times the unsignalled cap.
        """

        return min(width, self.unsignalled_cap)
