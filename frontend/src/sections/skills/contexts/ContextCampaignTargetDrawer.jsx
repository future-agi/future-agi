import { useId } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Drawer, MenuItem, Stack, TextField, Typography } from '@mui/material';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import { coveredDrawerSx, useDrawerCovered } from 'src/hooks/use-drawer-stack';

const source = { overflowWrap: 'anywhere', whiteSpace: 'pre-wrap', m: 0 };
export default function ContextCampaignTargetDrawer({ target, open, onClose, decision, onDecision, editable = false, observations }) {
  const title = useId();
  const covered = useDrawerCovered(open);
  return <Drawer anchor="right" open={open} onClose={onClose} sx={coveredDrawerSx(covered)}
    PaperProps={{ role: 'dialog', 'aria-modal': true, 'aria-labelledby': title, sx: { width: { xs: 1, md: 960 }, maxWidth: 1, p: 2, gap: 2 } }}>
    <Stack direction="row" justifyContent="space-between" alignItems="center" spacing={1} sx={{ position: 'sticky', top: 0, zIndex: 1, bgcolor: 'background.paper', py: 1 }}>
      <Typography id={title} component="h2" variant="m1">{target?.name || 'Repository target'}</Typography>
      <Button size="small" onClick={onClose}>Close target</Button>
    </Stack>
    {target && <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Alert severity="info">Git plans and PR receipts are client-reported. Merged does not mean installed or delivered.</Alert>
      <Section title="Exact target">
        <Typography component="p" variant="s1">Release v{target.desiredVersion} · {target.selector.version ? `Pinned v${target.selector.version}` : `Follows ${target.selector.label}`}</Typography>
        <Typography component="p" variant="s3" sx={source}>{target.desiredVersionId}{'\n'}{target.desiredDigest}</Typography>
        {target.repository ? <Typography component="p" variant="s3" sx={source}>Current binding repository ID {target.repository.repositoryId} · {target.repository.defaultBranch} · Scope {target.repository.projectScope || 'project root'}</Typography> : <Alert severity="warning">This binding no longer names a repository. The old plan cannot authorize new writes.</Alert>}
      </Section>
      {!target.plan ? <Alert severity="warning">No local plan has been submitted. Inspect this repository with the qualified local executor before including it.</Alert> : <>
        <Section title="Reviewed repository preimages">
          <Typography component="p" variant="s3" sx={source}>Planned repository ID {target.plan.repositoryId}{'\n'}Base commit {target.plan.baseCommit}{'\n'}Plan {target.planDigest}{'\n'}Previous lock {target.plan.previousLockDigest || 'None — cold adoption'}{'\n'}Materialization {target.plan.materializationDigest}</Typography>
          {target.plan.localReviewRequired && <Alert severity="warning">Review and preserve existing local instructions. Record that decision below; this is not a semantic merge.</Alert>}
          {target.plan.blockers.map((blocker) => <Alert severity="warning" key={blocker}>{blocker}</Alert>)}
        </Section>
        <Section title="Exact planned changes" description="These are review excerpts. The local executor must verify all full-file preimages and output hashes before writing.">
          {target.plan.changes.map((change) => <Box key={change.path} sx={{ borderBottom: 1, borderColor: 'divider', py: 1 }}>
            <Typography component="h3" variant="m3" sx={source}>{change.path} · {change.action} · {change.ownership}</Typography>
            <Typography component="p" variant="s3" color="text.secondary" sx={source}>Before {change.beforeSha256 || 'absent'}{'\n'}After {change.afterSha256 || 'absent'}</Typography>
            <Typography component="pre" variant="s1" sx={source}>{change.diffPreview}</Typography>
          </Box>)}
        </Section>
      </>}
      {decision && <Section title="Target decision">
        <TextField select fullWidth size="small" label="Include this target" value={decision.decision} disabled={!editable} onChange={(event) => onDecision({ ...decision, decision: event.target.value })}>
          <MenuItem value="pending">Not decided</MenuItem><MenuItem value="include" disabled={!target.repository || !target.plan || target.plan.blockers.length > 0 || (target.selector.version && target.selector.version !== target.desiredVersion)}>Include exact plan</MenuItem><MenuItem value="exclude">Exclude with reason</MenuItem>
        </TextField>
        <TextField fullWidth multiline minRows={2} size="small" label="Review or exclusion reason" value={decision.reason} disabled={!editable} onChange={(event) => onDecision({ ...decision, reason: event.target.value })} inputProps={{ maxLength: 500 }} sx={{ mt: 1 }} />
      </Section>}
      <Section title="Git observation">
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap"><StatusBadge status={target.prState} /><StatusBadge status={target.mergeState} label={`Merge: ${target.mergeState}`} /></Stack>
        {target.attempt && <Typography component="p" variant="s3" sx={source}>Branch {target.attempt.branch}{'\n'}Claim {target.attempt.id} · generation {target.attempt.generation}{'\n'}Mode {target.attempt.mode}{'\n'}{target.attempt.receipt?.pullRequestNumber ? `PR #${target.attempt.receipt.pullRequestNumber}` : 'No PR number observed'}</Typography>}
        {target.prState === 'unknown' && <Alert severity="warning">Reconcile the existing branch and PR marker before retrying. Do not issue another create request.</Alert>}
      </Section>
      <Section title="Independent delivery observations" description="Only observations for this exact release are shown. Missing or stale reports are not recovery proof.">
        {observations?.isLoading ? <ContentSkeleton variant="rows" rows={3} label="Loading delivery observations" /> : observations?.isError ? <ErrorState title="Could not load observations" onRetry={observations.refetch} /> : ['installed', 'discovered', 'delivered'].map((stage) => {
          const fact = observations?.data?.[stage];
          return <Box key={stage} sx={{ py: 1 }}><Typography component="h3" variant="m3">{stage}</Typography><StatusBadge status={fact ? (fact.stale ? 'expired' : 'acknowledged') : 'unknown'} label={fact ? (fact.stale ? 'Stale observation' : 'Observed exact release') : 'Not observed'} />{fact && <Typography component="p" variant="s3" sx={source}>{fact.observedAt} · {fact.origin}</Typography>}</Box>;
        })}
      </Section>
    </Stack>}
  </Drawer>;
}
ContextCampaignTargetDrawer.propTypes = {
  target: PropTypes.object, open: PropTypes.bool.isRequired, onClose: PropTypes.func.isRequired,
  decision: PropTypes.object, onDecision: PropTypes.func.isRequired, editable: PropTypes.bool,
  observations: PropTypes.object,
};
