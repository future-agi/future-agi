import PropTypes from 'prop-types';
import { Alert, Box, Stack, Typography } from '@mui/material';
import Section from 'src/components/section';
import { IdentityValue } from './ReleaseReviewBody';

// History is a metadata receipt, not a re-render of retained evidence. Keep the
// pre-move checks and post-move pointer visibly separate even after expiry.
export default function ReleaseReceipt({ receipt }) {
  return <Section title="Durable release receipt">
    <Stack spacing={1}>
      <IdentityValue label="Request" value={receipt.requestId} />
      <IdentityValue label="Decision" value={receipt.decisionId} />
      <IdentityValue label="Executed by" value={receipt.executedBy} />
      <Box component="details" open>
        <Typography component="summary" variant="m3">Currentness before the move</Typography>
        <Typography component="pre" variant="s3" sx={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{JSON.stringify(receipt.currentnessAtAction, null, 2)}</Typography>
      </Box>
      <Box component="details" open>
        <Typography component="summary" variant="m3">Pointer state after the move</Typography>
        <Typography component="pre" variant="s3" sx={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap' }}>{JSON.stringify(receipt.postMoveState, null, 2)}</Typography>
      </Box>
      <Alert severity="info">This receipt records the pointer at execution, not its current position. Installation and model delivery require their own observations.</Alert>
    </Stack>
  </Section>;
}
ReleaseReceipt.propTypes = { receipt: PropTypes.object.isRequired };
