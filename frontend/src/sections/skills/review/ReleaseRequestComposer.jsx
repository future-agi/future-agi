import { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Autocomplete, Box, Button, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import ObjectHeader from '../components/ObjectHeader';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import ContentSkeleton from 'src/components/content-skeleton';
import Section from 'src/components/section';
import { paths } from 'src/routes/paths';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useReviewSubjects, useReviewCandidates, useReviewOptions, usePrepareReleaseReview, useSubmitReleaseReview, useShareReleaseEvidence } from 'src/api/skills/review-authoring';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import { ConfirmDialog } from 'src/components/custom-dialog';
import { ReviewState, TypedEvidenceList, evidenceKey, evidenceProblems, evidenceSelection } from './ReleaseReviewBody';
import PeoplePicker from '../components/PeoplePicker';
import IdentityChip from '../components/IdentityChip';
import { evidenceTitle } from '../utils/review-display';
import EvidenceSummary from '../components/EvidenceSummary';
import TechnicalDetails from '../components/TechnicalDetails';
import { suggestedEvidence } from './evidenceSuggestions';

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
function ShareAllEvidence({ packet, evidence, selected = [], onShare, onShared, row = false }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const shareable = evidence.filter((ref) => {
    const reasons = evidenceProblems(ref, packet);
    return reasons.length === 1 && ref.visibility !== 'workspace';
  });
  const ready = evidence.filter((ref) => !evidenceProblems(ref, packet).length);
  const all = [...shareable, ...ready];
  if (!all.length || (!shareable.length && all.every((ref) => selected.some((item) => evidenceKey(item) === evidenceKey(ref))))) return null;
  return <Stack spacing={1}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'center' }}>
      <Button variant="outlined" disabled={busy} onClick={() => { setError(''); if (shareable.length) setOpen(true); else onShared(ready); }}>
        {row ? 'Share and attach' : shareable.length ? `Share and attach ${all.length} ${all.length === 1 ? 'result' : 'results'}` : `Attach ${all.length} ${all.length === 1 ? 'result' : 'results'}`}
      </Button>
      {!row && <Typography variant="s3">Or choose results one by one below.</Typography>}
    </Stack>
    {error && <Alert severity="error">{error}</Alert>}
    <ConfirmDialog open={open} onClose={() => { if (!busy) setOpen(false); }} closeDisabled={busy}
      title={`Share ${shareable.length} private results with the workspace?`}
      content={<Stack spacing={1}>
        <Typography variant="s1">Each frozen draft, example inputs, model answers and file receipts become visible to workspace members. Sharing does not extend expiry or release content. These results are then attached, and the exact review packet opens for you to inspect before anything is sent:</Typography>
        {all.map((ref) => <Typography key={`${ref.source}:${ref.sourceId}`} variant="s3" sx={{ overflowWrap: 'anywhere' }}>{evidenceTitle(ref)} · <IdentityChip value={ref.sourceId} kind="id" /> · {ref.visibility}</Typography>)}
      </Stack>}
      action={<Button variant="contained" color="primary" disabled={busy} onClick={async () => {
        setBusy(true);
        try {
          for (const ref of shareable) await onShare({ ...evidenceSelection(ref), acknowledgeSnapshotSharing: true });
          setOpen(false);
          await onShared(all);
        } catch { setError('Could not confirm sharing for every result. Refresh the evidence state; nothing was attached.'); } finally { setBusy(false); }
      }}>Share and attach</Button>} />
  </Stack>;
}
ShareAllEvidence.propTypes = { packet: PropTypes.object.isRequired, evidence: PropTypes.array.isRequired, onShare: PropTypes.func.isRequired, onShared: PropTypes.func.isRequired, selected: PropTypes.array, row: PropTypes.bool };

export default function ReleaseRequestComposer({ scopeKey, renewal, prefill, state }) {
  const navigate = useNavigate();
  const access = useSkillV11Scope();
  // A just-published candidate carries its identities forward; nothing is typed.
  const [kind, setKind] = useState(renewal?.kind || prefill?.kind || 'skill');
  const [resourceId, setResourceId] = useState(renewal?.subjectId || prefill?.subjectId || '');
  const [candidateId, setCandidateId] = useState(renewal ? '' : prefill?.candidate.id || '');
  const targetTouched = useRef(false);
  const [subjectPage, setSubjectPage] = useState(1);
  const [versionPage, setVersionPage] = useState(1);
  const [label, setLabel] = useState(prefill?.label || 'production');
  const [targetId, setTargetId] = useState(prefill?.target || '');
  const [selected, setSelected] = useState([]);
  const [inspecting, setInspecting] = useState(null);
  const [suggested, setSuggested] = useState([]);
  const [recipientId, setRecipientId] = useState('');
  const [editingCandidate, setEditingCandidate] = useState(!prefill);
  const [subjectSearch, setSubjectSearch] = useState('');
  const suggestedFor = useRef(null);
  const [pipelineUrl, setPipelineUrl] = useState('');
  const [pipelineConfig, setPipelineConfig] = useState('');
  const [pipelineDecision, setPipelineDecision] = useState('');
  const [prepared, setPrepared] = useState(null);
  const [error, setError] = useState('');
  const subjects = useReviewSubjects(kind, subjectPage, subjectSearch);
  const versions = useReviewCandidates(kind, resourceId, versionPage);
  const options = useReviewOptions(kind, resourceId, candidateId);
  const prepare = usePrepareReleaseReview();
  const submit = useSubmitReleaseReview();
  const share = useShareReleaseEvidence(resourceId, kind);
  // The carried-forward candidate does not depend on which version page loaded.
  const carried = prefill && !renewal && candidateId === prefill.candidate.id && resourceId === prefill.subjectId;
  const candidate = carried ? prefill.candidate : versions.data?.results.find((item) => item.id === candidateId);
  const target = options.data?.targets.find((item) => item.target.bindingId === targetId)?.target || null;
  const changeResource = (value) => { setResourceId(value); setCandidateId(''); setVersionPage(1); setSelected([]); setTargetId(''); setError(''); };
  const changeCandidate = (value) => { setCandidateId(value); setSelected([]); setTargetId(''); targetTouched.current = false; setError(''); };
  // Exactly one registered target is preselected (visible, changeable).
  const onlyTarget = options.data?.targets.length === 1 ? options.data.targets[0].target.bindingId : '';
  useEffect(() => { if (onlyTarget && !targetTouched.current && !targetId) setTargetId(onlyTarget); }, [onlyTarget, targetId]);
  const busy = prepare.isPending;
  const failed = subjects.isError || versions.isError || options.isError;
  const packet = { subjectId: resourceId, subjectKind: kind, candidateDigest: candidate?.digest, target };
  useEffect(() => {
    if (!options.data || options.isFetching || !candidate) return;
    const key = JSON.stringify([scopeKey, kind, resourceId, candidateId, targetId]);
    if (suggestedFor.current === key) return;
    suggestedFor.current = key;
    const refs = renewal ? [] : suggestedEvidence(options.data.evidence,
      { subjectId: resourceId, subjectKind: kind, candidateDigest: candidate.digest, target }, carried ? prefill?.evidence : []);
    setSelected(refs);
    setSuggested(refs.map(evidenceKey));
    setInspecting(null);
  }, [scopeKey, kind, resourceId, candidateId, targetId, target, candidate, options.data, options.isFetching, renewal, carried, prefill?.evidence]);
  const pipelineComplete = !pipelineUrl && !pipelineConfig && !pipelineDecision || /^https:\/\//.test(pipelineUrl) && pipelineConfig.trim() && pipelineDecision.trim();
  const refresh = () => { subjects.refetch(); if (resourceId) versions.refetch(); if (candidateId) options.refetch(); };
  const labelValid = /^[a-z0-9][a-z0-9-]{0,39}$/.test(label);
  const preparePacket = async (evidence = selected) => {
    setError('');
    try {
      setPrepared(await prepare.mutateAsync({ resourceId, kind, candidateId, label, target, evidence,
        externalPipeline: pipelineUrl ? { trust: 'operator_recorded_unimported', url: pipelineUrl, configuration_identity: pipelineConfig, decision: pipelineDecision, recorded_by: access.scope.userId } : null }));
    } catch { setError('Could not prepare the exact review. Reload the available records and inspect any changed selections. Nothing was submitted.'); }
  };
  const header = <ObjectHeader crumbs={[{ label: 'Reviews', to: paths.dashboard.skills.reviews }, { label: 'New request' }]} title={renewal ? 'Renew release review' : 'Request release review'} purpose="Choose a reviewer and inspect the changes. Production stays unchanged until approval and release." />;
  const formState = state && state !== 'ready' ? state : failed ? 'error' : subjects.isLoading ? 'loading' : resourceId && !carried && versions.data?.results.length === 0 ? 'empty' : null;
  if (formState) return <Stack spacing={3} sx={{ px: { xs: 2, md: 3 }, py: 2, maxWidth: 1000 }}>{header}{formState === 'loading' ? <ContentSkeleton variant="panel" label="Loading request form" rows={4} /> : formState === 'error' ? <ErrorState title="Couldn't load the request form." onRetry={refresh} /> : formState === 'empty' ? <EmptyState title="No published versions yet" description="Publish a version before requesting a review." secondaryAction={<Button variant="contained" color="primary" href={paths.dashboard.skills.root}>Go to Skills</Button>} /> : <ReviewState state={formState} onRetry={refresh} />}</Stack>;
  const subject = options.data?.subject || subjects.data?.results.find((item) => item.id === resourceId);
  const previousVersion = subject?.labels?.find((item) => item.name === label)?.version ?? prefill?.previousVersion;
  const testsUrl = resourceId ? `${kind === 'agent_context' ? paths.dashboard.skills.contextDetail(resourceId) : paths.dashboard.skills.detail(resourceId)}?version=${candidate?.number}&tab=tests` : paths.dashboard.skills.root;
  return <Stack spacing={3} sx={{ px: { xs: 2, sm: 3 }, py: 2, minWidth: 0, maxWidth: 1000 }}>
    {header}
    {renewal && <Alert severity="info">Renew request revision {renewal.revision}. Select and inspect current evidence explicitly; previous approval will not carry forward.</Alert>}
    {prepared ? <ReleaseRequestPanel scopeKey={scopeKey} state="ready" {...prepared}
      initialRecipientId={recipientId} recipients={options.data?.recipients || []} recipientsLoading={options.isFetching} recipientsError={options.error} onRetryRecipients={options.refetch} permissions={{ canSubmit: true }}
      onRefresh={() => { setPrepared(null); refresh(); }}
      onSubmit={async (input) => {
        const result = await submit.mutateAsync({ ...input, prepared, ...(renewal ? { requestId: renewal.id, revision: renewal.revision } : {}) });
        navigate(`${paths.dashboard.skills.reviews}?mine=1`);
        return result;
      }} /> : <>
      {failed && <ReviewState state="error" onRetry={refresh} />}
      {error && <Alert severity="error">{error}</Alert>}
      <Section title="Release candidate"><Stack spacing={1.5}>
        {carried && !editingCandidate ? <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><Stack spacing={0.5}><Typography variant="m3">{prefill.name || 'Published candidate'} · {label} {previousVersion != null ? `v${previousVersion}` : '(current version not loaded)'} → v{candidate.number}</Typography><Typography variant="s3" color="text.secondary">{target ? 'Application verification' : 'Compare instructions; no application target'}</Typography></Stack><Button onClick={() => setEditingCandidate(true)}>Change</Button></Stack> : <>

        <TextField select size="small" label="Resource kind" value={kind} disabled={busy || Boolean(renewal)} onChange={(event) => { setKind(event.target.value); changeResource(''); setSubjectPage(1); }}>
          <MenuItem value="skill">Skill</MenuItem><MenuItem value="agent_context">Agent context</MenuItem>
        </TextField>
        {carried ? <>
          <TextField size="small" label="Release resource" value={prefill.name || prefill.subjectId} InputProps={{ readOnly: true }} />
          <TextField size="small" label="Published candidate" value={`v${prefill.candidate.number}`} InputProps={{ readOnly: true }} helperText="Carried forward from the publication you just reviewed." />
          <Stack direction="row"><Button size="small" disabled={busy} onClick={() => changeCandidate('')}>Choose another version</Button></Stack>
        </> : renewal ? <Typography variant="s1">Renewing the same resource. Choose its immutable candidate below.</Typography> : <>
          <Autocomplete size="small" options={subjects.data?.results || []} getOptionLabel={(item) => item.name} isOptionEqualToValue={(a, b) => a.id === b.id} filterOptions={(items) => items} loading={subjects.isFetching} value={subjects.data?.results.find((item) => item.id === resourceId) || null} onChange={(_, item) => changeResource(item?.id || '')} onInputChange={(_, value, reason) => { if (reason === 'input') { setSubjectSearch(value); setSubjectPage(1); } }} renderInput={(params) => <TextField {...params} label="Release resource" />} />
          {subjects.data?.total_pages > 1 && <PageButtons query={subjects} page={subjectPage} setPage={(value) => { setSubjectPage(value); changeResource(''); }} label="matches" />}
        </>}
        {resourceId && !carried && <>
          <TextField select size="small" label="Published candidate" value={candidateId} disabled={busy || versions.isFetching} onChange={(event) => changeCandidate(event.target.value)}>
            <MenuItem value="">Choose an immutable version</MenuItem>
            {versions.data?.results.map((item) => <MenuItem key={item.id} value={item.id}>v{item.number}{item.note ? ` — ${item.note}` : ''}</MenuItem>)}
          </TextField>
          <PageButtons query={versions} page={versionPage} setPage={(value) => { setVersionPage(value); changeCandidate(''); }} label="versions" />
        </>}
        <TextField size="small" label="Release label" value={label} disabled={busy} onChange={(event) => setLabel(event.target.value)} inputProps={{ maxLength: 40 }} />
        {options.isFetching && !options.data && <ReviewState state="loading" />}
        {options.data && <TextField select size="small" label="Application target" SelectProps={{ displayEmpty: true }} InputLabelProps={{ shrink: true }} value={targetId} disabled={busy} onChange={(event) => { targetTouched.current = true; setTargetId(event.target.value); setSelected([]); }}>
          <MenuItem value="">No application target — text-only decision</MenuItem>
          {options.data.targets.map((item) => <MenuItem key={item.target.bindingId} value={item.target.bindingId}>{item.name} · configuration r{item.target.revision}</MenuItem>)}
        </TextField>}
        </>}
      </Stack></Section>
      {candidate && <PeoplePicker label="Assigned reviewer" people={options.data?.recipients || []} value={recipientId} onChange={setRecipientId} loading={options.isFetching} error={options.error} onRetry={options.refetch} disabled={busy} helperText="An independent reviewer approves this release." />}
      {candidate && options.data && <Section title="Evidence to share and attach">
        <Stack spacing={1.5}>
          <ShareAllEvidence packet={packet} evidence={options.data.evidence} selected={selected} onShare={(input) => share.mutateAsync(input)}
            onShared={async (refs) => {
              const keys = new Set(refs.map((ref) => `${ref.source}:${ref.sourceId}`));
              const fresh = refs.some((ref) => ref.visibility !== 'workspace') ? (await options.refetch()).data : options.data;
              const attached = (fresh?.evidence || []).filter((ref) => keys.has(`${ref.source}:${ref.sourceId}`) && !evidenceProblems(ref, packet).length).map(evidenceSelection);
              setSelected(attached);
              // J4: the batch decision continues straight to the exact packet
              // the author must inspect and attest; nothing is sent here. An
              // incomplete optional pipeline record keeps the form open instead.
              if (attached.length && pipelineComplete && labelValid) await preparePacket(attached);
            }} />
          <EvidenceSummary items={options.data.evidence.map((ref) => ({
            id: ref.sourceId, source: ref.source, kind: ref.source === 'skills_text' ? 'text_comparison' : 'application_verification',
            title: evidenceTitle(ref, candidate.number, ref.baselineNumber ?? previousVersion), suggested: suggested.includes(evidenceKey(ref)), outcome: ref.outcome, counts: ref.counts,
            status: ref.status, visibility: ref.visibility, expiresAt: ref.payloadExpiresAt,
            candidateNumber: candidate.number, completedAt: ref.completedAt,
            targetMatch: ref.source === 'simulation_native' ? ref.configuration?.digest === target?.configurationDigest : undefined,
          }))} renderAction={(item) => item.visibility === 'private' && <ShareAllEvidence row packet={packet} evidence={options.data.evidence.filter((ref) => ref.sourceId === item.id && ref.source === item.source)} onShare={share.mutateAsync} onShared={async (refs) => {
              const fresh = (await options.refetch()).data;
              const attached = fresh?.evidence.find((ref) => evidenceKey(ref) === evidenceKey(refs[0]) && !evidenceProblems(ref, packet).length);
              if (attached) setSelected((previous) => [...previous.filter((ref) => evidenceKey(ref) !== evidenceKey(attached)), evidenceSelection(attached)]);
            }} />} selectable selected={selected.map(evidenceKey)} onInspect={(item) => setInspecting(`${item.source}:${item.id}`)}
            onToggle={(item) => {
              const key = `${item.source}:${item.id}`;
              const ref = options.data.evidence.find((entry) => evidenceKey(entry) === key);
              if (selected.some((entry) => evidenceKey(entry) === key)) setSelected(selected.filter((entry) => evidenceKey(entry) !== key));
              else if (ref) {
                const reasons = evidenceProblems(ref, packet);
                if (reasons.length) { setError(reasons.join('. ')); setInspecting(key); }
                else { setError(''); setSelected([...selected, evidenceSelection(ref)]); }
              }
            }} />
          {!options.data.evidence.length && <Button href={testsUrl} sx={{ alignSelf: 'flex-start' }}>Run tests</Button>}
          {inspecting && <TypedEvidenceList packet={packet} evidence={options.data.evidence.filter((ref) => evidenceKey(ref) === inspecting)} selected={[]} />}
          {selected.filter((ref) => !options.data.evidence.some((item) => evidenceKey(item) === evidenceKey(ref))).map((ref) => <Alert key={evidenceKey(ref)} severity="warning">Selected evidence {ref.sourceId} is unavailable. Refresh and review again.</Alert>)}
        </Stack>
      </Section>}
      <TechnicalDetails title="External pipeline (optional)"><Stack spacing={1.5}>
        <Typography variant="s3">Operator-recorded, not a verified Hub verdict. Leave all three fields empty if no external pipeline applies.</Typography>
        <TextField size="small" label="Pipeline result URL" value={pipelineUrl} onChange={(event) => setPipelineUrl(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
        <TextField size="small" label="Pipeline configuration identity" value={pipelineConfig} onChange={(event) => setPipelineConfig(event.target.value)} disabled={busy} inputProps={{ maxLength: 255 }} />
        <TextField size="small" label="Recorded pipeline decision" value={pipelineDecision} onChange={(event) => setPipelineDecision(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
      </Stack></TechnicalDetails>
      <Box sx={{ position: 'sticky', bottom: 0, bgcolor: 'background.default', borderTop: 1, borderColor: 'divider', py: 1.5 }}><Stack spacing={1}>
      <Typography variant="s3" color="text.secondary">{!candidate ? 'Choose a published candidate to continue.' : !pipelineComplete ? 'Complete all three pipeline fields or leave them empty.' : 'Next, inspect the exact changes and confirm before sending.'}</Typography>
      <Stack direction="row" spacing={1}>
        <Button variant="outlined" disabled={busy} onClick={() => navigate(paths.dashboard.skills.reviews)}>Cancel</Button>
        <Button variant="contained" color="primary" disabled={busy || failed || options.isFetching || !candidate || (Boolean(targetId) && !target) || !options.data || !pipelineComplete || !labelValid} onClick={() => preparePacket()}>Prepare exact review</Button>
      </Stack></Stack></Box>
    </>}
  </Stack>;
}
ReleaseRequestComposer.propTypes = { scopeKey: PropTypes.string.isRequired, renewal: PropTypes.object, prefill: PropTypes.object, state: PropTypes.string };
