import { useState } from 'react';
import { Helmet } from 'react-helmet-async';
import PropTypes from 'prop-types';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Stack, TextField, Typography } from '@mui/material';
import ModalWrapper from 'src/components/ModalWrapper/ModalWrapper';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import PageHeader from 'src/components/page-header';
import { useSkillV11Scope } from 'src/api/skills/testing';
import {
  campaignsContractReady, useAuthorizeContextCampaign, useChangeContextCampaignStatus,
  useContextCampaign, useContextCampaigns, useContextCampaignTargets,
  useContextCampaignObservations, usePreviewContextCampaignRollback,
  useContextCampaignAvailability, useCreateContextCampaign,
} from 'src/api/skills/context-campaigns';
import CapabilityNotice from 'src/sections/skills/components/CapabilityNotice';
import { useSkillsAccess } from 'src/sections/skills/utils/access';
import IdentityChip from 'src/sections/skills/components/IdentityChip';
import ContextCampaignList from 'src/sections/skills/contexts/ContextCampaignList';
import ContextCampaignReview from 'src/sections/skills/contexts/ContextCampaignReview';
import RolloutsOffNotice from 'src/sections/skills/contexts/RolloutsOffNotice';

function CreateInventory({ inventory, submission, onSubmission, onClose, onCreated }) {
  const [name, setName] = useState(submission?.name || '');
  const create = useCreateContextCampaign();
  const valid = inventory.targets.length > 0 && inventory.targets.length <= 100 && new Set(inventory.targets.map((row) => row.bindingId)).size === inventory.targets.length;
  const submit = async () => {
    const input = submission || { name: name.trim(), targets: inventory.targets, parentCampaignId: inventory.parentCampaignId || null, idempotencyKey: crypto.randomUUID() };
    onSubmission(input);
    try { const result = await create.mutateAsync(input); onCreated(result.id); }
    catch { /* The original input and operation key remain available for retry. */ }
  };
  return <ModalWrapper open onClose={onClose} title="Review fixed repository inventory" actionBtnTitle={submission ? 'Recover original submission' : 'Create draft rollout'} onSubmit={submit} isValid={valid && Boolean(name.trim())} isLoading={create.isPending}>
    <TextField label="Rollout name" value={name} disabled={Boolean(submission)} onChange={(event) => setName(event.target.value)} inputProps={{ maxLength: 120 }} size="small" />
    <Alert severity="info">This creates a draft with exactly {inventory.targets.length} targets. Each repository still needs a local plan and explicit authorization before a PR can be created.</Alert>
    <Stack spacing={1} sx={{ maxHeight: 320, overflow: 'auto' }}>{inventory.targets.map((target) => <Stack key={target.bindingId} direction="row" spacing={1} alignItems="center"><Typography variant="s3">v{target.version}</Typography><IdentityChip label="Target ID" kind="id" value={target.bindingId} /></Stack>)}</Stack>
    {create.isError && <Alert severity="error">Creation was not confirmed. Retry this original submission to recover its receipt; do not create a new rollout with a fresh key.</Alert>}
  </ModalWrapper>;
}
CreateInventory.propTypes = { inventory: PropTypes.object.isRequired, submission: PropTypes.object, onSubmission: PropTypes.func.isRequired, onClose: PropTypes.func.isRequired, onCreated: PropTypes.func.isRequired };

function CampaignRoute() {
  const { campaignId } = useParams();
  const access = useSkillV11Scope();
  const role = useSkillsAccess();
  const location = useLocation();
  const [creating, setCreating] = useState(false);
  const availability = useContextCampaignAvailability();
  const inventory = location.state?.campaignInventory?.scopeIdentity === access.identity ? location.state.campaignInventory : null;
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const state = params.get('state');
  const [page, setPage] = useState(1);
  const [selectedTarget, selectTarget] = useState(null);
  const list = useContextCampaigns({ page });
  const detail = useContextCampaign(campaignId);
  // The bound is 100. Load the exact complete set once, not a partial first-page
  // approval. Server decisions still enforce exact membership and revision CAS.
  const targets = useContextCampaignTargets(campaignId, { pageSize: 100 });
  const observations = useContextCampaignObservations(campaignId, selectedTarget);
  const authorize = useAuthorizeContextCampaign(campaignId);
  const status = useChangeContextCampaignStatus(campaignId);
  const rollback = usePreviewContextCampaignRollback(campaignId);
  const back = (id) => navigate(`/dashboard/skills/contexts/rollouts/${id}`);
  if (!campaignId) return <>
    <ContextCampaignList access={role} query={list} availability={availability} enabled={availability.data?.enabled === true} state={state} onOpen={back} page={page} onPageChange={setPage} onCreate={inventory ? () => setCreating(true) : undefined} />
    {creating && inventory && availability.data?.enabled === true && !availability.isError && <CreateInventory inventory={inventory} submission={location.state?.campaignSubmission}
      onSubmission={(input) => navigate(location.pathname, { replace: true, state: { ...location.state, campaignSubmission: input } })}
      onClose={() => setCreating(false)} onCreated={back} />}
  </>;
  if (state === 'loading' || detail.isLoading || targets.isLoading) return <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" rows={6} label="Loading rollout review" />;
  if (['denied', 'forbidden'].includes(state) || detail.error?.statusCode === 403 || targets.error?.statusCode === 403) return <CapabilityNotice title="Rollout access" description="You don't have access to repository rollouts. Ask a workspace admin." />;
  if (state === 'error' || detail.isError || targets.isError) return <ErrorState title="Couldn't load rollouts." onRetry={() => { detail.refetch(); targets.refetch(); }} />;
  if (!detail.data || !targets.data) return <Alert severity="info">Select an authenticated workspace to inspect this rollout.</Alert>;
  return <ContextCampaignReview access={role} campaign={detail.data} targets={targets.data.results}
    onAuthorize={authorize.mutateAsync} onStatus={status.mutateAsync} onRollback={rollback.mutateAsync}
    pending={authorize.isPending || status.isPending || rollback.isPending}
    selectedTarget={selectedTarget} onSelectTarget={selectTarget} observations={observations} rollback={rollback.data} />;
}

export default function ContextCampaignPage() {
  const access = useSkillV11Scope();
  const role = useSkillsAccess();
  return <><Helmet><title>Repository rollouts · Skills</title></Helmet><Stack spacing={1.5} sx={{ minWidth: 0, px: { xs: 2, md: 3 }, py: 1.5 }}>
    {campaignsContractReady() ? <CampaignRoute key={access.identity} /> : <>
      <PageHeader title="Repository rollouts" description="Fixed repository plans with separate delivery observations." />
      <RolloutsOffNotice access={role} />
    </>}
  </Stack></>;
}
