"""Readiness of Falcon's on-demand trace worker, not interactive Falcon chat."""

from typing import NoReturn


class BackgroundAnalysisUnavailable(RuntimeError):
    code = "FALCON_BACKGROUND_ACCOUNTING_UNAVAILABLE"


def require_background_analysis_ready() -> NoReturn:
    # This is a source hold, not an entitlement or deployment toggle. A future
    # worker must check allowance per trace and record actual usage, including
    # the explorer's separate provider call. Readiness itself must not bill.
    raise BackgroundAnalysisUnavailable(
        "Background multi-trace analysis is unavailable right now. "
        "No analysis was started and no results will appear on the Feed. "
        "Tell the user this explicitly; do not promise background results."
    )
