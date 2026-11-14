import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Checkbox, FormControlLabel, Link, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
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
import ActionReason, { reasonProps } from '../components/ActionReason';
import RolloutInstructions from './RolloutInstructions';
import RolloutsOffNotice from './RolloutsOffNotice';
import { ROLLOUTS_OFF_REASON } from './rolloutCopy';
import ContextCampaignTargetDrawer from './ContextCampaignTargetDrawer';

const decisionLabels = { pending: 'Not decided', include: 'Included', exclude: 'Excluded' };
const fileCount = (plan) => plan ? `${plan.changes.length} file${plan.changes.length === 1 ? '' : 's'} changed` : 'No plan';

export default function ContextCampaignReview({ campaign, targets, onAuthorize, onStatus, onRollback, pending = false, selectedTarget, onSelectTarget, observations, rollback, access }) {
  const cancelReasonId = useId();
  const authorizeReasonId = useId();
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
  const blockedLabel = (target) => !target.repository ? 'Blocked: repository missing' : !target.plan ? 'Blocked: no plan' : target.plan.blockers.length ? (target.plan.blockers.some((reason) => /local/i.test(reason)) ? 'Blocked: local changes' : 'Blocked: plan needs attention') : 'Blocked: pinned version';
  const needsReview = (target) => campaign.status === 'draft' ? !reviewedTarget(target) : target.decision === 'pending';
  const visible = targets.filter((target) => target.name.toLowerCase().includes(search.trim().toLowerCase()) && (filter === 'all' || (filter === 'pending' && needsReview(target)) || (filter === 'blocked' && blocked(target)) || (filter === 'excluded' && decisionFor(target) === 'exclude')));
  const outstanding = targets.filter(needsReview);
  const missingPlans = targets.filter((target) => !target.plan).length;
  const blockedCount = targets.filter(blocked).length;
  const planVersion = new Set(targets.map((target) => target.desiredVersion)).size === 1 ? targets[0]?.desiredVersion : undefined;
  const conclusion = !complete ? 'Waiting for the complete repository inventory. Nothing can be authorized yet.'
    : !campaign.enabled ? 'Repository rollouts are off. Existing records stay readable.'
      : campaign.status === 'settled' ? 'Repository work has ended. Check each pull request and app delivery report before calling the rollout complete.'
        : campaign.status === 'cancelled' ? 'Unstarted work is cancelled. Existing pull requests and receipts are kept.'
          : campaign.status === 'paused' ? 'New repository work is paused. Review the reason before resuming unchanged plans.'
            : campaign.status === 'authorized' ? 'The reviewed plans are authorized. Open each repository to follow pull request progress.'
              : campaign.status === 'running' ? 'Repository work is running. Open each repository to check its pull request and delivery reports.'
                : missingPlans === targets.length && targets.length > 0 ? `All ${targets.length} ${targets.length === 1 ? 'repository is' : 'repositories are'} blocked: no local plan yet. Run the plan command in each repository, then review it here.`
                  : `${outstanding.length} ${outstanding.length === 1 ? 'repository needs' : 'repositories need'} review${blockedCount ? `; ${blockedCount} blocked` : ''}. Open each repository to review its changes.`;
  // Off wins: while rollouts are off nothing can be authorized, so "review every target" would be a false next step.
  const authorizeReason = !campaign.enabled ? ROLLOUTS_OFF_REASON : !ready ? 'Review every target to continue.' : !acknowledged ? 'Tick the acknowledgment to continue.' : '';
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
    <Typography variant="s1" role="status">{conclusion}</Typography>
    {campaign.enabled && campaign.status === 'draft' && missingPlans > 0 && <RolloutInstructions version={planVersion} />}
    <Typography variant="s1">{campaign.targetCount} targets · {outstanding.length} need review · <Link component="button" type="button" variant="s1" color="inherit" onClick={() => setFilter('blocked')}>{targets.filter(blocked).length} blocked</Link> · {targets.filter((target) => decisionFor(target) === 'exclude').length} excluded</Typography>
    <TechnicalDetails title="Rollout details"><Typography variant="s3">Revision {campaign.revision}</Typography>{campaign.manifestDigest ? <IdentityChip label="Inventory fingerprint" value={campaign.manifestDigest} /> : <Typography variant="s3">Waiting for local plans</Typography>}</TechnicalDetails>
    {!campaign.enabled && <RolloutsOffNotice access={access} lead="New repository changes can't be started." />}
    {!campaign.canAuthorize && <Alert severity="info">A current workspace administrator must authorize these repository writes.</Alert>}
    {!complete && <Alert severity="warning">The complete target inventory has not loaded. Authorization is unavailable.</Alert>}
    {stale && <Alert severity="warning" action={<Button color="inherit" size="small" onClick={reset}>Review changed state</Button>}>This review is stale. No preconditions were refreshed automatically.</Alert>}
    {error && <Alert severity="error" role="alert">{error}</Alert>}
    <Box sx={{ bgcolor: 'background.default', borderBottom: 1, borderColor: 'divider', py: 1 }}>
    {campaign.status === 'draft' && <Section title="Authorize pull requests"><Stack spacing={1} alignItems="flex-start">
      <Typography variant="s3">Reviewed {targets.length - outstanding.length} of {campaign.targetCount}</Typography>
      <FormControlLabel control={<Checkbox checked={acknowledged} disabled={!editable} onChange={(event) => setAcknowledged(event.target.checked)} />} label="I reviewed every included patch and authorize only the listed repository writes, not merge or deployment." />
      <Button color="primary" variant="contained" disabled={!editable || !ready || !acknowledged} {...reasonProps(authorizeReasonId, authorizeReason)} onClick={() => setConfirm('authorize')}>Authorize pull requests</Button>
      <ActionReason id={authorizeReasonId} reason={authorizeReason} />
    </Stack></Section>}
    </Box>
    <ListToolbar sx={{ flexWrap: 'wrap', gap: 1 }}>
      <FormSearchField label="Search repositories" searchQuery={search} onChange={(event) => setSearch(event.target.value)} />
      <TextField select size="small" label="Target status" value={filter} onChange={(event) => setFilter(event.target.value)}>
        <MenuItem value="all">All targets</MenuItem><MenuItem value="pending">Needs review</MenuItem><MenuItem value="blocked">Blocked</MenuItem><MenuItem value="excluded">Excluded</MenuItem>
      </TextField>
    </ListToolbar>
    {!visible.length && <EmptyState variant="no-results" title="No targets match these filters" onClearFilters={() => { setSearch(''); setFilter('all'); }} />}
    <TableContainer><Table size="small" aria-label="Fixed rollout targets">
      <TableHead sx={{ display: { xs: 'none', md: 'table-header-group' } }}><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Repository / target</TableCell><TableCell>Changes</TableCell><TableCell>Pull request</TableCell><TableCell>Decision</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
      <TableBody>{visible.map((target) => <TableRow key={target.bindingId} hover onClick={() => onSelectTarget(target.bindingId)} sx={{ cursor: 'pointer', height: { xs: 'auto', md: ROW_HEIGHT.default }, display: { xs: 'grid', md: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr) auto', py: { xs: 1, md: 0 }, '& > td': { borderBottom: { xs: 0, md: undefined } } }}>
        <TableCell sx={{ gridColumn: '1 / -1', wordBreak: 'normal', overflowWrap: 'break-word' }}><Typography variant="s1">{target.name.split(/(?<=[/-])/).map((part, i) => <Box key={i} component="span">{part}<wbr /></Box>)}</Typography><Typography component="div" variant="s3" color="text.secondary">v{target.desiredVersion} · {target.selector.version ? `Pin v${target.selector.version}` : `Follow ${target.selector.label}`}</Typography></TableCell>
        <TableCell sx={{ gridColumn: '1 / -1' }}><Typography variant="s3">{fileCount(target.plan)}</Typography>{target.plan?.localReviewRequired && <Typography component="div" variant="s3">Local instructions need review</Typography>}{target.plan?.blockers.map((blocker) => <Typography component="div" variant="s3" key={blocker}>{blocker}</Typography>)}</TableCell>
        <TableCell sx={{ display: { xs: 'none', md: 'table-cell' } }}><StatusBadge status={target.prState} /></TableCell>
        <TableCell><Stack spacing={0.5} alignItems="flex-start">{blocked(target) && <StatusBadge status="warning" label={blockedLabel(target)} />}{(!blocked(target) || decisionFor(target) !== 'pending') && <StatusBadge status="draft" label={decisionLabels[decisionFor(target)] || 'Not decided'} />}</Stack></TableCell>
        <TableCell><Button variant="outlined" size="small" onClick={(event) => { event.stopPropagation(); onSelectTarget(target.bindingId); }} aria-label={`Review target ${target.name}`}>Open</Button></TableCell>
      </TableRow>)}</TableBody>
    </Table></TableContainer>
    <Typography variant="s3" color="text.secondary">A merged pull request does not mean apps are updated. Check their delivery reports; pinned versions stay unchanged.</Typography><TechnicalDetails title="Delivery details"><Typography variant="s3">PR open → merged → installed → delivered are independent observations. Unobserved consumers remain unknown. A rollout does not advance deliberate pins.</Typography></TechnicalDetails>
    <TextField label="Reason for rollout change" size="small" fullWidth value={reason} onChange={(event) => setReason(event.target.value)} inputProps={{ maxLength: 500 }} helperText="Required before pausing, resuming or cancelling. Existing PRs are never changed." />
    <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
      {['authorized', 'running'].includes(campaign.status) && <Button disabled={!campaign.canAuthorize || pending} onClick={() => setConfirm('pause')}>Pause new claims</Button>}
      {campaign.status === 'paused' && <Button disabled={!campaign.canAuthorize || !campaign.enabled || pending} onClick={() => setConfirm('resume')}>Resume unchanged plans</Button>}
      {!['cancelled', 'settled'].includes(campaign.status) && <Button disabled={!campaign.canAuthorize || pending || !reason.trim()} {...reasonProps(cancelReasonId, !reason.trim() && 'Enter a reason to cancel')} onClick={() => setConfirm('cancel')}>Cancel unstarted work</Button>}
      <Button disabled={pending || !campaign.manifestDigest} onClick={() => run(() => onRollback({ revision: campaign.revision }))}>Preview reverse changes</Button>
    </Stack>
    {!['cancelled', 'settled'].includes(campaign.status) && <ActionReason id={cancelReasonId} reason={!reason.trim() && 'Enter a reason to cancel'} />}
    {rollback && <Section title="Reverse-plan review" description="This preview is not executable. Reinspect each repository, verify the retained previous lock and create a new rollout linked to this one.">
      {rollback.targets.map((target) => <Box key={target.bindingId} sx={{ py: 1 }}><Typography variant="s3">{targets.find((row) => row.bindingId === target.bindingId)?.name || 'Repository target'} · {target.reverseChanges.length} reverse changes</Typography><TechnicalDetails><IdentityChip label="Target ID" kind="id" value={target.bindingId} /><IdentityChip label="Previous lock" value={target.previousLockDigest || 'No previous lock recorded'} /></TechnicalDetails><Typography component="div" variant="s3">{target.blockers.join(', ')}</Typography></Box>)}
    </Section>}
    <ContextCampaignTargetDrawer target={selected} open={Boolean(selected)} onClose={() => onSelectTarget(null)}
      decision={selected ? (campaign.status === 'draft' ? chosen(selected) : { decision: selected.decision, reason: selected.reason }) : null}
      onDecision={(decision) => setDecisions((old) => ({ ...old, [selected.bindingId]: decision }))} editable={editable} observations={observations} readOnly={!campaign.enabled} onNext={nextTarget ? () => onSelectTarget(nextTarget.bindingId) : undefined} />
    <ConfirmDialog open={Boolean(confirm)} onClose={() => setConfirm(null)} title={confirm === 'authorize' ? 'Authorize exact repository patches?' : `${confirm || 'Change'} rollout?`}
      content={confirm === 'authorize' ? 'Only included plans in this reviewed manifest may create pull requests. This does not authorize merges or deployments.' : 'In-flight Git requests may still complete. Existing branches, PRs and receipts are retained.'}
      action={<Button color="primary" variant="contained" disabled={pending || (confirm === 'authorize' ? (!editable || !ready || !acknowledged) : !reason.trim())} onClick={() => run(() => confirm === 'authorize' ? onAuthorize({ revision: basis.revision, expectedManifestDigest: basis.digest, decisions: targets.map(chosen), acknowledgeRepositoryWrites: true }) : onStatus({ action: confirm, revision: campaign.revision, reason }))}>{confirm === 'authorize' ? 'Authorize exact plans' : 'Confirm change'}</Button>}
    />
  </Stack>;
}
ContextCampaignReview.propTypes = {
  campaign: PropTypes.object.isRequired, targets: PropTypes.array.isRequired,
  onAuthorize: PropTypes.func.isRequired, onStatus: PropTypes.func.isRequired, onRollback: PropTypes.func.isRequired,
  pending: PropTypes.bool, selectedTarget: PropTypes.string, onSelectTarget: PropTypes.func.isRequired,
  observations: PropTypes.object, rollback: PropTypes.object,
  access: PropTypes.shape({ ready: PropTypes.bool, canAdmin: PropTypes.bool }),
};
