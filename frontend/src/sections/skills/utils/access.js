import { useSkillV11Scope } from 'src/api/skills/testing';
import { useOrganization } from 'src/contexts/OrganizationContext';
import { useWorkspace } from 'src/contexts/WorkspaceContext';

// Role levels from tfc/constants/levels.py (organization and workspace share one scale).
export const SKILLS_LEVEL = { viewer: 1, member: 3, admin: 8 };

/**
 * Pure rule behind useSkillsAccess, exported for tests.
 * The role only counts when the selected organization and workspace are the
 * ones the Skills scope is using (the same rule the allowance page used), and
 * at least one level has loaded. Until then nothing is decided: canWrite and
 * isViewer are both false, so neither write actions nor viewer notices flash.
 */
export function skillsAccess({ scope, organization, workspace }) {
  const sameScope = Boolean(organization?.isReady && workspace?.isReady
    && scope?.organizationId && scope?.workspaceId
    && organization.currentOrganizationId === scope.organizationId
    && workspace.currentWorkspaceId === scope.workspaceId);
  const known = organization?.orgLevel != null || workspace?.wsLevel != null;
  const ready = sameScope && known;
  const level = ready ? Math.max(Number(organization.orgLevel) || 0, Number(workspace.wsLevel) || 0) : 0;
  return {
    sameScope,
    ready,
    level,
    isViewer: ready && level < SKILLS_LEVEL.member,
    canWrite: ready && level >= SKILLS_LEVEL.member,
    canAdmin: ready && level >= SKILLS_LEVEL.admin,
  };
}

/**
 * Presentation-only role signal for Skills screens: hide create destinations
 * and explain disabled record actions for viewers. The server remains the
 * authority on every request; this never grants anything.
 */
export function useSkillsAccess() {
  const access = useSkillV11Scope();
  const organization = useOrganization();
  const workspace = useWorkspace();
  return skillsAccess({ scope: access.scope, organization, workspace });
}
