"""Safety contracts shared by eval-task validation and row resolution."""

# Exact historical span/trace resolution keeps every selected identity plus a
# latest-state de-duplication set until the requested prefix is proven.  Keep
# the public task contract aligned with that bounded in-process state; allowing
# a larger limit would route the task back to a whole-window ClickHouse stream.
MAX_BOUNDED_HISTORICAL_SPAN_TRACE_ROWS = 100_000
