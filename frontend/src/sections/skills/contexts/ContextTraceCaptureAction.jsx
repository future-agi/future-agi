import { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField } from '@mui/material';
import { ClosableDialog } from 'src/components/custom-dialog';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import { useContexts, useContextDetail } from 'src/api/skills/context-authoring';
import { useSkillV11Scope } from 'src/api/skills/testing';
import ContextCaptureController from './ContextCaptureController';

// Intent: the responder chooses a destination, not four physical identity fields.
// Hierarchy: source notice then context choice, with one explicit review action.
// Palette/depth/surfaces: existing neutral paper and divider dialog.
// Typography/spacing: shared m3/s1 forms and 12px groups, no new visual language.
function ChooseContext({ traceRef, contextHint, onClose }) {
  const [page, setPage] = useState(1);
  // The span names the context it was delivered from. It is only a default: the server still
  // authorizes capture into it, and the reviewer can change it before reviewing the span.
  const [contextId, setContextId] = useState(contextHint || '');
  const [review, setReview] = useState(false);
  // The reviewer can always leave the delivered context for another one; the hint is only a default.
  const [declined, setDeclined] = useState(false);
  const contexts = useContexts({ page, pageSize: 25 });
  const detail = useContextDetail(contextId);
  const canCapture = !detail.isError && !detail.isFetching && detail.data?.releaseState.permissions.canPublish === true;
  const hinted = Boolean(contextHint) && contextId === contextHint && !declined;
  const resolving = hinted && (detail.isLoading || detail.isFetching);
  useEffect(() => { if (hinted && canCapture) setReview(true); }, [hinted, canCapture]);
  if (review) return <ContextCaptureController contextId={contextId} traceRef={traceRef} canCapture={canCapture} onClose={onClose}
    onChangeContext={() => { setDeclined(true); setReview(false); setContextId(''); }} />;
  return <ClosableDialog open onClose={onClose} fullWidth maxWidth="sm" aria-labelledby="context-capture-destination">
    <DialogTitle id="context-capture-destination">Choose a context for this test case</DialogTitle>
    <DialogContent><Stack spacing={1.5} sx={{ pt: 1 }}>
      <Alert severity="info">The selected span identity is carried automatically. Review and redact its request and tool results before sharing a case.</Alert>
      {hinted && detail.isError && <Alert severity="warning">The context this span was delivered from is not available to you. Choose another context.</Alert>}
      {resolving ? <ContentSkeleton label="Opening the context this span was delivered from" /> : contexts.isLoading ? <ContentSkeleton label="Loading contexts" /> : contexts.isError ? <ErrorState title="Could not load contexts" onRetry={contexts.refetch} /> : !contexts.data?.results.length ? <EmptyState title="No contexts on this page" description="Create a context in Skills before saving a test case." /> : <TextField select size="small" label="Context" value={contextId} onChange={(event) => setContextId(event.target.value)}>
        {contexts.data.results.map((context) => <MenuItem key={context.id} value={context.id}>{context.name}</MenuItem>)}
      </TextField>}
      <Stack direction="row" spacing={1}>
        <Button disabled={page <= 1 || contexts.isFetching} onClick={() => { setContextId(''); setPage((value) => value - 1); }}>Previous contexts</Button>
        <Button disabled={!contexts.data || page >= contexts.data.totalPages || contexts.isFetching} onClick={() => { setContextId(''); setPage((value) => value + 1); }}>More contexts</Button>
      </Stack>
      {detail.isError && <ErrorState title="Context access unavailable" onRetry={detail.refetch} />}
      {contextId && detail.data && !detail.isFetching && !canCapture && <Alert severity="info">Only a workspace author can save test cases to this context.</Alert>}
    </Stack></DialogContent>
    <DialogActions><Button onClick={onClose}>Cancel</Button><Button color="primary" variant="contained" disabled={!contextId || !canCapture || contexts.isError} onClick={() => setReview(true)}>Review selected span</Button></DialogActions>
  </ClosableDialog>;
}
ChooseContext.propTypes = { traceRef: PropTypes.object.isRequired, contextHint: PropTypes.string, onClose: PropTypes.func.isRequired };
function ScopedAction({ traceRef, contextHint }) {
  const [open, setOpen] = useState(false);
  return <><Button size="small" variant="outlined" onClick={() => setOpen(true)}>Save as test case</Button>
    {open && <ChooseContext traceRef={traceRef} contextHint={contextHint} onClose={() => setOpen(false)} />}</>;
}
ScopedAction.propTypes = { traceRef: PropTypes.object.isRequired, contextHint: PropTypes.string };
export default function ContextTraceCaptureAction({ traceRef, contextHint }) {
  const access = useSkillV11Scope();
  if (!access.enabled) return null;
  return <ScopedAction key={`${access.identity}:${JSON.stringify(traceRef)}`} traceRef={traceRef} contextHint={contextHint} />;
}
ContextTraceCaptureAction.propTypes = ScopedAction.propTypes;
