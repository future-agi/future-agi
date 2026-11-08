import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Checkbox, FormControlLabel, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
import PageHeader from 'src/components/page-header';
import StatusBadge from 'src/components/status-badge';
import Section from 'src/components/section';
import { ConfirmDialog } from 'src/components/custom-dialog';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import ContextCampaignTargetDrawer from './ContextCampaignTargetDrawer';

export default function ContextCampaignReview({ campaign, targets, onAuthorize, onStatus, onRollback, pending = false, selectedTarget, onSelectTarget, observations, rollback }) {
  const [basis, setBasis] = useState(() => ({ revision: campaign.revision, digest: campaign.manifestDigest }));
  const [decisions, setDecisions] = useState({});
  const [acknowledged, setAcknowledged] = useState(false);
  const [error, setError] = useState('');
  const [confirm, setConfirm] = useState(null);
  const [reason, setReason] = useState('');
  const stale = basis.revision !== campaign.revision || basis.digest !== campaign.manifestDigest;
  const complete = targets.length === campaign.targetCount;
  const editable = campaign.enabled && campaign.canAuthorize && campaign.status === 'draft' && !stale && !pending;
  const chosen = (target) => decisions[target.bindingId] || { bindingId: target.bindingId, planDigest: target.planDigest, decision: 'pending', reason: '' };
  const ready = complete && targets.every((target) => {
    const decision = chosen(target);
    if (decision.planDigest !== target.planDigest) return false;
    if (decision.decision === 'exclude') return Boolean(decision.reason.trim());
    return decision.decision === 'include' && target.repository && target.plan && !target.plan.blockers.length && (!target.selector.version || target.selector.version === target.desiredVersion) && (!target.plan.localReviewRequired || Boolean(decision.reason.trim()));
  });
  const selected = targets.find((target) => target.bindingId === selectedTarget);
  const run = async (operation) => {
    setError('');
    try { await operation(); setConfirm(null); }
    catch { setError('The operation was not confirmed. Your review is preserved. Refresh the receipt before retrying an unknown outcome; changed plans require a new review.'); }
  };
  const reset = () => {
    setBasis({ revision: campaign.revision, digest: campaign.manifestDigest });
    setDecisions({}); setAcknowledged(false); setError('');
  };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title={campaign.name} description="Authorize only these exact patches. The local executor creates PRs using your GitHub access; it never merges them." badge={<StatusBadge status={campaign.status} />} />
    <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>Revision {campaign.revision} · {campaign.targetCount} fixed targets · {campaign.manifestDigest || 'Waiting for local plans'}</Typography>
    {!campaign.enabled && <Alert severity="info">Campaign execution is disabled. No repository writes can be authorized or claimed.</Alert>}
    {!campaign.canAuthorize && <Alert severity="info">A current workspace administrator must authorize these repository writes.</Alert>}
    {!complete && <Alert severity="warning">The complete target inventory has not loaded. Authorization is unavailable.</Alert>}
    {stale && <Alert severity="warning" action={<Button color="inherit" size="small" onClick={reset}>Review changed state</Button>}>This review is stale. No preconditions were refreshed automatically.</Alert>}
    {error && <Alert severity="error" role="alert">{error}</Alert>}
    <TableContainer><Table size="small" aria-label="Fixed rollout targets">
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Repository / target</TableCell><TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>Plan / exception</TableCell><TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>Git</TableCell><TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>Decision</TableCell><TableCell>Inspect</TableCell></TableRow></TableHead>
      <TableBody>{targets.map((target) => <TableRow key={target.bindingId} hover sx={{ height: ROW_HEIGHT.default }}>
        <TableCell sx={{ overflowWrap: 'anywhere' }}><Typography variant="s1">{target.name}</Typography><Typography component="div" variant="s3" color="text.secondary">v{target.desiredVersion} · {target.selector.version ? `Pin v${target.selector.version}` : `Follow ${target.selector.label}`}</Typography><Box sx={{ display: { xs: 'block', md: 'none' }, mt: 0.5 }}><StatusBadge status={target.prState} /><Typography component="div" variant="s3">{campaign.status === 'draft' ? chosen(target).decision : target.decision} · {target.plan ? `${target.plan.changes.length} file changes` : 'No plan'}</Typography>{target.plan?.localReviewRequired && <Typography component="div" variant="s3">Review local instructions</Typography>}{target.plan?.blockers.map((blocker) => <Typography component="div" variant="s3" key={blocker}>{blocker}</Typography>)}</Box></TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}><Typography variant="s3">{target.plan ? `${target.plan.changes.length} file changes` : 'No plan'}</Typography>{target.plan?.localReviewRequired && <Typography component="div" variant="s3">Local instructions need review</Typography>}{target.plan?.blockers.map((blocker) => <Typography component="div" variant="s3" key={blocker}>{blocker}</Typography>)}</TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}><StatusBadge status={target.prState} /></TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}><Typography variant="s1">{campaign.status === 'draft' ? chosen(target).decision : target.decision}</Typography></TableCell>
        <TableCell><Button size="small" onClick={() => onSelectTarget(target.bindingId)} aria-label={`Review target ${target.name}`}>Inspect target</Button></TableCell>
      </TableRow>)}</TableBody>
    </Table></TableContainer>
    <Alert severity="info">PR open → merged → installed → delivered are independent observations. Unobserved consumers remain unknown. A rollout does not advance deliberate pins.</Alert>
    {campaign.status === 'draft' && <Section title="Repository-write authorization">
      <FormControlLabel control={<Checkbox checked={acknowledged} disabled={!editable} onChange={(event) => setAcknowledged(event.target.checked)} />} label="I reviewed every included patch and authorize only the listed repository writes, not merge or deployment." />
      <Button variant="contained" disabled={!editable || !ready || !acknowledged} onClick={() => setConfirm('authorize')}>Authorize reviewed PR plans</Button>
    </Section>}
    <TextField label="Reason for campaign change" size="small" fullWidth value={reason} onChange={(event) => setReason(event.target.value)} inputProps={{ maxLength: 500 }} helperText="Required before pausing, resuming or cancelling. Existing PRs are never changed." />
    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
      {['authorized', 'running'].includes(campaign.status) && <Button disabled={!campaign.canAuthorize || pending} onClick={() => setConfirm('pause')}>Pause new claims</Button>}
      {campaign.status === 'paused' && <Button disabled={!campaign.canAuthorize || !campaign.enabled || pending} onClick={() => setConfirm('resume')}>Resume unchanged plans</Button>}
      {!['cancelled', 'settled'].includes(campaign.status) && <Button disabled={!campaign.canAuthorize || pending} onClick={() => setConfirm('cancel')}>Cancel unstarted work</Button>}
      <Button disabled={pending || !campaign.manifestDigest} onClick={() => run(() => onRollback({ revision: campaign.revision }))}>Preview reverse changes</Button>
    </Stack>
    {rollback && <Section title="Reverse-plan review" description="This preview is not executable. Reinspect each repository, verify the retained previous lock and create a new campaign linked to this one.">
      {rollback.targets.map((target) => <Box key={target.bindingId} sx={{ py: 1 }}><Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{target.bindingId} · {target.reverseChanges.length} reverse changes · {target.previousLockDigest || 'No previous lock recorded'}</Typography><Typography component="div" variant="s3">{target.blockers.join(', ')}</Typography></Box>)}
    </Section>}
    <ContextCampaignTargetDrawer target={selected} open={Boolean(selected)} onClose={() => onSelectTarget(null)}
      decision={selected ? (campaign.status === 'draft' ? chosen(selected) : { decision: selected.decision, reason: selected.reason }) : null}
      onDecision={(decision) => setDecisions((old) => ({ ...old, [selected.bindingId]: decision }))} editable={editable} observations={observations} />
    <ConfirmDialog open={Boolean(confirm)} onClose={() => setConfirm(null)} title={confirm === 'authorize' ? 'Authorize exact repository patches?' : `${confirm || 'Change'} campaign?`}
      content={confirm === 'authorize' ? 'Only included plans in this reviewed manifest may create pull requests. This does not authorize merges or deployments.' : 'In-flight Git requests may still complete. Existing branches, PRs and receipts are retained.'}
      action={<Button variant="contained" disabled={pending || (confirm === 'authorize' ? (!editable || !ready || !acknowledged) : !reason.trim())} onClick={() => run(() => confirm === 'authorize' ? onAuthorize({ revision: basis.revision, expectedManifestDigest: basis.digest, decisions: targets.map(chosen), acknowledgeRepositoryWrites: true }) : onStatus({ action: confirm, revision: campaign.revision, reason }))}>{confirm === 'authorize' ? 'Authorize exact plans' : 'Confirm change'}</Button>}
    />
  </Stack>;
}
ContextCampaignReview.propTypes = {
  campaign: PropTypes.object.isRequired, targets: PropTypes.array.isRequired,
  onAuthorize: PropTypes.func.isRequired, onStatus: PropTypes.func.isRequired, onRollback: PropTypes.func.isRequired,
  pending: PropTypes.bool, selectedTarget: PropTypes.string, onSelectTarget: PropTypes.func.isRequired,
  observations: PropTypes.object, rollback: PropTypes.object,
};
