import { useEffect, useState } from 'react';
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
  const start = async () => {
    const result = await operation.run(onStart, { revision: 0, fromVersion: targetVersion });
    if (result) onStarted(result);
  };
  return <Started preserved={preserved} targetVersion={targetVersion} operation={operation} start={start} />;
}
// The reviewer already consented to the checkpoint for exactly this origin, so the exact-origin
// start follows automatically once. A failed or conflicting start keeps the retained checkpoint
// and offers an explicit retry; it never suspends again.
function Started({ preserved, targetVersion, operation, start }) {
  const [began, setBegan] = useState(false);
  useEffect(() => { if (!began) { setBegan(true); start(); } }, [began]); // eslint-disable-line react-hooks/exhaustive-deps
  return <Stack spacing={1.5}>
    <Alert severity="info">Your private draft is preserved. Starting from the exact viewed v{targetVersion}; newer latest files will not be copied.</Alert>
    <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>Checkpoint {preserved.checkpoint.id} · expires {preserved.checkpoint.expiresAt}</Typography>
    <AuthoringError error={operation.error} />
    {operation.error && <Button color="primary" variant="contained" disabled={operation.pending} onClick={start}>Start preserved-origin draft</Button>}
  </Stack>;
}
Started.propTypes = { preserved: PropTypes.object.isRequired, targetVersion: PropTypes.number.isRequired, operation: PropTypes.object.isRequired, start: PropTypes.func.isRequired };
ContextDraftSwitch.propTypes = {
  scopeKey: PropTypes.string.isRequired, draft: PropTypes.object.isRequired, targetVersion: PropTypes.number.isRequired,
  onSuspend: PropTypes.func.isRequired, onStart: PropTypes.func.isRequired, onStarted: PropTypes.func.isRequired,
};
