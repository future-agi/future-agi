import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, Stack, Tooltip, Typography } from '@mui/material';
import { enqueueSnackbar } from 'notistack';
import { relativeReviewTime, reviewPersonName } from '../utils/review-display';
import { paths } from 'src/routes/paths';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import SummaryTable from './SummaryTable';
import { isSkillTracesEnabled } from '../runtime/skillTracesFlag';
import ReceiptLoadsLine from '../runtime/ReceiptLoadsLine';
import ContextReleaseCard from '../runtime/ContextReleaseCard';

// A durable historical record is not a statement about today's production.
export default function ReleaseReceipt({ receipt, request }) {
  const [copyError, setCopyError] = useState(false);
  const after = receipt.postMoveState;
  const before = receipt.previousVersion ?? request?.previousVersion ?? receipt.currentnessAtAction?.version ?? receipt.currentnessAtAction?.expected_version;
  const label = after.label || 'production';
  const labelName = label.charAt(0).toUpperCase() + label.slice(1);
  const subjectId = request?.subjectId || receipt.subjectId;
  const kind = request?.subjectKind || receipt.subjectKind;
  const resourceUrl = subjectId ? (kind === 'agent_context' ? paths.dashboard.skills.contextDetail(subjectId) : paths.dashboard.skills.detail(subjectId)) : null;
  const receiptUrl = `${paths.dashboard.skills.reviews}?request=${encodeURIComponent(receipt.requestId)}`;
  const runtime = Boolean(subjectId && after.eventId) && isSkillTracesEnabled();
  return <Stack spacing={1.5} aria-label="Release receipt">
      {/* The page title already states the move ("Production moved from v1 to v2"); the receipt is the record. */}
      <Stack direction="row" gap={0.75} alignItems="baseline" flexWrap="wrap">
        <Typography variant="s3" color="text.secondary">Release receipt</Typography>
        {receipt.recordedAt ? <Tooltip title={receipt.recordedAt}><Typography component="span" variant="s3" color="text.secondary">· {relativeReviewTime(receipt.recordedAt)}</Typography></Tooltip> : <Typography component="span" variant="s3" color="text.secondary">· Time not reported</Typography>}
      </Stack>
      <SummaryTable label="Release receipt details" labelWidth={140} rows={[
        { label: 'Before the move', value: before != null ? `${labelName} v${before}` : `${labelName} unassigned` },
        { label: 'After the move', value: `${labelName} v${after.version}` },
        { label: 'Approved by', value: reviewPersonName(receipt.approvedByName) },
        { label: 'Released by', value: reviewPersonName(receipt.executedByName) },
      ]} />
      <Typography variant="s3" color="text.secondary">Recorded at release time — production may have moved since.</Typography>
      <Stack direction="row" flexWrap="wrap" alignItems="center" gap={1}>
        {/* The next job for an agent context is confirming apps picked the release up; a skill has no apps to check. */}
        {kind === 'agent_context' && <Button variant="contained" color="primary" href={resourceUrl ? `${resourceUrl}?tab=consumers` : undefined} disabled={!resourceUrl}>Check apps picked up v{after.version}</Button>}
        <Button variant={kind === 'agent_context' ? 'outlined' : 'contained'} color="primary" href={resourceUrl ? `${resourceUrl}?version=${after.version}` : undefined} disabled={!resourceUrl}>View version</Button>
        <Button variant="outlined" href={resourceUrl ? `${resourceUrl}?tab=versions` : undefined} disabled={!resourceUrl}>View release history</Button>
        <Button onClick={async () => {
          try { await navigator.clipboard.writeText(new URL(receiptUrl, window.location.origin).href); setCopyError(false); enqueueSnackbar('Copied'); }
          catch { setCopyError(true); }
        }}>Copy receipt link</Button>
      </Stack>
      {/* With skillTraces on: what loads report since the move, beside delivery (SKILL-TRACES §7.3, §7.4, UX-01, UX-11). */}
      {runtime && <ReceiptLoadsLine kind={kind === 'agent_context' ? 'agent_context' : 'skill'} subjectId={subjectId} eventId={after.eventId} />}
      {runtime && kind === 'agent_context' && <ContextReleaseCard contextId={subjectId} eventId={after.eventId} />}
      {!resourceUrl && <Typography variant="s3" color="text.secondary">Version links are unavailable because this retained receipt does not include its resource.</Typography>}
      {copyError && <Alert severity="warning">Couldn&apos;t copy. Receipt link: {receiptUrl}</Alert>}
      <TechnicalDetails title="What was approved"><Stack spacing={1}>
        <Typography variant="s1">{request?.name || receipt.name || 'This resource'} · revision {receipt.revision ?? request?.revision ?? 'not reported'}</Typography>
        <Typography variant="s3">Approval covered this candidate, target and attached evidence. This receipt records the release; it does not imply that expired evidence is still available.</Typography>
        {request?.packet && <Typography variant="s3">{request.packet.evidence.length} attached results · candidate v{request.packet.transition.version}</Typography>}
      </Stack></TechnicalDetails>
      <TechnicalDetails title="Audit details">
        <Stack spacing={1}>
          <IdentityChip label="Request" kind="id" value={receipt.requestId} />
          <IdentityChip label="Decision" kind="id" value={receipt.decisionId} />
          <IdentityChip label="Executed by" kind="id" value={receipt.executedBy} />
          <Typography variant="s3">Before: release revision {receipt.currentnessAtAction?.release_revision ?? 'not reported'} · security policy revision {receipt.currentnessAtAction?.security_revision ?? 'not reported'}</Typography>
          <Typography variant="s3">After: release revision {after.releaseRevision ?? 'not reported'}</Typography>
          <Typography variant="m3">Recorded preconditions</Typography>
          <Typography component="pre" variant="s3" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(receipt.currentnessAtAction, null, 2)}</Typography>
          <Typography variant="m3">Recorded result</Typography>
          <Typography component="pre" variant="s3" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(after, null, 2)}</Typography>
          <Typography variant="s3">Installation and model delivery require their own observations.</Typography>
        </Stack>
      </TechnicalDetails>
    </Stack>;
}
ReleaseReceipt.propTypes = { receipt: PropTypes.object.isRequired, request: PropTypes.object };
