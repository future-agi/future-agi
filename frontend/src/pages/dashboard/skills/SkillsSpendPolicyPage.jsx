import PropTypes from 'prop-types';
import { Helmet } from 'react-helmet-async';
import { useSearchParams } from 'react-router-dom';
import { Stack } from '@mui/material';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { spendPolicyReady, useSkillsSpendPolicy, useUpdateSkillsSpendPolicy } from 'src/api/skills/r3-spend';
import { useSkillsAccess } from 'src/sections/skills/utils/access';
import { paths } from 'src/routes/paths';
import ObjectHeader from 'src/sections/skills/components/ObjectHeader';
import { skillsRouteSx } from 'src/sections/skills/components/routeGutter';
import SpendPolicyPanel from 'src/sections/skills/review/SpendPolicyPanel';

function ScopedAllowance({ scopeKey, canManage }) {
  const query = useSkillsSpendPolicy();
  const update = useUpdateSkillsSpendPolicy();
  const [params] = useSearchParams();
  const requested = params.get('state');
  const state = ['loading', 'error', 'empty', 'denied'].includes(requested) ? requested
    : query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'empty';
  return <SpendPolicyPanel scopeKey={scopeKey} state={state} policy={query.data} canManage={canManage}
    onRetry={query.refetch} onRefresh={query.refetch} onSave={update.mutateAsync} />;
}
ScopedAllowance.propTypes = { scopeKey: PropTypes.string.isRequired, canManage: PropTypes.bool.isRequired };

export default function SkillsSpendPolicyPage() {
  const access = useSkillV11Scope();
  // The selected organization/workspace role, only when it belongs to the
  // Skills scope; the service rechecks effective membership on every PUT.
  const { sameScope, canAdmin } = useSkillsAccess();
  return <Stack sx={skillsRouteSx} spacing={1.5}>
    <Helmet><title>Workspace allowance · Skills</title></Helmet>
    <ObjectHeader crumbs={[{ label: 'Skills', to: paths.dashboard.skills.root }, { label: 'Workspace allowance' }]} title="Workspace allowance" purpose="A monthly limit for paid test runs in this workspace." />
    {access.enabled && sameScope && spendPolicyReady()
      ? <ScopedAllowance key={access.identity} scopeKey={access.identity} canManage={canAdmin} />
      : <SpendPolicyPanel scopeKey={access.enabled ? access.identity : undefined} state="unavailable" />}
  </Stack>;
}
