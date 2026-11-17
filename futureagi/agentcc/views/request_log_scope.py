"""Which request logs a console request may read.

The Logs view (``agentcc/views/request_log.py``) and the gateway analytics
(``agentcc/views/analytics.py``) both read ``AgentccRequestLog`` through
``OrganizationRequestLogScopeMixin``, so the calls a user sees in Logs and the
numbers on their Analytics page, a key's usage and the Budgets summary come
from the same rows.
"""

from tfc.middleware.workspace_context import workspace_scope_is_empty
from tfc.utils.base_viewset import (
    BaseModelViewSetMixinWithUserOrg,
    active_request_organization,
)


class OrganizationRequestLogScopeMixin(BaseModelViewSetMixinWithUserOrg):
    """The request logs a request may read.

    Ingestion writes an organization's request logs with workspace=NULL, so
    the shared mixin's selected-workspace scope hides them whenever another
    workspace than the default one is selected. An active Owner or Admin of
    the request's organization (global workspace access) reads every
    non-deleted log of that organization whichever workspace is selected.

    Everyone else keeps the shared mixin's scoping exactly, with no widening:
    a workspace-scoped member, and every request that mixin refuses (an
    authenticated empty scope, a bound session with no workspace, no
    organization, an inactive membership, an anonymous user).

    It is a ``BaseModelViewSetMixinWithUserOrg`` so that the fallback is
    always that mixin's scoping, never DRF's unscoped ``get_queryset``.
    """

    def _get_authorized_queryset(self):
        request = getattr(self, "request", None)
        organization = active_request_organization(request)
        user = getattr(request, "user", None)
        workspace = getattr(request, "workspace", None)
        if (
            workspace is not None
            and not workspace_scope_is_empty()
            and organization is not None
            and workspace.organization_id == organization.pk
            and user is not None
            and getattr(user, "is_authenticated", False)
            and user.can_access_organization(organization)
            and user.has_global_workspace_access(organization)
        ):
            # Ingestion writes organization logs with workspace=NULL. Selecting
            # a workspace must not hide them from active global org roles.
            return self.queryset.all().filter(organization=organization, deleted=False)
        # Keep restricted-member authorization and the authenticated-empty /
        # bound-session-without-workspace guards in the shared mixin.
        return super().get_queryset()
