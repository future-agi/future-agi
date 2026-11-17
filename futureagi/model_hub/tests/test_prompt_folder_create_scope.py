"""Prompt-folder create answers a caller with no organization before the body.

``PromptFolderViewSet.create`` (both lineages) refuses a caller whose request
resolved no organization with 404 "Organization not found" before it reads
the request body, so a parent folder of any other organization gets the same
answer as an unknown one. The organization authentication set (``None``
here) is final: the account's legacy ``organization`` field does not widen it.
"""

import uuid

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

import model_hub.views.prompt_folder  # noqa: F401  (imported with no workspace bound)
from accounts.models.organization import Organization
from accounts.models.user import User
from tfc.middleware.workspace_context import clear_workspace_context


def _create(organization, body):
    from model_hub.views.prompt_folder import PromptFolderViewSet

    request = APIRequestFactory().post(
        "/model-hub/prompt-folders/", body, content_type="application/json"
    )
    request.organization = organization
    request.workspace = None
    force_authenticate(
        request,
        user=User(
            email="removed@example.invalid",
            organization=Organization(id=uuid.uuid4()),
        ),
    )
    clear_workspace_context()
    try:
        return PromptFolderViewSet.as_view({"post": "create"})(request)
    finally:
        clear_workspace_context()


@pytest.mark.django_db
def test_create_without_an_organization_is_404_before_the_body_is_read():
    # Not JSON: reading the body would answer 400.
    unread = _create(None, b"{not json")

    assert unread.status_code == 404, unread.data
    assert "Organization not found" in str(unread.data)


@pytest.mark.django_db
def test_create_without_an_organization_answers_any_parent_like_an_unknown_one():
    named = _create(
        None,
        b'{"name": "folder", "parent_folder": "%s"}' % str(uuid.uuid4()).encode(),
    )
    unknown = _create(None, b'{"name": "folder", "parent_folder": null}')

    assert named.status_code == unknown.status_code == 404
    assert named.data == unknown.data


@pytest.mark.django_db
def test_create_with_an_organization_reads_the_body():
    # Control: with an organization the same body is read and refused as
    # unparseable, so the 404 above is the organization check.
    organization = Organization.objects.create(name="Prompt folder org")

    read = _create(organization, b"{not json")

    assert read.status_code == 400, read.data
