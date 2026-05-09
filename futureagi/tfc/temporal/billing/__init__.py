"""Temporal billing module — activities for Stripe usage reporting and dunning."""


def get_activities():
    """Lazy-load billing activities (imports Django)."""
    from tfc.temporal.billing.activities import (
        generate_monthly_invoices_activity,
        monthly_closing_activity,
        report_stripe_usage_activity,
        run_dunning_checks_activity,
    )

    return [
        report_stripe_usage_activity,
        run_dunning_checks_activity,
        generate_monthly_invoices_activity,
        monthly_closing_activity,
    ]
