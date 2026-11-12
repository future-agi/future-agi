"""Managed-credit console routes, under /agentcc/managed-credits/.

Balance, entries and CSV, statement, checkout, settings (Owner or Admin), and
the staff grant, adjust and refund. Included ahead of the router in
agentcc/urls.py.
"""

from django.urls import path

from agentcc.views.auto_reload import ManagedCreditAutoReloadTriggerView
from agentcc.views.managed_credits import (
    ManagedCreditAdjustView,
    ManagedCreditCheckoutStatusView,
    ManagedCreditCheckoutView,
    ManagedCreditEntriesView,
    ManagedCreditGrantView,
    ManagedCreditRefundView,
    ManagedCreditSettingsView,
    ManagedCreditStatementView,
    ManagedCreditSummaryView,
)

PREFIX = "managed-credits/"

urlpatterns = [
    path(PREFIX, ManagedCreditSummaryView.as_view(), name="agentcc-managed-credits"),
    path(
        f"{PREFIX}entries/",
        ManagedCreditEntriesView.as_view(),
        name="agentcc-managed-credit-entries",
    ),
    path(
        f"{PREFIX}statement/",
        ManagedCreditStatementView.as_view(),
        name="agentcc-managed-credit-statement",
    ),
    path(
        f"{PREFIX}checkout/",
        ManagedCreditCheckoutView.as_view(),
        name="agentcc-managed-credit-checkout",
    ),
    path(
        f"{PREFIX}checkout/<str:session_id>/",
        ManagedCreditCheckoutStatusView.as_view(),
        name="agentcc-managed-credit-checkout-status",
    ),
    path(
        f"{PREFIX}auto-reload/trigger/",
        ManagedCreditAutoReloadTriggerView.as_view(),
        name="agentcc-managed-credit-auto-reload-trigger",
    ),
    path(
        f"{PREFIX}settings/",
        ManagedCreditSettingsView.as_view(),
        name="agentcc-managed-credit-settings",
    ),
    path(
        f"{PREFIX}grant/",
        ManagedCreditGrantView.as_view(),
        name="agentcc-managed-credit-grant",
    ),
    path(
        f"{PREFIX}adjust/",
        ManagedCreditAdjustView.as_view(),
        name="agentcc-managed-credit-adjust",
    ),
    path(
        f"{PREFIX}refund/",
        ManagedCreditRefundView.as_view(),
        name="agentcc-managed-credit-refund",
    ),
]
