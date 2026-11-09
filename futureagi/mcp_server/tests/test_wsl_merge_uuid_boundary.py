"""N07: UUID validation must refuse before the workspace ORM boundary."""

from unittest.mock import patch
from uuid import UUID

import pytest
from django.core.exceptions import ValidationError

from accounts.models.workspace import Workspace
from accounts.tests.test_workspace_landing import landing as _landing_fixture
from mcp_server import mcp_app
from mcp_server.tests.test_wsl_merge_binding import clean_context as _clean_context
from mcp_server.tests.test_wsl_merge_binding import oauth

landing = _landing_fixture
clean_context = _clean_context


@pytest.mark.parametrize("value", ["", "not-a-workspace-id"])
def test_invalid_oauth_uuid_is_refused_before_workspace_query(landing, value):
    manager = Workspace.no_workspace_objects
    with patch.object(manager, "filter", wraps=manager.filter) as query:
        # Let the REAL ORM run if a mutant removes the parser. Record its error
        # rather than allowing that error to stand in for an assertion-level kill.
        result, raised = None, None
        try:
            result = oauth(landing.user, org_id=str(landing.org.pk), workspace_id=value)
        except (ValidationError, ValueError, TypeError, AttributeError) as error:
            raised = error
        assert query.call_count == 0, "invalid UUID reached the workspace ORM"
        assert raised is None, "invalid UUID escaped the authentication boundary"
        assert result is None
        assert mcp_app._current_context() is None

        # Same real manager and endpoint, with a legitimate explicitly selected
        # workspace: prove interception without replacing any access decision.
        allowed = oauth(
            landing.user,
            org_id=str(landing.org.pk),
            workspace_id=str(landing.default.pk),
        )
        query.assert_called_once()
        assert query.call_args.kwargs == {
            "pk": UUID(str(landing.default.pk)),
            "organization": landing.org,
            "is_active": True,
        }
        assert allowed.workspace == landing.default
        assert mcp_app._current_context() is allowed
