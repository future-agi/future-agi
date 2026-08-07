"""Temporal billing module — activities for invoice gen and monthly closing."""


def get_activities():
    """Lazy-load billing activities (imports Django)."""
    from tfc.temporal.billing.activities import (
        generate_monthly_invoices_activity,
        monthly_closing_activity,
    )

    return [
        generate_monthly_invoices_activity,
        monthly_closing_activity,
    ]
