import PropTypes from 'prop-types';
import { Helmet } from 'react-helmet-async';
import { useSearchParams } from 'react-router-dom';
import { Stack } from '@mui/material';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { spendPolicyReady, useSkillsSpendPolicy, useUpdateSkillsSpendPolicy } from 'src/api/skills/r3-spend';
import { useOrganization } from 'src/contexts/OrganizationContext';
import { useWorkspace } from 'src/contexts/WorkspaceContext';
import PageHeader from 'src/components/page-header';
import SpendPolicyPanel from 'src/sections/skills/review/SpendPolicyPanel';

function ScopedAllowance({ scopeKey, canManage }) {
  const query = useSkillsSpendPolicy();
  const update = useUpdateSkillsSpendPolicy();
  const [params] = useSearchParams();
  const requested = params.get('state');
  const state = ['loading', 'error', 'empty'].includes(requested) ? requested
    : query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'empty';
  return <SpendPolicyPanel scopeKey={scopeKey} state={state} policy={query.data} canManage={canManage}
    onRetry={query.refetch} onRefresh={query.refetch} onSave={update.mutateAsync} />;
}
ScopedAllowance.propTypes = { scopeKey: PropTypes.string.isRequired, canManage: PropTypes.bool.isRequired };

export default function SkillsSpendPolicyPage() {
  const access = useSkillV11Scope();
  const organization = useOrganization();
  const workspace = useWorkspace();
  // These are the currently selected scope contexts, not a default-workspace
  // role from user-info. The service rechecks effective membership on every PUT.
  const sameScope = organization.isReady && workspace.isReady
    && organization.currentOrganizationId === access.scope.organizationId
    && workspace.currentWorkspaceId === access.scope.workspaceId;
  const canManage = Boolean(sameScope && Math.max(organization.orgLevel || 0, workspace.wsLevel || 0) >= 8);
  return <Stack sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }} spacing={1.5}>
    <Helmet><title>Workspace allowance · Skills</title></Helmet>
    <PageHeader title="Workspace allowance" description="An administrator authorizes spending explicitly. No amount is enabled by default." />
    {access.enabled && sameScope && spendPolicyReady()
      ? <ScopedAllowance key={access.identity} scopeKey={access.identity} canManage={canManage} />
      : <SpendPolicyPanel scopeKey={access.enabled ? access.identity : undefined} state="unavailable" />}
  </Stack>;
}
