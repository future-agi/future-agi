"""Admin Custom Tools for staff: who may open the pages, and what they render.

The API boundary behind the pages is covered in the future-agi/ee repo
(``ee/cloud/tests/test_admin_custom_tools_access.py``); everything here
exercises only this repo's views and templates.
"""

import re
import uuid

import pytest
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory

from accounts.models.user import User
from ee.usage.admin import (
    _custom_tools_read_only,
    custom_pricing_view,
    generate_invoice_view,
)

# Inputs that stay in the DOM on the read-only page (their values are shown)
# but must not look editable.
FEE_INPUT_IDS = (
    "platform-fee",
    "platform-fee-billing-cycle",
    "contract-end-date",
    "start-date",
)


def _user(organization, label, **flags):
    return User.objects.create(
        email=f"{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        name=label.title(),
        organization=organization,
        **flags,
    )


@pytest.fixture
def staff(db, organization):
    return _user(organization, "staff", is_staff=True, is_superuser=False)


@pytest.fixture
def superuser(db, organization):
    return _user(organization, "root", is_staff=True, is_superuser=True)


@pytest.fixture
def non_staff(db, organization):
    return _user(organization, "customer", is_staff=False)


def _request(user):
    request = RequestFactory().get("/admin/usage/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _render(view, user):
    response = view(_request(user))
    if hasattr(response, "render"):
        response.render()
    return response


def _control_tag(html, element_id):
    match = re.search(rf'<(?:input|select)[^>]*\bid="{element_id}"[^>]*>', html)
    assert match, f"{element_id} not rendered"
    return match.group(0)


@pytest.mark.django_db
class TestCustomToolsReadOnlyMode:
    def test_staff_gets_read_only(self, staff):
        assert _custom_tools_read_only(_request(staff)) is True

    def test_superuser_gets_full_access(self, superuser):
        assert _custom_tools_read_only(_request(superuser)) is False

    def test_non_staff_is_refused(self, non_staff):
        assert _custom_tools_read_only(_request(non_staff)) is None

    def test_inactive_staff_is_refused(self, organization):
        gone = _user(organization, "gone", is_staff=True, is_active=False)
        assert _custom_tools_read_only(_request(gone)) is None

    def test_pages_return_403_for_refused_users(self, non_staff):
        assert custom_pricing_view(_request(non_staff)).status_code == 403
        assert generate_invoice_view(_request(non_staff)).status_code == 403


@pytest.mark.django_db
class TestCustomPricingPage:
    def test_staff_page_has_no_editing_controls(self, staff):
        response = _render(custom_pricing_view, staff)
        html = response.content.decode()

        assert response.status_code == 200
        assert response.context_data["read_only"] is True
        assert 'id="read-only-banner"' in html
        assert "const READ_ONLY = true;" in html
        assert 'id="submit-btn"' not in html
        assert 'onclick="addTierLocal()"' not in html
        assert 'onclick="addEntitlementLocal()"' not in html
        assert 'id="new-ent-feature"' not in html

    def test_staff_page_fee_inputs_are_disabled(self, staff):
        html = _render(custom_pricing_view, staff).content.decode()
        for element_id in FEE_INPUT_IDS:
            assert " disabled" in _control_tag(html, element_id), element_id

    def test_staff_page_startup_script_tolerates_missing_feature_select(self, staff):
        # The dropdown-population IIFE runs on load; without this guard the
        # read-only page threw before the fee-preview listeners attached.
        html = _render(custom_pricing_view, staff).content.decode()
        assert re.search(
            r'const fSel = document\.getElementById\("new-ent-feature"\);\s*if \(fSel\)',
            html,
        )

    def test_superuser_page_has_editing_controls(self, superuser):
        response = _render(custom_pricing_view, superuser)
        html = response.content.decode()

        assert response.context_data["read_only"] is False
        assert 'id="read-only-banner"' not in html
        assert 'id="submit-btn"' in html
        assert 'id="new-ent-feature"' in html
        for element_id in FEE_INPUT_IDS:
            assert " disabled" not in _control_tag(html, element_id), element_id


@pytest.mark.django_db
class TestGenerateInvoicePage:
    def test_staff_page_keeps_preview_but_not_generate(self, staff):
        response = _render(generate_invoice_view, staff)
        html = response.content.decode()

        assert response.status_code == 200
        assert response.context_data["read_only"] is True
        assert 'id="read-only-banner"' in html
        assert 'id="preview-btn"' in html
        assert 'id="generate-btn"' not in html

    def test_superuser_page_has_generate(self, superuser):
        html = _render(generate_invoice_view, superuser).content.decode()
        assert 'id="generate-btn"' in html
        assert 'id="read-only-banner"' not in html


@pytest.mark.django_db
class TestAdminIndexShowsCustomTools:
    def _index(self, user):
        client = Client()
        client.force_login(user)
        return client.get("/admin/").content.decode()

    def test_staff_sees_read_only_section(self, staff):
        html = self._index(staff)
        assert "Custom Tools (read only)" in html
        assert "/admin/usage/custom-pricing/" in html
        assert "/admin/usage/generate-invoice/" in html

    def test_superuser_sees_full_section(self, superuser):
        html = self._index(superuser)
        assert "Custom Tools" in html
        assert "(read only)" not in html
