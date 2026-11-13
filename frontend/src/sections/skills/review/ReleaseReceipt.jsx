import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, Stack, Tooltip, Typography } from '@mui/material';
import { enqueueSnackbar } from 'notistack';
import StatusBadge from 'src/components/status-badge';
import { relativeReviewTime, reviewPersonName } from '../utils/review-display';
import { paths } from 'src/routes/paths';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';

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
  return <Stack spacing={1.5} aria-label="Release receipt">
      <Typography variant="s3" color="text.secondary">Release receipt</Typography>
      <StatusBadge status="released" label="Released" sx={{ alignSelf: 'flex-start' }} />
      <Typography variant="m3">{labelName} moved {before != null ? `from v${before} ` : ''}to v{after.version}</Typography>
      {(request?.name || receipt.name) && <Typography variant="s1">{request?.name || receipt.name}</Typography>}
      <Typography variant="s1">Released by {reviewPersonName(receipt.executedByName)} {receipt.recordedAt && <Tooltip title={receipt.recordedAt}><span>{relativeReviewTime(receipt.recordedAt)}</span></Tooltip>} · Approved by {reviewPersonName(receipt.approvedByName)}</Typography>
      <Typography variant="s1" color="text.secondary">Recorded at release time — production may have moved since.</Typography>
      <Stack direction="row" flexWrap="wrap" gap={1}>
        <Button variant="contained" color="primary" href={resourceUrl ? `${resourceUrl}?version=${after.version}` : undefined} disabled={!resourceUrl}>View version</Button>
        <Button variant="outlined" href={resourceUrl ? `${resourceUrl}?tab=versions` : undefined} disabled={!resourceUrl}>View release history</Button>
        <Button onClick={async () => {
          try { await navigator.clipboard.writeText(new URL(receiptUrl, window.location.origin).href); setCopyError(false); enqueueSnackbar('Copied'); }
          catch { setCopyError(true); }
        }}>Copy receipt link</Button>
      </Stack>
      {!resourceUrl && <Typography variant="s3" color="text.secondary">Version links are unavailable because this retained receipt does not include its resource.</Typography>}
      {copyError && <Alert severity="warning">Couldn&apos;t copy. Receipt link: {receiptUrl}</Alert>}
      <TechnicalDetails title="Audit details">
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={3}>
        <Stack spacing={0.5}><Typography variant="s3" color="text.secondary">Currentness before the move</Typography><Typography variant="s1">Release revision {receipt.currentnessAtAction?.release_revision ?? 'Not reported'} · Security policy revision {receipt.currentnessAtAction?.security_revision ?? 'Not reported'}</Typography></Stack>
        <Stack spacing={0.5}><Typography variant="s3" color="text.secondary">Pointer state after the move</Typography><Typography variant="s1">{labelName} v{after.version} · Release revision {after.releaseRevision ?? 'Not reported'}</Typography></Stack>
      </Stack>

        <Stack spacing={1}>
          <IdentityChip label="Request" kind="id" value={receipt.requestId} />
          <IdentityChip label="Decision" kind="id" value={receipt.decisionId} />
          <IdentityChip label="Executed by" kind="id" value={receipt.executedBy} />
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
