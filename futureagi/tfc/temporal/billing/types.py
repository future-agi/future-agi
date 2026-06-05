"""Dataclass types for billing Temporal activities.

Separated from activities to avoid Django imports in workflow sandbox.
"""

from dataclasses import dataclass


@dataclass
class DunningCheckInput:
    """Input for the daily dunning checks activity."""

    pass


@dataclass
class DunningCheckOutput:
    """Output from the daily dunning checks activity."""

    orgs_processed: int = 0
    status: str = "COMPLETED"


@dataclass
class MonthlyInvoiceInput:
    """Input for the monthly invoice generation activity."""

    period: str = ""  # YYYY-MM. Empty = previous month.
    org_id: str = ""  # Empty = all paid orgs.


@dataclass
class MonthlyInvoiceOutput:
    """Output from the monthly invoice generation activity."""

    invoices_created: int = 0
    invoices_skipped: int = 0
    errors: int = 0
    status: str = "COMPLETED"


@dataclass
class MonthlyClosingInput:
    period: str = ""  # YYYY-MM being CLOSED. Empty = previous month from now.


@dataclass
class MonthlyClosingOutput:
    """Output of the monthly closing activity.

    The closing flushes Redis usage for ``closed_period`` and bills an
    invoice for ``period`` (= closed_period + 1 month) — advance fee +
    arrears usage. Both are surfaced so callers can't confuse them.
    """

    period: str = ""  # YYYY-MM billed (closed_period + 1).
    closed_period: str = ""  # YYYY-MM whose Redis usage was flushed.
    invoices_created: int = 0
    invoices_skipped: int = 0
    errors: int = 0
    status: str = "COMPLETED"
