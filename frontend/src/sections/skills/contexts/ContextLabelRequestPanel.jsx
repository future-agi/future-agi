import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Button, Checkbox, FormControlLabel, Stack, TextField, Typography } from '@mui/material';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { usePreviewContextLabelRequest, useSubmitContextLabelRequest } from 'src/api/skills/context-release-request';
import Section from 'src/components/section';
import { paths } from 'src/routes/paths';
import { AuthoringError, useAuthoringOperation } from '../editor/HotfixHeadReview';

// Intent: authors request, never imply, authority to move an exact parent.
// Hierarchy/signature: current -> proposed pointer, compiled digest, reviewer.
// Palette/depth/surfaces: shared neutral paper and warning roles, flat groups.
// Typography: m3/s1/s3; 12px spacing. No bulk approval or success-green shortcut.
// The last reviewer a person chose is a per-viewer convenience for requesting several parents in a row;
// it is shown in the field, validated like typed input, and never submitted without the explicit confirmation below.
// Keyed by the organization/workspace/user identity so another login never inherits this choice.
const reviewerKey = (identity) => `skills.releaseRequest.lastReviewer:${identity}`;
const readReviewer = (identity) => { try { return window.localStorage.getItem(reviewerKey(identity)) || ''; } catch { return ''; } };
const writeReviewer = (identity, value) => { try { window.localStorage.setItem(reviewerKey(identity), value); } catch { /* Storage is optional. */ } };
function Request({ contextId, version, scopeKey, authorId, identity }) {
  const [label, setLabel] = useState('production');
  const [reviewerId, setReviewerId] = useState(() => readReviewer(identity));
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [receipt, setReceipt] = useState(null);
  const prepare = usePreviewContextLabelRequest(contextId);
  const submit = useSubmitContextLabelRequest();
  const operation = useAuthoringOperation(scopeKey);
  const locked = operation.pending || operation.uncertain || prepare.isPending || Boolean(receipt);
  const validReviewer = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(reviewerId) && reviewerId.toLowerCase() !== authorId?.toLowerCase();
  const current = preview?.packet.candidate_version_id === version?.id && preview?.packet.candidate_digest === version?.digest && preview?.packet.transition.label === label;
  const send = async () => {
    const result = await operation.run((input, extra) => submit.mutateAsync({ ...input, ...extra }), { packet: preview.packet, fingerprint: preview.fingerprint, reviewerId });
    if (result) { setReceipt(result); writeReviewer(identity, reviewerId); }
  };
  return <Stack spacing={1.5}>
    <Section title="Request a label move" description="Publication and promotion are separate. A different authorized person must review this exact parent before a label can move." />
    <Alert severity="warning">This request contains no test evidence and makes no release-readiness claim. Evidence-gated policies can reject it. The reviewer inspects exact immutable files and current release preconditions.</Alert>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
      <TextField size="small" label="Release label" value={label} disabled={locked} onChange={(event) => { setLabel(event.target.value); setPreview(null); setConfirmed(false); }} />
      <TextField size="small" label="Independent reviewer user ID" value={reviewerId} disabled={locked} onChange={(event) => { setReviewerId(event.target.value.trim()); setConfirmed(false); }} helperText="Use the ID of an authorized workspace member, not your own ID." fullWidth />
    </Stack>
    <AuthoringError error={prepare.error || operation.error} />
    <Button variant="outlined" disabled={locked || !version || !/^[a-z0-9][a-z0-9-]{0,39}$/.test(label)} onClick={async () => { setPreview(null); setConfirmed(false); try { setPreview(await prepare.mutateAsync({ versionId: version.id, label })); } catch { /* The authoritative rejection is rendered above. */ } }}>Preview parent label move</Button>
    {preview && <>
      <Typography variant="m3">{label}: {preview.packet.transition.expected_version === null ? 'Unassigned' : `v${preview.packet.transition.expected_version}`} → v{preview.packet.transition.version}</Typography>
      <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{preview.packet.candidate_digest}</Typography>
      <Typography variant="s3">Release revision {preview.packet.transition.expected_release_revision} · safety revision {preview.packet.transition.expected_security_revision}</Typography>
      {!current && <Alert severity="warning">The viewed release changed. Preview the new exact target before submitting.</Alert>}
      <FormControlLabel control={<Checkbox checked={confirmed} disabled={locked || !current} onChange={(event) => setConfirmed(event.target.checked)} />} label="Request independent review of this exact parent transition. Do not move its label yet." />
      <Button variant="contained" disabled={locked || !current || !confirmed || !validReviewer} onClick={send}>Send parent release request</Button>
    </>}
    {operation.uncertain && <Button disabled={operation.pending} onClick={async () => { const result = await operation.retry(); if (result) setReceipt(result); }}>Recover original release request</Button>}
    {receipt && <Alert severity="info" action={<Button component={RouterLink} to={`${paths.dashboard.skills.contextReview(contextId)}?request=${receipt.id}`}>Inspect request</Button>}>Request sent to the independent reviewer. No label move has been confirmed.</Alert>}
  </Stack>;
}
Request.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object, scopeKey: PropTypes.string.isRequired, authorId: PropTypes.string, identity: PropTypes.string.isRequired };
export default function ContextLabelRequestPanel({ contextId, version }) {
  const { identity, scope } = useSkillV11Scope();
  const scopeKey = `${identity}:${contextId}`;
  return <Request key={scopeKey} contextId={contextId} version={version} scopeKey={scopeKey} authorId={scope.userId} identity={identity} />;
}
ContextLabelRequestPanel.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object };
