"""Temporal billing module — activities for dunning, invoice gen, monthly closing."""


def get_activities():
    """Lazy-load billing activities (imports Django)."""
    from tfc.temporal.billing.activities import (
        generate_monthly_invoices_activity,
        monthly_closing_activity,
        run_dunning_checks_activity,
    )

    return [
        run_dunning_checks_activity,
        generate_monthly_invoices_activity,
        monthly_closing_activity,
    ]
