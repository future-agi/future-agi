import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, Stack, Typography } from '@mui/material';
import RecoverableDraftChanges from '../editor/RecoverableDraftChanges';
import { AuthoringError, useAuthoringOperation } from '../editor/HotfixHeadReview';

// Intent: an incident responder keeps unrelated personal work before a historical repair.
// Hierarchy: explicit checkpoint decision, then a separate exact-origin start.
// Palette/depth/surfaces: existing neutral theme; warning means a draft will suspend.
// Typography/spacing: shared s1/s3 and 12px groups, no new surfaces or visual language.
// Domain: checkpoint, content origin, publication head, retained links, private draft.
// Signature: preserving one draft never silently imports the newer publication head.
export default function ContextDraftSwitch({ scopeKey, draft, targetVersion, onSuspend, onStart, onStarted }) {
  const [preserved, setPreserved] = useState(null);
  const operation = useAuthoringOperation(scopeKey);
  // This is a saved-draft review, not a second editor. Retain its exact revision
  // across query invalidations and lost responses; the server enforces its CAS.
  const session = { ...draft, editSequence: 0, flush: async () => draft };
  if (!preserved) return <RecoverableDraftChanges
    scopeKey={scopeKey} session={session} serverDraft={draft} targetVersion={targetVersion}
    onSuspend={onSuspend} onPreserved={setPreserved}
  />;
  return <Stack spacing={1.5}>
    <Alert severity="info">Your private draft is preserved. Start from the exact viewed v{targetVersion}; newer latest files will not be copied.</Alert>
    <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>Checkpoint {preserved.checkpoint.id} · expires {preserved.checkpoint.expiresAt}</Typography>
    <AuthoringError error={operation.error} />
    <Button variant="contained" disabled={operation.pending} onClick={async () => {
      const result = await operation.run(onStart, { revision: 0, fromVersion: targetVersion });
      if (result) onStarted(result);
    }}>Start preserved-origin draft</Button>
  </Stack>;
}
ContextDraftSwitch.propTypes = {
  scopeKey: PropTypes.string.isRequired, draft: PropTypes.object.isRequired, targetVersion: PropTypes.number.isRequired,
  onSuspend: PropTypes.func.isRequired, onStart: PropTypes.func.isRequired, onStarted: PropTypes.func.isRequired,
};
