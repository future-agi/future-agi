"""
Tests for the public shared-link widget-data endpoint.

`resolve_shared_widget_data` lets a viewer of a shared dashboard load live
widget data through a share-token-authorized backend call (issue #83,
acceptance criterion #6). These tests pin the authorization boundary:
the token — and only the token — gates access, and a token never unlocks
a widget outside the dashboard it shares.

The actual ClickHouse query execution is exercised by the existing
DashboardWidget endpoints; here `is_clickhouse_enabled` is patched off, so
a `400 "ClickHouse is not enabled"` means "authorization passed, reached
execution" — which is exactly what these tests assert.
"""

import uuid

import pytest

from tracer.models.dashboard import Dashboard, DashboardWidget
from tracer.models.shared_link import AccessType, SharedLink, SharedLinkAccess

WIDGET_DATA_URL = "/tracer/shared/{token}/widget/{widget_id}/data/"

# A minimal query_config that passes the "has metrics" guard.
_QUERY_CONFIG = {"metrics": [{"name": "count", "source": "traces"}]}


@pytest.fixture
def dashboard(db, workspace, user):
    """A dashboard owned by the test workspace."""
    return Dashboard.objects.create(
        workspace=workspace,
        name="Shared Dashboard",
        created_by=user,
    )


@pytest.fixture
def widget(db, dashboard, user):
    """A widget belonging to `dashboard` with a non-empty query_config."""
    return DashboardWidget.objects.create(
        dashboard=dashboard,
        name="Trace count",
        query_config=_QUERY_CONFIG,
        chart_config={"chart_type": "line"},
        created_by=user,
    )


@pytest.fixture
def public_link(db, dashboard, organization, workspace, user):
    """A public ('anyone with the link') share link for the dashboard."""
    return SharedLink.objects.create(
        resource_type="dashboard",
        resource_id=str(dashboard.id),
        access_type=AccessType.PUBLIC,
        created_by=user,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def restricted_link(db, dashboard, organization, workspace, user):
    """A restricted share link for the dashboard (empty ACL)."""
    return SharedLink.objects.create(
        resource_type="dashboard",
        resource_id=str(dashboard.id),
        access_type=AccessType.RESTRICTED,
        created_by=user,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def _no_clickhouse(monkeypatch):
    """Force `is_clickhouse_enabled` off so tests stop at the exec boundary.

    A 400 past this point means authorization succeeded — the assertion
    these tests care about — without needing a live ClickHouse.
    """
    monkeypatch.setattr(
        "tracer.services.clickhouse.client.is_clickhouse_enabled",
        lambda: False,
    )


class TestSharedWidgetDataAuthorization:
    """The token gates access; authorized requests reach query execution."""

    def test_public_link_reaches_query_execution(
        self, api_client, public_link, widget, _no_clickhouse
    ):
        """An unauthenticated viewer of a public link passes the auth wall."""
        resp = api_client.get(
            WIDGET_DATA_URL.format(token=public_link.token, widget_id=widget.id)
        )
        # Authorization passed — only the patched-off ClickHouse stops it.
        assert resp.status_code == 400
        assert "ClickHouse" in str(resp.data)

    def test_restricted_link_requires_authentication(
        self, api_client, restricted_link, widget
    ):
        """A restricted link rejects an unauthenticated viewer with 401."""
        resp = api_client.get(
            WIDGET_DATA_URL.format(
                token=restricted_link.token, widget_id=widget.id
            )
        )
        assert resp.status_code == 401

    def test_restricted_link_rejects_user_not_in_acl(
        self, auth_client, restricted_link, widget
    ):
        """An authenticated user absent from the ACL gets 403.

        `auth_client`'s user is not the link creator and not in access_list.
        """
        resp = auth_client.get(
            WIDGET_DATA_URL.format(
                token=restricted_link.token, widget_id=widget.id
            )
        )
        assert resp.status_code == 403

    def test_restricted_link_allows_user_in_acl(
        self, auth_client, user, restricted_link, widget, _no_clickhouse
    ):
        """A user whose email is in the ACL passes the auth wall."""
        SharedLinkAccess.objects.create(
            shared_link=restricted_link,
            email=user.email,
            user=user,
            granted_by=user,
        )
        resp = auth_client.get(
            WIDGET_DATA_URL.format(
                token=restricted_link.token, widget_id=widget.id
            )
        )
        assert resp.status_code == 400
        assert "ClickHouse" in str(resp.data)


class TestSharedWidgetDataBoundary:
    """A token must not unlock anything outside the dashboard it shares."""

    def test_widget_from_other_dashboard_is_rejected(
        self, api_client, public_link, organization, workspace, user
    ):
        """A widget_id belonging to a *different* dashboard returns 404.

        This is the core security boundary: the share token authorizes its
        own dashboard's widgets only — never an arbitrary widget id.
        """
        other_dashboard = Dashboard.objects.create(
            workspace=workspace, name="Other Dashboard", created_by=user
        )
        foreign_widget = DashboardWidget.objects.create(
            dashboard=other_dashboard,
            name="Foreign widget",
            query_config=_QUERY_CONFIG,
            created_by=user,
        )
        resp = api_client.get(
            WIDGET_DATA_URL.format(
                token=public_link.token, widget_id=foreign_widget.id
            )
        )
        assert resp.status_code == 404

    def test_unknown_widget_id_is_rejected(
        self, api_client, public_link
    ):
        """A widget id that does not exist returns 404."""
        resp = api_client.get(
            WIDGET_DATA_URL.format(
                token=public_link.token, widget_id=uuid.uuid4()
            )
        )
        assert resp.status_code == 404

    def test_unknown_token_is_rejected(self, api_client, widget):
        """An unknown share token returns 404 — never leaks the widget."""
        resp = api_client.get(
            WIDGET_DATA_URL.format(token="not-a-real-token", widget_id=widget.id)
        )
        assert resp.status_code == 404

    def test_revoked_link_is_gone(
        self, api_client, public_link, widget
    ):
        """A revoked link returns 410, not widget data."""
        public_link.is_active = False
        public_link.save(update_fields=["is_active"])
        resp = api_client.get(
            WIDGET_DATA_URL.format(token=public_link.token, widget_id=widget.id)
        )
        assert resp.status_code == 410

    def test_non_dashboard_link_is_rejected(
        self, api_client, organization, workspace, user, widget
    ):
        """A share link for a non-dashboard resource returns 400."""
        trace_link = SharedLink.objects.create(
            resource_type="trace",
            resource_id=str(uuid.uuid4()),
            access_type=AccessType.PUBLIC,
            created_by=user,
            organization=organization,
            workspace=workspace,
        )
        resp = api_client.get(
            WIDGET_DATA_URL.format(token=trace_link.token, widget_id=widget.id)
        )
        assert resp.status_code == 400
