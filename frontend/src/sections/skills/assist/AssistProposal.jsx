import PropTypes from 'prop-types';
import { Alert, Box, Button, Stack, Typography } from '@mui/material';

import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import EmptyState from 'src/components/empty-state';
import TextDiff from 'src/sections/skills/components/TextDiff';
import ChecksList from 'src/sections/skills/components/ChecksList';

export default function AssistProposal({ proposal, beforeFiles, isStale, applying, onApply, onUseSummary }) {
  if (proposal.payloadExpired) return <EmptyState variant="no-results" title="Proposal details expired" description="The proposal text is no longer retained. Start a new preview to request another suggestion." />;
  if (proposal.status !== 'completed' || !proposal.proposal) return null;
  const proposed = proposal.proposal;
  const paths = [...new Set([...beforeFiles.map((file) => file.path), ...(proposed.files ?? []).map((file) => file.path)])].sort();
  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap">
        <Typography variant="m3">Review the suggestion</Typography>
        <StatusBadge status="suggested" label="AI suggestion" />
        <Typography variant="s3" color="text.secondary">Private · saved draft r{proposal.baseRevision}</Typography>
      </Stack>
      {isStale && <Alert severity="warning">The draft changed after this suggestion. Review a new preview; this proposal will not replace your later edits.</Alert>}
      {proposal.kind === 'description' && <Alert severity="info">Suggested from these examples, not verified improvement. Test fresh requests before promoting. The {proposal.exposedCaseIds.length} selected examples are now marked as exposed to assistance.</Alert>}
      {proposal.missingInformation.length > 0 && (
        <Section title="Missing facts to resolve" description="Questions and TODOs are not verified policy.">
          <Box component="ul" sx={{ m: 0, pl: 2 }}>
            {proposal.missingInformation.map((question, index) => <Typography component="li" variant="s1" key={index}>{question}</Typography>)}
          </Box>
        </Section>
      )}
      {proposal.kind === 'summary' ? (
        <Section title="Suggested version note" description="AI suggestion — review before publishing.">
          <Typography variant="s1" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{proposed.summary}</Typography>
        </Section>
      ) : paths.map((path) => {
        const before = beforeFiles.find((file) => file.path === path)?.content ?? '';
        const after = (proposed.files ?? []).find((file) => file.path === path)?.content ?? '';
        if (before === after) return null;
        return <Section key={path} title={path}><TextDiff fromText={before} toText={after} fromTitle="Saved draft" toTitle="Proposed draft" /></Section>;
      })}
      {proposed.reason && <Typography variant="s1">{proposed.reason}</Typography>}
      {proposed.tradeoffs?.length > 0 && <Section title="Tradeoffs"><Box component="ul" sx={{ m: 0, pl: 2 }}>{proposed.tradeoffs.map((tradeoff, index) => <Typography component="li" variant="s1" key={index}>{tradeoff}</Typography>)}</Box></Section>}
      {proposal.checks && <Section title="Static checks"><ChecksList checks={proposal.checks} dense /></Section>}
      {proposal.metrics && <Typography variant="s3" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>Provider estimate: {proposal.metrics.taskCost.knownEstimateUsd === null ? 'unknown' : `USD ${proposal.metrics.taskCost.knownEstimateUsd}`}{!proposal.metrics.taskCost.complete ? ' · partial; some usage is unknown' : ''}. This is not a provider invoice.</Typography>}
      <Typography variant="s3" color="text.secondary">Nothing is published or promoted. Applying a proposal only changes your draft.</Typography>
      <Box>
        {proposal.kind === 'summary' ? (
          <Button variant="outlined" disabled={isStale || !onUseSummary} onClick={() => onUseSummary(proposed.summary)}>Use in editable note</Button>
        ) : (
          <Button variant="outlined" disabled={isStale || applying} onClick={onApply}>{applying ? 'Applying…' : 'Apply to draft'}</Button>
        )}
      </Box>
    </Stack>
  );
}

AssistProposal.propTypes = {
  proposal: PropTypes.shape({ status: PropTypes.string.isRequired, kind: PropTypes.string.isRequired, proposal: PropTypes.object, payloadExpired: PropTypes.bool, baseRevision: PropTypes.number, missingInformation: PropTypes.array.isRequired, exposedCaseIds: PropTypes.array.isRequired, checks: PropTypes.object, metrics: PropTypes.object }).isRequired,
  beforeFiles: PropTypes.arrayOf(PropTypes.shape({ path: PropTypes.string.isRequired, content: PropTypes.string.isRequired })).isRequired,
  isStale: PropTypes.bool.isRequired,
  applying: PropTypes.bool,
  onApply: PropTypes.func.isRequired,
  onUseSummary: PropTypes.func,
};
