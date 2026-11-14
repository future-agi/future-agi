import { useId, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Drawer, IconButton, MenuItem, Stack, TextField, Typography } from '@mui/material';
import Iconify from 'src/components/iconify';
import { fDateTimeUTC } from 'src/utils/format-time';
import '../components/skillIcons';
import { humanizeCode } from './consumerPresentation';
import RolloutInstructions from './RolloutInstructions';
import { planCommand } from './rolloutCommands';
import CapabilityNotice from '../components/CapabilityNotice';
import Section from 'src/components/section';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import TextDiff from '../components/TextDiff';
import StatusBadge from 'src/components/status-badge';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import { coveredDrawerSx, useDrawerCovered } from 'src/hooks/use-drawer-stack';

const source = { overflowWrap: 'anywhere', whiteSpace: 'pre-wrap', m: 0 };

// Excerpts can be plain descriptions or unified diffs. Keep hunk headers visible
// and render each hunk separately so omitted lines never look like adjacent text.
function ChangeExcerpt({ text }) {
  const chunks = text.split(/(^@@[^\n]*@@[^\n]*$|^--- [^\n]*$|^\+\+\+ [^\n]*$)/m).filter(Boolean);
  return <Stack spacing={1}>{chunks.map((chunk, index) => {
    const lines = chunk.replace(/^\n|\n$/g, '').split('\n');
    if (!lines.some((line) => /^[+-]/.test(line)) || /^(?:@@|--- |\+\+\+ )/.test(chunk)) return <Typography key={index} component="pre" variant="s1" sx={source}>{chunk}</Typography>;
    const content = (line) => /^[ +-]/.test(line) ? line.slice(1) : line;
    return <TextDiff key={index} fromText={lines.filter((line) => !line.startsWith('+')).map(content).join('\n')} toText={lines.filter((line) => !line.startsWith('-')).map(content).join('\n')} fromTitle="Before (excerpt)" toTitle="After (excerpt)" />;
  })}</Stack>;
}
ChangeExcerpt.propTypes = { text: PropTypes.string.isRequired };
export default function ContextCampaignTargetDrawer({ target, open, onClose, decision, onDecision, editable = false, observations, onNext }) {
  const title = useId();
  const [copyState, setCopyState] = useState(null);
  const covered = useDrawerCovered(open);
  return <Drawer anchor="right" open={open} onClose={onClose} sx={coveredDrawerSx(covered)}
    PaperProps={{ role: 'dialog', 'aria-modal': true, 'aria-labelledby': title, sx: { width: { xs: 1, sm: 640 }, maxWidth: 1, overflow: 'hidden' } }}>
    <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1} sx={{ position: 'sticky', top: 0, zIndex: 1, bgcolor: 'background.paper', p: 2, borderBottom: 1, borderColor: 'divider', flexShrink: 0 }}>
      <Typography id={title} component="h2" variant="m1">{target?.name || 'Repository target'}</Typography>
      <IconButton aria-label="Close target" onClick={onClose}><Iconify icon="solar:close-linear" width={18} /></IconButton>
    </Stack>
    {target && <Stack spacing={2} sx={{ minWidth: 0, overflowY: 'auto', flex: 1, p: 2 }}>
      <CapabilityNotice title="Pull requests and delivery are separate" description="Pull request status comes from your Git client. A merged PR doesn't mean the app has the change." />
      <Section title="Target">
        <Typography component="p" variant="s1">Release v{target.desiredVersion} · {target.selector.version ? `Pinned v${target.selector.version}` : `Follows ${target.selector.label}`}</Typography>
        {target.repository ? <Typography component="p" variant="s3" sx={source}>Branch: {target.repository.defaultBranch} · {target.repository.projectScope ? `Folder: ${target.repository.projectScope}` : 'Root folder'}</Typography> : <Alert severity="warning">This target no longer names a repository. The old plan cannot authorize new writes.</Alert>}
      </Section>
      {!target.plan ? <Section title="No local plan yet" description="Plan this repository locally, submit the plan, then review the changes here."><RolloutInstructions version={target.desiredVersion} /></Section> : <>
        <Section title="Changes" description="Excerpts only. Files are checked again before anything is written.">
          {target.plan.changes.map((change) => <Box key={change.path} sx={{ borderBottom: 1, borderColor: 'divider', py: 1 }}>
            <Typography component="h3" variant="m3" sx={source}>{{ replace: 'Replaces', add: 'Adds', create: 'Creates', remove: 'Removes', delete: 'Deletes' }[change.action] || humanizeCode(change.action)} {change.path} ({change.ownership === 'managed' ? 'managed by Future AGI' : humanizeCode(change.ownership)})</Typography>
            <TechnicalDetails title="File fingerprints"><IdentityChip label="Before" value={change.beforeSha256 || 'absent'} /><IdentityChip label="After" value={change.afterSha256 || 'absent'} /></TechnicalDetails>
            <ChangeExcerpt text={change.diffPreview || ''} />
          </Box>)}
        </Section>
        {(target.plan.localReviewRequired || target.plan.blockers.length > 0) && <Section title="Needs attention">
          {target.plan.localReviewRequired && <Alert severity="warning">Review and preserve existing local instructions. Record that decision below; this is not a semantic merge.</Alert>}
          {target.plan.blockers.map((blocker) => <Alert severity="warning" key={blocker}>{blocker}</Alert>)}
        </Section>}
      </>}
      {decision && <Section title="Target decision">
        <TextField select fullWidth size="small" label="Include this target" value={decision.decision} disabled={!editable} onChange={(event) => onDecision({ ...decision, decision: event.target.value })}>
          <MenuItem value="pending">Not decided</MenuItem><MenuItem value="include" disabled={!target.repository || !target.plan || target.plan.blockers.length > 0 || (target.selector.version && target.selector.version !== target.desiredVersion)}>Include exact plan</MenuItem><MenuItem value="exclude">Exclude with reason</MenuItem>
        </TextField>
        <TextField fullWidth multiline minRows={2} size="small" label="Review or exclusion reason" value={decision.reason} disabled={!editable} onChange={(event) => onDecision({ ...decision, reason: event.target.value })} inputProps={{ maxLength: 500 }} sx={{ mt: 1 }} />
      </Section>}
      <TechnicalDetails title="Repository fingerprints">
        <IdentityChip value={target.desiredDigest} label="Release digest" /><Typography variant="s3">Repository ID {target.repository?.repositoryId ?? 'Not reported'}</Typography>
        <IdentityChip kind="id" label="Release ID" value={target.desiredVersionId} />
        {target.plan && <Stack spacing={1}><Typography variant="s3">Planned repository ID {target.plan.repositoryId}</Typography><IdentityChip label="Base commit" value={target.plan.baseCommit} /><IdentityChip label="Plan" value={target.planDigest} /><IdentityChip label="Previous lock" value={target.plan.previousLockDigest || 'None — cold adoption'} /><IdentityChip label="Materialization" value={target.plan.materializationDigest} /></Stack>}
      </TechnicalDetails>
      {/* Git and delivery observations stay visible: a merged PR is not delivery, and the
          reconcile warning must be seen before anyone retries. Only raw identifiers are disclosed. */}
      <Section title="Pull request">
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" alignItems="center"><StatusBadge status={target.prState} /><StatusBadge status={target.mergeState} label={target.mergeState === 'unknown' ? 'Merge status not reported' : humanizeCode(target.mergeState)} />{target.attempt && <Typography variant="s3" color="text.secondary">{target.attempt.receipt?.pullRequestNumber ? `PR #${target.attempt.receipt.pullRequestNumber}` : 'No PR number observed'}</Typography>}</Stack>
        {target.prState === 'unknown' && <Alert severity="warning" sx={{ mt: 1 }}>Reconcile the existing branch and PR marker before retrying. Do not issue another create request.</Alert>}
        {target.attempt && <TechnicalDetails title="Pull request details"><Typography component="p" variant="s3" sx={source}>Branch {target.attempt.branch} · generation {target.attempt.generation} · Mode {target.attempt.mode}</Typography><IdentityChip label="Claim" kind="id" value={target.attempt.id} /></TechnicalDetails>}
      </Section>
      <Section title="Independent delivery observations" description="Only observations for this exact release are shown. Missing or stale reports are not recovery proof.">
        {observations?.isLoading ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" rows={3} label="Loading delivery observations" /> : observations?.isError ? <ErrorState title="Could not load observations" onRetry={observations.refetch} /> : <Stack divider={<Box sx={{ borderTop: 1, borderColor: 'divider' }} />}>{['installed', 'discovered', 'delivered'].map((stage) => {
          const fact = observations?.data?.[stage];
          return <Stack key={stage} direction="row" spacing={1} alignItems="center" justifyContent="space-between" useFlexGap flexWrap="wrap" sx={{ py: 1 }}><Box sx={{ minWidth: 0 }}><Typography component="h3" variant="m3">{humanizeCode(stage)}</Typography>{fact && <Typography component="p" variant="s3" color="text.secondary" sx={source}>{fDateTimeUTC(fact.observedAt)} · {humanizeCode(fact.origin)}</Typography>}</Box><StatusBadge status={fact ? (fact.stale ? 'expired' : 'acknowledged') : 'unknown'} label={fact ? (fact.stale ? 'Stale observation' : 'Observed exact release') : 'Not observed'} /></Stack>;
        })}</Stack>}
      </Section>
    </Stack>}
    {target && <Stack spacing={1} alignItems="flex-start" sx={{ p: 2, borderTop: 1, borderColor: 'divider', bgcolor: 'background.paper', flexShrink: 0 }}><Typography variant="s3" color="text.secondary">Decisions are saved to this draft for this review session.</Typography><Stack direction="row" spacing={1}>
      {!target.plan && <Button color="primary" variant="contained" onClick={async () => {
        try {
          if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
          await navigator.clipboard.writeText(planCommand(target.desiredVersion));
          setCopyState({ id: target.bindingId, message: 'Copied plan command.' });
        } catch { setCopyState({ id: target.bindingId, message: "Couldn't copy. Select the plan command above." }); }
      }}>Copy plan command</Button>}
      {onNext && <Button color="primary" variant={target.plan ? 'contained' : 'outlined'} onClick={onNext}>{target.plan ? 'Next target needing review' : 'Next target'}</Button>}
    </Stack>{copyState?.id === target.bindingId && <Typography variant="s3" role="status">{copyState.message}</Typography>}</Stack>}
  </Drawer>;
}
ContextCampaignTargetDrawer.propTypes = {
  target: PropTypes.object, open: PropTypes.bool.isRequired, onClose: PropTypes.func.isRequired,
  decision: PropTypes.object, onDecision: PropTypes.func.isRequired, editable: PropTypes.bool,
  observations: PropTypes.object, onNext: PropTypes.func,
};
