"""Rollout routes, mounted at `omega-rollout/` (tfc/urls.py)."""

from django.urls import path

from omega_rollout import admin_api as admin
from omega_rollout.early_access_api import EarlyAccessView

P = "admin/products/<str:product>"

app_name = "omega_rollout"

urlpatterns = [
    path("early-access/", EarlyAccessView.as_view(), name="early-access"),
    path("admin/products/", admin.ProductListView.as_view(), name="admin-products"),
    path(f"{P}/", admin.ProductDetailView.as_view(), name="admin-product"),
    path(f"{P}/kill/", admin.KillView.as_view(), name="admin-kill"),
    path(f"{P}/restore/", admin.RestoreView.as_view(), name="admin-restore"),
    path(f"{P}/access/", admin.AccessView.as_view(), name="admin-access"),
    path(
        f"{P}/access/remove/",
        admin.AccessRemoveView.as_view(),
        name="admin-access-remove",
    ),
    path("admin/waitlist/", admin.WaitlistView.as_view(), name="admin-waitlist"),
    path(
        "admin/waitlist/<uuid:request_id>/decision/",
        admin.WaitlistDecisionView.as_view(),
        name="admin-waitlist-decision",
    ),
    path("admin/audit/", admin.AuditView.as_view(), name="admin-audit"),
]
