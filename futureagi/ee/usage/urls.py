"""Shippable EE URL patterns.

Cloud billing/admin URLs have moved to ee.cloud.urls.
This module remains for self-hosted EE endpoints (license status, capabilities)
and the one billing ledger's routes under ``ledger/v1/`` (usage-event-v1
ingest and billing-ledger-read-v1, phase 2a: balance, entries, usage,
invoices and the workspace reads). Mounted at ``usage/`` (tfc/urls.py).
"""

from django.urls import path

from ee.usage.views.ledger_ingest import UsageEventIngestView
from ee.usage.views.ledger_read import (
    BalanceView,
    EntriesView,
    InvoicesView,
    UsageView,
    WorkspaceEntriesView,
    WorkspaceUsageView,
)

urlpatterns: list[path] = [
    path("ledger/v1/events/", UsageEventIngestView.as_view(), name="ledger-v1-events"),
    path("ledger/v1/balance/", BalanceView.as_view(), name="ledger-v1-balance"),
    path("ledger/v1/entries/", EntriesView.as_view(), name="ledger-v1-entries"),
    path("ledger/v1/usage/", UsageView.as_view(), name="ledger-v1-usage"),
    path("ledger/v1/invoices/", InvoicesView.as_view(), name="ledger-v1-invoices"),
    path(
        "ledger/v1/workspaces/<str:workspace_id>/usage/",
        WorkspaceUsageView.as_view(),
        name="ledger-v1-workspace-usage",
    ),
    path(
        "ledger/v1/workspaces/<str:workspace_id>/entries/",
        WorkspaceEntriesView.as_view(),
        name="ledger-v1-workspace-entries",
    ),
]
