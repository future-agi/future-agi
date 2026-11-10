import { useState } from 'react';
import PropTypes from 'prop-types';
import { Button, DialogActions, DialogContent, DialogTitle, Stack, TextField, Typography } from '@mui/material';
import { ClosableDialog } from 'src/components/custom-dialog';
import ErrorState from 'src/components/error-state';
import { useSkillTestSuite, useSkillV11Scope } from 'src/api/skills/testing';
import { useAcceptContextCapture, useContextCaptureSource, usePreviewContextCapture } from 'src/api/skills/context-capture';
import ContextCaptureDialog from './ContextCaptureDialog';

// Intent: choose a physical source, inspect authorized segments, then redact and
// explicitly share a regression example. No inferred lineage or model execution.
// Reuse the shared dialog and neutral form rhythm; captured text stays scoped.
function CaptureSource({ contextId, traceRef, onClose, canCapture }) {
  const access = useSkillV11Scope();
  const source = useContextCaptureSource(contextId, traceRef);
  const suite = useSkillTestSuite(contextId);
  const preview = usePreviewContextCapture(contextId);
  const accept = useAcceptContextCapture(contextId);
  if (source.isError || suite.isError) return <ClosableDialog open onClose={onClose} fullWidth maxWidth="sm">
    <DialogTitle>Create context regression case</DialogTitle>
    <DialogContent><ErrorState title="Could not load this authorized source" onRetry={() => { source.refetch(); suite.refetch(); }} /></DialogContent>
  </ClosableDialog>;
  return <ContextCaptureDialog open onClose={onClose} scopeKey={`${access.identity}:${contextId}`}
    state={source.isLoading || suite.isLoading ? 'loading' : source.data && suite.data ? 'ready' : 'unavailable'}
    source={source.data} suiteRevision={suite.data?.revision} canCapture={canCapture}
    onPreview={(input) => preview.mutateAsync(input)}
    onAccept={(input, options) => accept.mutateAsync({ ...input, ...options })} />;
}
CaptureSource.propTypes = { contextId: PropTypes.string.isRequired, traceRef: PropTypes.object.isRequired, onClose: PropTypes.func.isRequired, canCapture: PropTypes.bool.isRequired };

function Capture({ contextId, canCapture }) {
  const [open, setOpen] = useState(false);
  const [fields, setFields] = useState({ projectId: '', traceId: '', spanId: '', startTime: '' });
  const [traceRef, setTraceRef] = useState(null);
  const close = () => { setOpen(false); setTraceRef(null); setFields({ projectId: '', traceId: '', spanId: '', startTime: '' }); };
  const valid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(fields.projectId)
    && fields.traceId.trim() && fields.spanId.trim() && Number.isFinite(Date.parse(fields.startTime));
  return <>
    <Button variant="outlined" disabled={!canCapture} onClick={() => setOpen(true)}>Create regression case from trace</Button>
    {open && (traceRef ? <CaptureSource key={contextId} contextId={contextId} traceRef={traceRef} onClose={close} canCapture={canCapture} />
      : <ClosableDialog open onClose={close} fullWidth maxWidth="sm" aria-labelledby="capture-source-title">
        <DialogTitle id="capture-source-title">Choose an exact trace source</DialogTitle>
        <DialogContent><Stack spacing={1.5} sx={{ pt: 1 }}>
          <Typography variant="s1">Copy the physical span identity from an authorized trace. Only request and tool-result text can be selected.</Typography>
          {Object.entries({ projectId: 'Source project ID', traceId: 'Source trace ID', spanId: 'Source span ID', startTime: 'Span start time' }).map(([key, label]) => <TextField
            key={key} label={label} size="small" value={fields[key]} disabled={!canCapture} inputProps={{ maxLength: 200 }}
            helperText={key === 'startTime' ? 'Exact timestamp with time zone, including fractional seconds.' : undefined}
            onChange={(event) => setFields((value) => ({ ...value, [key]: event.target.value }))} />)}
        </Stack></DialogContent>
        <DialogActions><Button onClick={close}>Cancel</Button><Button variant="contained" disabled={!valid || !canCapture} onClick={() => setTraceRef({ ...fields })}>Load authorized source</Button></DialogActions>
      </ClosableDialog>)}
  </>;
}
Capture.propTypes = { contextId: PropTypes.string.isRequired, canCapture: PropTypes.bool.isRequired };
export default function ContextCaptureController(props) {
  const access = useSkillV11Scope();
  return <Capture key={`${access.identity}:${props.contextId}`} {...props} />;
}
ContextCaptureController.propTypes = Capture.propTypes;
