"""Whether two rows of one organization lie in the same workspace scope.

An experiment snapshot must stay in its source dataset's scope. Older
snapshots of a dataset with no workspace were saved with the creating
request's workspace, which is always the organization's default workspace:
only the default workspace can see a dataset with no workspace
(``tfc.utils.tenant_scope.workspace_scope_q``), and an organization has one
default workspace. Such a pair is in one scope, the same way the managers
already treat a row with no workspace as the default workspace's.
"""


def same_workspace_scope(workspace_id, other_workspace_id, organization_id):
    """True when the two workspace ids name the same workspace scope.

    - Equal ids match, including two rows with no workspace. No query runs.
    - A row with no workspace also matches a row in ``organization_id``'s
      default workspace, in either order, while that workspace is live and
      active. This is the workspace the execution scope uses for a source
      with no workspace.
    - Nothing else matches: a non-default workspace, another organization's
      default workspace, an inactive, deleted or former default workspace,
      two different workspaces, or no organization.

    Callers still require both rows to belong to ``organization_id``.
    """
    if workspace_id == other_workspace_id:
        return True
    if organization_id is None or (workspace_id is None) == (
        other_workspace_id is None
    ):
        return False
    from accounts.models.workspace import Workspace

    stamped = workspace_id if workspace_id is not None else other_workspace_id
    return Workspace.no_workspace_objects.filter(
        pk=stamped,
        organization_id=organization_id,
        is_default=True,
        is_active=True,
    ).exists()
