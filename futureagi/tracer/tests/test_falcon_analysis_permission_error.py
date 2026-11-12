"""An accessible viewer workspace is not an unavailable workspace."""

import pytest

from accounts.models.workspace import WorkspaceMembership
from accounts.tests.test_workspace_landing import landing as landing
from accounts.tests.workspace_write_support import scope_no_egress as scope_no_egress
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tracer.tasks import error_analysis
from tracer.tests.test_falcon_analysis_principal import analysis_case as analysis_case
from tracer.tests.test_falcon_analysis_principal import principal

pytestmark = pytest.mark.django_db(transaction=True)


def test_analysis_viewer_gets_write_permission_denial(analysis_case):
    case = analysis_case
    WorkspaceMembership._base_manager.filter(
        user=case.landing.user, workspace=case.landing.default
    ).update(role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER)
    result = error_analysis._falcon_analyze_single_trace(
        case.trace_id, str(case.project.pk), **principal(case)
    )
    assert not result["success"]
    assert result["code"] == "permission_denied", result
    assert "Write access denied" in result["error"], result
    case.factory.assert_not_called()
    case.run.assert_not_awaited()
