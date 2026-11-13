import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
import ListToolbar from 'src/components/list-toolbar';
import FormSearchField from 'src/components/FormSearchField/FormSearchField';
import EmptyState from 'src/components/empty-state';
import ObjectHeader from '../components/ObjectHeader';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
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
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const stale = basis.revision !== campaign.revision || basis.digest !== campaign.manifestDigest;
  const complete = targets.length === campaign.targetCount;
  const editable = campaign.enabled && campaign.canAuthorize && campaign.status === 'draft' && !stale && !pending;
  const chosen = (target) => decisions[target.bindingId] || { bindingId: target.bindingId, planDigest: target.planDigest, decision: 'pending', reason: '' };
  const reviewedTarget = (target) => {
    const decision = chosen(target);
    if (decision.planDigest !== target.planDigest) return false;
    if (decision.decision === 'exclude') return Boolean(decision.reason.trim());
    return decision.decision === 'include' && target.repository && target.plan && !target.plan.blockers.length && (!target.selector.version || target.selector.version === target.desiredVersion) && (!target.plan.localReviewRequired || Boolean(decision.reason.trim()));
  };
  const ready = complete && targets.every(reviewedTarget);
  // Filtering is presentation only. Readiness and the payload always use the complete inventory.
  const decisionFor = (target) => campaign.status === 'draft' ? chosen(target).decision : target.decision;
  const blocked = (target) => !target.repository || !target.plan || target.plan.blockers.length > 0 || (target.selector.version && target.selector.version !== target.desiredVersion);
  const needsReview = (target) => campaign.status === 'draft' ? !reviewedTarget(target) : target.decision === 'pending';
  const visible = targets.filter((target) => target.name.toLowerCase().includes(search.trim().toLowerCase()) && (filter === 'all' || (filter === 'pending' && needsReview(target)) || (filter === 'blocked' && blocked(target)) || (filter === 'excluded' && decisionFor(target) === 'exclude')));
  const outstanding = targets.filter(needsReview);
  const nextTarget = outstanding.find((target) => target.bindingId !== selectedTarget);
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
    <ObjectHeader crumbs={[{ label: 'Repository rollouts', to: '/dashboard/skills/contexts/rollouts' }, { label: campaign.name }]} title={campaign.name} purpose={campaign.status === 'settled' ? 'Repository work finished · delivery tracked separately' : 'Review repository changes'} badge={<StatusBadge status={campaign.status} />} />
    <Typography variant="s1">{campaign.targetCount} targets · {outstanding.length} need review · {targets.filter(blocked).length} blocked · {targets.filter((target) => decisionFor(target) === 'exclude').length} excluded</Typography>
    <Stack direction="row" spacing={1} alignItems="center"><Typography variant="s3">Revision {campaign.revision}</Typography>{campaign.manifestDigest ? <IdentityChip label="Inventory fingerprint" value={campaign.manifestDigest} /> : <Typography variant="s3">Waiting for local plans</Typography>}</Stack>
    {!campaign.enabled && <Alert severity="info">Campaign execution is disabled. No repository writes can be authorized or claimed.</Alert>}
    {!campaign.canAuthorize && <Alert severity="info">A current workspace administrator must authorize these repository writes.</Alert>}
    {!complete && <Alert severity="warning">The complete target inventory has not loaded. Authorization is unavailable.</Alert>}
    {stale && <Alert severity="warning" action={<Button color="inherit" size="small" onClick={reset}>Review changed state</Button>}>This review is stale. No preconditions were refreshed automatically.</Alert>}
    {error && <Alert severity="error" role="alert">{error}</Alert>}
    <ListToolbar sx={{ flexWrap: 'wrap', gap: 1 }}>
      <FormSearchField label="Search repositories" searchQuery={search} onChange={(event) => setSearch(event.target.value)} />
      <TextField select size="small" label="Target status" value={filter} onChange={(event) => setFilter(event.target.value)}>
        <MenuItem value="all">All targets</MenuItem><MenuItem value="pending">Needs review</MenuItem><MenuItem value="blocked">Blocked</MenuItem><MenuItem value="excluded">Excluded</MenuItem>
      </TextField>
    </ListToolbar>
    <Box sx={{ position: 'sticky', top: 0, zIndex: 1, bgcolor: 'background.paper', borderBottom: 1, borderColor: 'divider', py: 1 }}>
    {campaign.status === 'draft' && <Section title="Repository-write authorization"><Stack spacing={1} alignItems="flex-start">
      <Typography variant="s3">{targets.length - outstanding.length} of {campaign.targetCount} reviewed</Typography>
      <FormControlLabel control={<Checkbox checked={acknowledged} disabled={!editable} onChange={(event) => setAcknowledged(event.target.checked)} />} label="I reviewed every included patch and authorize only the listed repository writes, not merge or deployment." />
      <Button variant="contained" disabled={!editable || !ready || !acknowledged} onClick={() => setConfirm('authorize')}>Authorize reviewed PR plans</Button>
    </Stack></Section>}
    </Box>
    {!visible.length && <EmptyState variant="no-results" title="No targets match these filters" onClearFilters={() => { setSearch(''); setFilter('all'); }} />}
    <TableContainer><Table size="small" aria-label="Fixed rollout targets">
      <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Repository / target</TableCell><TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>Plan / exception</TableCell><TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>Pull request</TableCell><TableCell>Delivery</TableCell><TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}>Decision</TableCell><TableCell>Inspect</TableCell></TableRow></TableHead>
      <TableBody>{visible.map((target) => <TableRow key={target.bindingId} hover sx={{ height: ROW_HEIGHT.default }}>
        <TableCell sx={{ overflowWrap: 'anywhere' }}><Typography variant="s1">{target.name}</Typography><Typography component="div" variant="s3" color="text.secondary">v{target.desiredVersion} · {target.selector.version ? `Pin v${target.selector.version}` : `Follow ${target.selector.label}`}</Typography><Box sx={{ display: { xs: 'block', md: 'none' }, mt: 0.5 }}><StatusBadge status={target.prState} /><Typography component="div" variant="s3">{campaign.status === 'draft' ? chosen(target).decision : target.decision} · {target.plan ? `${target.plan.changes.length} file changes` : 'No plan'}</Typography>{target.plan?.localReviewRequired && <Typography component="div" variant="s3">Review local instructions</Typography>}{target.plan?.blockers.map((blocker) => <Typography component="div" variant="s3" key={blocker}>{blocker}</Typography>)}</Box></TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}><Typography variant="s3">{target.plan ? `${target.plan.changes.length} file changes` : 'No plan'}</Typography>{target.plan?.localReviewRequired && <Typography component="div" variant="s3">Local instructions need review</Typography>}{target.plan?.blockers.map((blocker) => <Typography component="div" variant="s3" key={blocker}>{blocker}</Typography>)}</TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}><StatusBadge status={target.prState} /></TableCell>
        <TableCell><Typography variant="s3">Inspect observations</Typography></TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}><Typography variant="s1">{campaign.status === 'draft' ? chosen(target).decision : target.decision}</Typography></TableCell>
        <TableCell><Button size="small" onClick={() => onSelectTarget(target.bindingId)} aria-label={`Review target ${target.name}`}>Inspect target</Button></TableCell>
      </TableRow>)}</TableBody>
    </Table></TableContainer>
    <Alert severity="info">PR open → merged → installed → delivered are independent observations. Unobserved consumers remain unknown. A rollout does not advance deliberate pins.</Alert>
    <TextField label="Reason for campaign change" size="small" fullWidth value={reason} onChange={(event) => setReason(event.target.value)} inputProps={{ maxLength: 500 }} helperText="Required before pausing, resuming or cancelling. Existing PRs are never changed." />
    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
      {['authorized', 'running'].includes(campaign.status) && <Button disabled={!campaign.canAuthorize || pending} onClick={() => setConfirm('pause')}>Pause new claims</Button>}
      {campaign.status === 'paused' && <Button disabled={!campaign.canAuthorize || !campaign.enabled || pending} onClick={() => setConfirm('resume')}>Resume unchanged plans</Button>}
      {!['cancelled', 'settled'].includes(campaign.status) && <Button disabled={!campaign.canAuthorize || pending} onClick={() => setConfirm('cancel')}>Cancel unstarted work</Button>}
      <Button disabled={pending || !campaign.manifestDigest} onClick={() => run(() => onRollback({ revision: campaign.revision }))}>Preview reverse changes</Button>
    </Stack>
    {rollback && <Section title="Reverse-plan review" description="This preview is not executable. Reinspect each repository, verify the retained previous lock and create a new campaign linked to this one.">
      {rollback.targets.map((target) => <Box key={target.bindingId} sx={{ py: 1 }}><Typography variant="s3">{targets.find((row) => row.bindingId === target.bindingId)?.name || 'Repository target'} · {target.reverseChanges.length} reverse changes</Typography><TechnicalDetails><IdentityChip label="Target ID" kind="id" value={target.bindingId} /><IdentityChip label="Previous lock" value={target.previousLockDigest || 'No previous lock recorded'} /></TechnicalDetails><Typography component="div" variant="s3">{target.blockers.join(', ')}</Typography></Box>)}
    </Section>}
    <ContextCampaignTargetDrawer target={selected} open={Boolean(selected)} onClose={() => onSelectTarget(null)}
      decision={selected ? (campaign.status === 'draft' ? chosen(selected) : { decision: selected.decision, reason: selected.reason }) : null}
      onDecision={(decision) => setDecisions((old) => ({ ...old, [selected.bindingId]: decision }))} editable={editable} observations={observations} onNext={nextTarget ? () => onSelectTarget(nextTarget.bindingId) : undefined} />
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
