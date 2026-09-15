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


class EmptyDensityEstimate:
    """The answer a density probe gives when it selected nothing at all.

    An index estimate that names no part is AMBIGUOUS and the ambiguity runs
    in the two opposite directions that matter here:

    * the key condition selected no part, so the candidate slice holds no rows
      - the sparse tail's answer, and reading it as unknown would pin the tail
      at the unprobed cap, which is the regression the row budget exists to
      remove;
    * the plan carried no readable step at all - a statement the server
      answered some other way, or a plan shape this lane cannot read - in
      which case the slice's population is simply UNKNOWN, and approving the
      widest proposal on it reinstates the very defect the probe was added to
      prevent.

    Nothing inside one result can tell those apart, so the reducer refuses to
    choose: it returns this marker and the caller decides, from what the rest
    of the request has already proven, whether a zero may be believed. This is
    deliberately the same conservative default the repo's other production
    ``EXPLAIN ESTIMATE`` consumer takes (``graph_dispatch`` treats an empty
    estimate as unusable and falls back); the difference is that this lane may
    ACCEPT the zero reading when a completed statement in the same request has
    independently shown the neighbouring, newer region to be empty.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "EMPTY_DENSITY_ESTIMATE"


EMPTY_DENSITY_ESTIMATE = EmptyDensityEstimate()


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
    measurement exists. ``unsignalled_cap`` is the ceiling that applies to any
    width this read cannot justify from a measurement: a transport without
    native progress, the first slice of a read, and — the reason it is also
    called the *unprobed* cap — any widening past it that no density probe has
    approved.

    THE WIDENING CONTRACT. Doubling is reactive: it fires on the rows the
    PREVIOUS slice read, and knows nothing about the population of the NEXT
    one. Across a sparse tail that is exactly right, and it is how a lane
    reaches old data in log(n) statements instead of n. But a tail that ends at
    a dense region ends the doubling on a slice whose measured predecessor was
    empty and whose own contents are not: eight doublings across a 166 h sparse
    tail land a 128 h slice on dense history, and that one statement read over
    12 GiB in production measurement. So a width ABOVE ``unsignalled_cap`` is
    not a width this policy may issue on the strength of the previous slice
    alone. The selector must first prove the candidate slice's row population
    with a cheap density probe and pass the count to ``probed_width``; a probe
    that fails, or a transport with no probe at all, gets the cap.

    The resulting contract is the one worth stating plainly: *a seed statement
    never reads more than ~``target_read_rows`` knowingly, and a sparse tail is
    crossed at probe cost, not at slice cost.* The probe reads the primary
    index and no column data at all, so its cost tracks the number of granules
    the slice spans rather than the rows inside them: a candidate reaching into
    dense history is refused for the price of reading its marks.

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

    @property
    def unprobed_cap(self) -> timedelta:
        """The widest slice this lane may issue without a density proof.

        The same width as ``unsignalled_cap`` and deliberately not a second
        knob: both answer one question — how wide may a statement be when this
        read has measured nothing about what is inside it? An unsignalled
        transport has measured nothing because it cannot report; an unprobed
        widening has measured only the slice next door.
        """

        return self.unsignalled_cap

    def requires_density_probe(self, width: timedelta) -> bool:
        """Whether issuing ``width`` needs a density proof first."""

        return width > self.unprobed_cap

    def probed_width(self, width: timedelta, slice_rows: int) -> timedelta:
        """Fit a probed candidate slice to the row budget.

        ``slice_rows`` is the probe's count of live rows inside the candidate
        slice ``[end - width, end)``. A count within budget issues the slice
        unchanged — this is the sparse-tail case, and it is why the tail is
        still crossed by doubling rather than one floor-width slice at a time.
        A count over budget shrinks the slice proportionally: rows are assumed
        uniform across the candidate, so the largest width whose share of the
        count fits is ``width * target / slice_rows``, snapped DOWN onto the
        lane's whole-hour power-of-two lattice and never below the floor.

        The proportional estimate is an estimate. It is wrong in both
        directions on a slice whose density is not uniform, which is precisely
        the shape that produced the defect — but it is wrong by the ratio of
        the densest part to the mean, not by the two orders of magnitude that
        separate a sparse week from a dense hour. A caller whose probe is cheap
        enough to spend twice may cost the fitted sub-slice itself through
        ``refined_width``; either way the next statement's own read rows
        correct what remains through the ordinary halving rule.
        """

        if slice_rows < 0:
            raise ValueError("a density probe cannot count negative rows")
        if slice_rows <= self.target_read_rows:
            return width
        fitted = width * (self.target_read_rows / slice_rows)
        return max(
            min(_snap_whole_hour_power_of_two(fitted, round_up=False), width),
            self.min_width,
        )

    def refined_width(self, width: timedelta, slice_rows: int) -> timedelta:
        """Fit a slice the proportional fit already produced, once.

        ``probed_width`` divides one count by one width, so it can only be as
        good as its uniformity assumption: on the shape that produced the
        defect the rows of a candidate are concentrated at its newer end, so
        the sub-slice the fit proposes is denser than the candidate's mean and
        still over budget. When a density estimate is cheap enough to spend
        twice, the honest repair is to ask about the sub-slice itself rather
        than to trust the average that produced it.

        This is the second and LAST question of one seed statement, so it does
        not re-fit proportionally - that would invite a third. A sub-slice
        still over budget is halved once, on the lane's whole-hour lattice and
        never below the floor, and the next statement's own read rows correct
        whatever remains through the ordinary halving rule.
        """

        if slice_rows < 0:
            raise ValueError("a density probe cannot count negative rows")
        if slice_rows <= self.target_read_rows:
            return width
        if width <= self.min_width:
            return width
        return max(
            _snap_whole_hour_power_of_two(width / 2, round_up=False),
            self.min_width,
        )

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
