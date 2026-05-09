"""Dataclass types for billing Temporal activities.

Separated from activities to avoid Django imports in workflow sandbox.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class StripeUsageReportInput:
    """Input for the hourly Stripe usage reporting activity."""

    period: str = field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m"))


@dataclass
class StripeUsageReportOutput:
    """Output from the Stripe usage reporting activity."""

    records_reported: int = 0
    status: str = "COMPLETED"


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
    period: str = ""  # YYYY-MM. Empty = previous month from now.


@dataclass
class MonthlyClosingOutput:
    period: str = ""
    invoices_created: int = 0
    invoices_skipped: int = 0
    errors: int = 0
    status: str = "COMPLETED"
