import { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import PageHeader from 'src/components/page-header';
import Section from 'src/components/section';
import { paths } from 'src/routes/paths';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useReviewSubjects, useReviewCandidates, useReviewOptions, usePrepareReleaseReview, useSubmitReleaseReview, useShareReleaseEvidence } from 'src/api/skills/review-authoring';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import { ConfirmDialog } from 'src/components/custom-dialog';
import { ReviewState, TypedEvidenceList, evidenceProblems, evidenceSelection } from './ReleaseReviewBody';

// Intent: choose immutable records by name, inspect a frozen packet, then assign
// an independent reviewer. Existing page/section/form tokens; no identity entry.
function PageButtons({ query, page, setPage, label }) {
  return <Stack direction="row" spacing={1}>
    <Button disabled={page <= 1 || query.isFetching} onClick={() => setPage(page - 1)}>Previous {label}</Button>
    <Button disabled={!query.data || page >= query.data.total_pages || query.isFetching} onClick={() => setPage(page + 1)}>Next {label}</Button>
  </Stack>;
}
PageButtons.propTypes = { query: PropTypes.object.isRequired, page: PropTypes.number.isRequired, setPage: PropTypes.func.isRequired, label: PropTypes.string.isRequired };

// J4 batch sharing: one explicit decision names every result that becomes
// workspace-visible and is attached. Nothing private is shared implicitly.
function ShareAllEvidence({ packet, evidence, onShare, onShared }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const shareable = evidence.filter((ref) => {
    const reasons = evidenceProblems(ref, packet);
    return reasons.length === 1 && ref.visibility !== 'workspace';
  });
  const ready = evidence.filter((ref) => !evidenceProblems(ref, packet).length);
  const all = [...shareable, ...ready];
  if (!all.length) return null;
  return <Stack spacing={1}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'center' }}>
      <Button variant="contained" disabled={busy} onClick={() => { setError(''); if (shareable.length) setOpen(true); else onShared(ready); }}>
        {shareable.length ? `Share and attach ${all.length} results` : `Attach ${all.length} results`}
      </Button>
      <Typography variant="s3">Or choose results one by one below.</Typography>
    </Stack>
    {error && <Alert severity="error">{error}</Alert>}
    <ConfirmDialog open={open} onClose={() => { if (!busy) setOpen(false); }} closeDisabled={busy}
      title={`Share ${shareable.length} private results with the workspace?`}
      content={<Stack spacing={1}>
        <Typography variant="s1">Each frozen draft, example inputs, model answers and file receipts become visible to workspace members. Sharing does not extend expiry or release content. These results are then attached to the review:</Typography>
        {all.map((ref) => <Typography key={`${ref.source}:${ref.sourceId}`} variant="s3" sx={{ overflowWrap: 'anywhere' }}>{ref.source === 'skills_text' ? 'Text' : 'Native'} · {ref.sourceId} · {ref.visibility}</Typography>)}
      </Stack>}
      action={<Button variant="contained" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          for (const ref of shareable) await onShare({ ...evidenceSelection(ref), acknowledgeSnapshotSharing: true });
          setOpen(false);
          await onShared(all);
        } catch { setError('Could not confirm sharing for every result. Refresh the evidence state; nothing was attached.'); } finally { setBusy(false); }
      }}>Share and attach</Button>} />
  </Stack>;
}
ShareAllEvidence.propTypes = { packet: PropTypes.object.isRequired, evidence: PropTypes.array.isRequired, onShare: PropTypes.func.isRequired, onShared: PropTypes.func.isRequired };

export default function ReleaseRequestComposer({ scopeKey, renewal, prefill }) {
  const navigate = useNavigate();
  const access = useSkillV11Scope();
  // A just-published candidate carries its identities forward; nothing is typed.
  const [kind, setKind] = useState(renewal?.kind || prefill?.kind || 'skill');
  const [resourceId, setResourceId] = useState(renewal?.subjectId || prefill?.subjectId || '');
  const [candidateId, setCandidateId] = useState(renewal ? '' : prefill?.candidateId || '');
  const targetTouched = useRef(false);
  const [subjectPage, setSubjectPage] = useState(1);
  const [versionPage, setVersionPage] = useState(1);
  const [label, setLabel] = useState('production');
  const [targetId, setTargetId] = useState('');
  const [selected, setSelected] = useState([]);
  const [pipelineUrl, setPipelineUrl] = useState('');
  const [pipelineConfig, setPipelineConfig] = useState('');
  const [pipelineDecision, setPipelineDecision] = useState('');
  const [prepared, setPrepared] = useState(null);
  const [error, setError] = useState('');
  const subjects = useReviewSubjects(kind, subjectPage);
  const versions = useReviewCandidates(kind, resourceId, versionPage);
  const options = useReviewOptions(kind, resourceId, candidateId);
  const prepare = usePrepareReleaseReview();
  const submit = useSubmitReleaseReview();
  const share = useShareReleaseEvidence(resourceId, kind);
  const candidate = versions.data?.results.find((item) => item.id === candidateId);
  const target = options.data?.targets.find((item) => item.target.bindingId === targetId)?.target || null;
  const changeResource = (value) => { setResourceId(value); setCandidateId(''); setVersionPage(1); setSelected([]); setTargetId(''); setError(''); };
  const changeCandidate = (value) => { setCandidateId(value); setSelected([]); setTargetId(''); targetTouched.current = false; setError(''); };
  // Exactly one registered target is preselected (visible, changeable).
  const onlyTarget = options.data?.targets.length === 1 ? options.data.targets[0].target.bindingId : '';
  useEffect(() => { if (onlyTarget && !targetTouched.current && !targetId) setTargetId(onlyTarget); }, [onlyTarget, targetId]);
  const busy = prepare.isPending;
  const failed = subjects.isError || versions.isError || options.isError;
  const packet = { subjectId: resourceId, subjectKind: kind, candidateDigest: candidate?.digest, target };
  const pipelineComplete = !pipelineUrl && !pipelineConfig && !pipelineDecision || /^https:\/\//.test(pipelineUrl) && pipelineConfig.trim() && pipelineDecision.trim();
  const refresh = () => { subjects.refetch(); if (resourceId) versions.refetch(); if (candidateId) options.refetch(); };
  const preparePacket = async () => {
    setError('');
    try {
      setPrepared(await prepare.mutateAsync({ resourceId, kind, candidateId, label, target, evidence: selected,
        externalPipeline: pipelineUrl ? { trust: 'operator_recorded_unimported', url: pipelineUrl, configuration_identity: pipelineConfig, decision: pipelineDecision, recorded_by: access.scope.userId } : null }));
    } catch { setError('Could not prepare the exact review. Reload the available records and inspect any changed selections. Nothing was submitted.'); }
  };
  return <Stack spacing={3} sx={{ px: { xs: 2, sm: 3 }, py: 2, minWidth: 0 }}>
    <PageHeader title={renewal ? 'Renew release review' : 'Request release review'} description="Production stays unchanged. Sharing, submitting, approving and releasing are separate decisions." />
    {renewal && <Alert severity="info">Renew request revision {renewal.revision}. Select and inspect current evidence explicitly; previous approval will not carry forward.</Alert>}
    {prepared ? <ReleaseRequestPanel scopeKey={scopeKey} state="ready" {...prepared}
      recipients={options.data?.recipients || []} permissions={{ canSubmit: true }}
      onRefresh={() => { setPrepared(null); refresh(); }}
      onSubmit={async (input) => {
        const result = await submit.mutateAsync({ ...input, prepared, ...(renewal ? { requestId: renewal.id, revision: renewal.revision } : {}) });
        navigate(`${paths.dashboard.skills.reviews}?mine=1`);
        return result;
      }} /> : <>
      {failed && <ReviewState state="error" onRetry={refresh} />}
      {error && <Alert severity="error">{error}</Alert>}
      <Section title="Candidate and intended transition"><Stack spacing={1.5}>
        <TextField select size="small" label="Resource kind" value={kind} disabled={busy || Boolean(renewal)} onChange={(event) => { setKind(event.target.value); changeResource(''); setSubjectPage(1); }}>
          <MenuItem value="skill">Skill</MenuItem><MenuItem value="agent_context">Agent context</MenuItem>
        </TextField>
        {renewal ? <Typography variant="s1">Renewing the same resource. Choose its immutable candidate below.</Typography> : <>
          <TextField select size="small" label="Release resource" value={resourceId} disabled={busy || subjects.isFetching} onChange={(event) => changeResource(event.target.value)}>
            <MenuItem value="">Choose a resource</MenuItem>
            {subjects.data?.results.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
          </TextField>
          <PageButtons query={subjects} page={subjectPage} setPage={(value) => { setSubjectPage(value); changeResource(''); }} label="resources" />
        </>}
        {resourceId && <>
          <TextField select size="small" label="Published candidate" value={candidateId} disabled={busy || versions.isFetching} onChange={(event) => changeCandidate(event.target.value)}>
            <MenuItem value="">Choose an immutable version</MenuItem>
            {versions.data?.results.map((item) => <MenuItem key={item.id} value={item.id}>v{item.number}{item.note ? ` — ${item.note}` : ''}</MenuItem>)}
          </TextField>
          <PageButtons query={versions} page={versionPage} setPage={(value) => { setVersionPage(value); changeCandidate(''); }} label="versions" />
        </>}
        <TextField size="small" label="Release label" value={label} disabled={busy} onChange={(event) => setLabel(event.target.value)} inputProps={{ maxLength: 40 }} />
        {options.isFetching && <ReviewState state="loading" />}
        {options.data && <TextField select size="small" label="Application target" value={targetId} disabled={busy} onChange={(event) => { targetTouched.current = true; setTargetId(event.target.value); setSelected([]); }}>
          <MenuItem value="">No application target — text-only decision</MenuItem>
          {options.data.targets.map((item) => <MenuItem key={item.target.bindingId} value={item.target.bindingId}>{item.name} · configuration r{item.target.revision}</MenuItem>)}
        </TextField>}
      </Stack></Section>
      {candidate && options.data && <Section title="Evidence to share and attach">
        <Stack spacing={1.5}>
          <ShareAllEvidence packet={packet} evidence={options.data.evidence} onShare={(input) => share.mutateAsync(input)}
            onShared={async (refs) => {
              const keys = new Set(refs.map((ref) => `${ref.source}:${ref.sourceId}`));
              const fresh = refs.some((ref) => ref.visibility !== 'workspace') ? (await options.refetch()).data : options.data;
              setSelected((fresh?.evidence || []).filter((ref) => keys.has(`${ref.source}:${ref.sourceId}`) && !evidenceProblems(ref, packet).length).map(evidenceSelection));
            }} />
          <TypedEvidenceList packet={packet} evidence={options.data.evidence} selected={selected} onSelectionChange={setSelected} onShare={(input) => share.mutateAsync(input)} />
        </Stack>
      </Section>}
      <Section title="Retained external pipeline (optional)"><Stack spacing={1.5}>
        <Typography variant="s3">Operator-recorded, not a verified Hub verdict. Leave all three fields empty if no external pipeline applies.</Typography>
        <TextField size="small" label="Pipeline result URL" value={pipelineUrl} onChange={(event) => setPipelineUrl(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
        <TextField size="small" label="Pipeline configuration identity" value={pipelineConfig} onChange={(event) => setPipelineConfig(event.target.value)} disabled={busy} inputProps={{ maxLength: 255 }} />
        <TextField size="small" label="Recorded pipeline decision" value={pipelineDecision} onChange={(event) => setPipelineDecision(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
      </Stack></Section>
      <Stack direction="row" spacing={1}>
        <Button variant="outlined" disabled={busy} onClick={() => navigate(paths.dashboard.skills.reviews)}>Cancel</Button>
        <Button variant="contained" disabled={busy || failed || options.isFetching || !candidate || !options.data || !pipelineComplete || !/^[a-z0-9][a-z0-9-]{0,39}$/.test(label)} onClick={preparePacket}>Prepare exact review</Button>
      </Stack>
    </>}
  </Stack>;
}
ReleaseRequestComposer.propTypes = { scopeKey: PropTypes.string.isRequired, renewal: PropTypes.object, prefill: PropTypes.object };
