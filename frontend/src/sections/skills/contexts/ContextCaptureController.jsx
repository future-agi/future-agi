import PropTypes from 'prop-types';
import { useNavigate } from 'react-router-dom';
import { Alert, Button, DialogContent, DialogTitle, Stack } from '@mui/material';
import { ClosableDialog } from 'src/components/custom-dialog';
import ErrorState from 'src/components/error-state';
import { paths } from 'src/routes/paths';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useContextDetail } from 'src/api/skills/context-authoring';
import { useContextInspection, useContextVersions } from 'src/api/skills/context-production';
import { useContextTestSuite } from 'src/api/skills/context-testing';
import { useAcceptContextCapture, useContextCaptureSource, usePreviewContextCapture } from 'src/api/skills/context-capture';
import ContextCaptureDialog from './ContextCaptureDialog';
import { contextSkillChoices } from './context-skill-choices';

// Intent: an incident responder carries the selected physical span into review,
// never transcribing identities. The source stays immutable across redaction.
// Hierarchy/palette/depth: shared neutral review dialog, identity before text.
// Surfaces/type/spacing: paper, m3/s1/s3 and the existing 12px control rhythm.
function CaptureSource({ contextId, traceRef, onClose, canCapture }) {
  const access = useSkillV11Scope();
  const navigate = useNavigate();
  const source = useContextCaptureSource(contextId, traceRef);
  const suite = useContextTestSuite(contextId);
  const preview = usePreviewContextCapture(contextId);
  const accept = useAcceptContextCapture(contextId);
  const detail = useContextDetail(contextId);
  const latest = detail.data?.context.latestVersion;
  const inspection = useContextInspection(contextId, latest);
  const versions = useContextVersions(contextId, 1);
  const skills = contextSkillChoices(inspection.data?.descriptor);
  if (source.isError || suite.isError) return <ClosableDialog open onClose={onClose} fullWidth maxWidth="sm">
    <DialogTitle>Create context regression case</DialogTitle>
    <DialogContent><ErrorState title="Could not load this authorized source" onRetry={() => { source.refetch(); suite.refetch(); }} /></DialogContent>
  </ClosableDialog>;
  // After the reviewed example is saved, the next step is the reproduction on the application.
  // The reference is the version the capture recorded (server data), the candidate is the
  // current head, and nothing is run until the reviewer accepts the paid ceiling there.
  const renderSaved = ({ caseId, preview: reviewed }) => {
    const recorded = versions.data?.results.find((version) => version.id === reviewed.recordedContext?.versionId)?.number;
    const params = new URLSearchParams({ tab: 'tests', reproduce: caseId, ...(latest ? { version: String(latest) } : {}), ...(recorded ? { reference: String(recorded) } : {}) });
    return <Stack spacing={1}>
      {!recorded && <Alert severity="warning">The exact recorded context is unavailable, so this example is a diagnostic text case, not a historical reproduction.</Alert>}
      <Button variant="contained" onClick={() => navigate(`${paths.dashboard.skills.contextDetail(contextId)}?${params}`)}>Reproduce on the application</Button>
    </Stack>;
  };
  return <ContextCaptureDialog open onClose={onClose} scopeKey={`${access.identity}:${contextId}`}
    state={source.isLoading || suite.isLoading ? 'loading' : source.data && suite.data ? 'ready' : 'unavailable'}
    source={source.data} suiteRevision={suite.data?.revision} canCapture={canCapture}
    applicationBehavior={skills.length ? { skills } : undefined} renderSaved={renderSaved}
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
