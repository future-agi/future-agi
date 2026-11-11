import PropTypes from 'prop-types';
import { DialogContent, DialogTitle } from '@mui/material';
import { ClosableDialog } from 'src/components/custom-dialog';
import ErrorState from 'src/components/error-state';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useContextTestSuite } from 'src/api/skills/context-testing';
import { useAcceptContextCapture, useContextCaptureSource, usePreviewContextCapture } from 'src/api/skills/context-capture';
import ContextCaptureDialog from './ContextCaptureDialog';

// Intent: an incident responder carries the selected physical span into review,
// never transcribing identities. The source stays immutable across redaction.
// Hierarchy/palette/depth: shared neutral review dialog, identity before text.
// Surfaces/type/spacing: paper, m3/s1/s3 and the existing 12px control rhythm.
function CaptureSource({ contextId, traceRef, onClose, canCapture }) {
  const access = useSkillV11Scope();
  const source = useContextCaptureSource(contextId, traceRef);
  const suite = useContextTestSuite(contextId);
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
export default function ContextCaptureController(props) {
  const access = useSkillV11Scope();
  const { traceRef } = props;
  return <CaptureSource key={`${access.identity}:${props.contextId}:${JSON.stringify(traceRef)}`} {...props} />;
}
ContextCaptureController.propTypes = CaptureSource.propTypes;
