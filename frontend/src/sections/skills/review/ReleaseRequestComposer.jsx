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
import StatusBadge from 'src/components/status-badge';
import { evidenceTitle, evidenceCoverage, evidenceOutcome } from '../utils/review-display';
import EvidenceSummary from '../components/EvidenceSummary';
import TechnicalDetails from '../components/TechnicalDetails';
import { suggestedEvidence } from './evidenceSuggestions';
import { useStickyFooterHeight } from '../components/stickyFooter';
import { counted, responsiveEvidenceSx } from './reviewPresentation';
import { skillsRouteSx } from '../components/routeGutter';

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
export function ShareAllEvidence({ packet, evidence, selected = [], onShare, onShared, row = false, candidateNumber, baselineNumber }) {
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
        <Typography variant="s1">Workspace members will be able to see {shareable.length === 1 ? 'this test result' : `these ${shareable.length} test results`}, including the inputs, model answers and files used. Sharing doesn&apos;t extend their 30-day expiry. You&apos;ll review the request before it&apos;s sent.</Typography>
        {/* Every record this decision shares or attaches is named here by title with its visibility, so the
            author confirms the exact set without reading identifiers; the IDs sit under Details. */}
        {all.map((ref) => <Stack key={`${ref.source}:${ref.sourceId}`} data-evidence-id={ref.sourceId} spacing={0.5} sx={{ minWidth: 0 }}>
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><Typography variant="s1" sx={{ overflowWrap: 'anywhere' }}>{evidenceTitle(ref, candidateNumber, ref.baselineNumber ?? baselineNumber)}</Typography>{ref.visibility === 'private' ? <StatusBadge status="private" /> : <Typography variant="s3" color="text.secondary">Already shared</Typography>}</Stack>
          <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><StatusBadge {...(evidenceOutcome(ref) || { status: 'not_recorded', label: 'Not recorded', role: 'neutral' })} /><Typography variant="s3" color="text.secondary">{evidenceCoverage(ref.counts)}</Typography></Stack>
        </Stack>)}
        <TechnicalDetails title="Details">
          <Stack spacing={0.5}>{all.map((ref) => <IdentityChip key={`${ref.source}:${ref.sourceId}`} label={evidenceTitle(ref, candidateNumber, ref.baselineNumber ?? baselineNumber)} value={ref.sourceId} kind="id" />)}</Stack>
        </TechnicalDetails>
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
ShareAllEvidence.propTypes = { packet: PropTypes.object.isRequired, evidence: PropTypes.array.isRequired, onShare: PropTypes.func.isRequired, onShared: PropTypes.func.isRequired, selected: PropTypes.array, row: PropTypes.bool, candidateNumber: PropTypes.number, baselineNumber: PropTypes.number };

// Two numbered steps; the current one is bold and marked for assistive technology.
function RequestSteps({ step }) {
  const steps = ['Choose', 'Confirm'];
  return <Stack component="ol" direction="row" alignItems="center" gap={1.5} aria-label="Request steps" sx={{ m: 0, p: 0, listStyle: 'none' }}>
    {steps.map((name, index) => {
      const current = index + 1 === step;
      return <Stack component="li" key={name} direction="row" alignItems="center" gap={1} aria-current={current ? 'step' : undefined}>
        {index > 0 && <Box aria-hidden="true" sx={{ width: 24, height: '1px', bgcolor: 'divider' }} />}
        <Box component="span" aria-hidden="true" sx={{ width: 20, height: 20, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', typography: 's3', fontWeight: 600, bgcolor: current ? 'primary.main' : 'transparent', color: current ? 'primary.contrastText' : 'text.secondary', border: 1, borderColor: current ? 'primary.main' : 'divider' }}>{index + 1}</Box>
        <Typography component="span" variant="s3" sx={{ fontWeight: current ? 600 : 400, color: current ? 'text.primary' : 'text.secondary' }}>{name}</Typography>
      </Stack>;
    })}
  </Stack>;
}
RequestSteps.propTypes = { step: PropTypes.number.isRequired };

function ComposerActions({ children }) {
  const footer = useRef(null);
  useStickyFooterHeight(footer);
  return <>
    <Box aria-hidden="true" sx={{ display: { xs: 'none', sm: 'block' }, height: 'calc(var(--skills-sticky-footer-height, 0px) + 16px)' }} />
    <Button sx={{ display: { xs: 'block', sm: 'none' }, position: 'sticky', bottom: 0, zIndex: 2, bgcolor: 'background.paper', borderTop: 1, borderColor: 'divider' }} onClick={() => footer.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })}>Prepare review ↓</Button>
    <Box ref={footer} data-sticky-footer sx={{ position: { xs: 'static', sm: 'sticky' }, bottom: 0, zIndex: 2, bgcolor: 'background.paper', borderTop: 1, borderColor: 'divider', p: 1.5 }}>{children}</Box>
  </>;
}
ComposerActions.propTypes = { children: PropTypes.node };

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
  const targetOption = options.data?.targets.find((item) => item.target.bindingId === targetId);
  const target = targetOption?.target || null;
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
  // The run the author came from is marked and listed first whatever its visibility or selection.
  const carriedKeys = new Set(carried ? prefill?.evidence || [] : []);
  const fromTest = selected.filter((ref) => carriedKeys.has(evidenceKey(ref)));
  const evidenceItems = (options.data?.evidence || []);
  // Available = a settled result for this exact content that could be attached now (a private one after sharing).
  const availableRefs = candidate ? evidenceItems.filter((ref) => evidenceProblems(ref, packet).every((reason) => reason.startsWith('Private'))) : [];
  const passingUnattached = availableRefs.filter((ref) => ['pass', 'passed'].includes(evidenceOutcome(ref)?.status) && !selected.some((item) => evidenceKey(item) === evidenceKey(ref)));
  const evidenceSection = useRef(null);
  const attachResults = () => { evidenceSection.current?.scrollIntoView({ block: 'center', behavior: 'smooth' }); evidenceSection.current?.focus({ preventScroll: true }); };
  const pipelineComplete = !pipelineUrl && !pipelineConfig && !pipelineDecision || /^https:\/\//.test(pipelineUrl) && pipelineConfig.trim() && pipelineDecision.trim();
  const refresh = () => { subjects.refetch(); if (resourceId) versions.refetch(); if (candidateId) options.refetch(); };
  const labelValid = /^[a-z0-9][a-z0-9-]{0,39}$/.test(label);
  const subject = options.data?.subject || subjects.data?.results.find((item) => item.id === resourceId);
  const previousVersion = subject?.labels?.find((item) => item.name === label)?.version ?? prefill?.previousVersion;
  const alreadyCurrent = candidate?.number != null && previousVersion != null && candidate.number === previousVersion;
  const currentReason = alreadyCurrent ? `v${candidate.number} is already in ${label}. Choose another version to release.` : '';
  const preparePacket = async (evidence = selected) => {
    if (alreadyCurrent) { setError(currentReason); return; }
    setError('');
    try {
      setPrepared(await prepare.mutateAsync({ resourceId, kind, candidateId, label, target, evidence,
        externalPipeline: pipelineUrl ? { trust: 'operator_recorded_unimported', url: pipelineUrl, configuration_identity: pipelineConfig, decision: pipelineDecision, recorded_by: access.scope.userId } : null }));
    } catch { setError('Could not prepare the review. Reload the available records and inspect any changed selections. Nothing was submitted.'); }
  };
  const header = <ObjectHeader crumbs={prefill?.kind === 'agent_context' ? [{ label: 'Agent contexts', to: paths.dashboard.skills.contexts }, { label: prefill.name || 'Agent context', to: paths.dashboard.skills.contextDetail(prefill.subjectId) }, { label: 'Request release review' }] : [{ label: 'Reviews', to: paths.dashboard.skills.reviews }, { label: 'New request' }]} title={renewal ? 'Renew release review' : 'Request release review'} purpose="Choose a reviewer and inspect the changes. Production stays unchanged until approval and release." />;
  const formState = state && state !== 'ready' ? state : failed ? 'error' : subjects.isLoading ? 'loading' : resourceId && !carried && versions.data?.results.length === 0 ? 'empty' : null;
  if (formState) return <Stack spacing={3} sx={{ ...skillsRouteSx, width: 1 }}>{header}{formState === 'loading' ? <ContentSkeleton variant="panel" label="Loading request form" rows={4} /> : formState === 'error' ? <ErrorState title="Couldn't load the request form." onRetry={refresh} /> : formState === 'empty' ? <EmptyState title="No published versions yet" description="Publish a version before requesting a review." secondaryAction={<Button variant="contained" color="primary" href={paths.dashboard.skills.root}>Go to Skills</Button>} /> : <ReviewState state={formState} onRetry={refresh} />}</Stack>;
  const testsUrl = resourceId ? `${kind === 'agent_context' ? paths.dashboard.skills.contextDetail(resourceId) : paths.dashboard.skills.detail(resourceId)}?version=${candidate?.number}&tab=tests` : paths.dashboard.skills.root;
  return <Stack spacing={3} sx={{ ...skillsRouteSx, width: 1, ...responsiveEvidenceSx }}>
    {header}
    <RequestSteps step={prepared ? 2 : 1} />
    {renewal && <Alert severity="info">Renew request revision {renewal.revision}. Select and inspect current evidence explicitly; previous approval will not carry forward.</Alert>}
    {prepared ? <ReleaseRequestPanel scopeKey={scopeKey} state="ready" {...prepared}
      availableEvidence={availableRefs.length} initialRecipientId={recipientId} recipients={options.data?.recipients || []} recipientsLoading={options.isFetching} recipientsError={options.error} onRetryRecipients={options.refetch} permissions={{ canSubmit: true }}
      onEdit={() => setPrepared(null)} onCancel={() => navigate(paths.dashboard.skills.reviews)}
      onRefresh={() => { setPrepared(null); refresh(); }}
      onSubmit={async (input) => {
        const result = await submit.mutateAsync({ ...input, prepared, ...(renewal ? { requestId: renewal.id, revision: renewal.revision } : {}) });
        navigate(`${paths.dashboard.skills.reviews}?mine=1`);
        return result;
      }} /> : <>
      {failed && <ReviewState state="error" onRetry={refresh} />}
      {error && <Alert severity="error">{error}</Alert>}
      <Section title="Version to release"><Stack spacing={1.5}>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr)' }, gap: 1.5 }}>
          <TextField select size="small" label="Type" value={kind} disabled={busy || Boolean(renewal) || Boolean(prefill)} onChange={(event) => { setKind(event.target.value); changeResource(''); setSubjectPage(1); }}>
            <MenuItem value="skill">Skill</MenuItem><MenuItem value="agent_context">Agent context</MenuItem>
          </TextField>
          {prefill || renewal ? <TextField size="small" label="Name" value={prefill?.name || subject?.name || 'Selected resource'} InputProps={{ readOnly: true }} /> : <Autocomplete size="small" sx={{ '& .MuiOutlinedInput-root.MuiInputBase-sizeSmall': { py: 0, pr: '30px' }, '& .MuiAutocomplete-input': { py: '4px !important' } }} options={subjects.data?.results || []} getOptionLabel={(item) => item.name} isOptionEqualToValue={(a, b) => a.id === b.id} filterOptions={(items) => items} loading={subjects.isFetching} value={subjects.data?.results.find((item) => item.id === resourceId) || null} onChange={(_, item) => changeResource(item?.id || '')} onInputChange={(_, value, reason) => { if (reason === 'input') { setSubjectSearch(value); setSubjectPage(1); } }} renderInput={(params) => <TextField {...params} label="Name" />} />}
          <TextField size="small" label="Label" value={label} disabled={busy} onChange={(event) => setLabel(event.target.value)} inputProps={{ maxLength: 40 }} />
        </Box>
        {!prefill && !renewal && subjects.data?.total_pages > 1 && <PageButtons query={subjects} page={subjectPage} setPage={(value) => { setSubjectPage(value); changeResource(''); }} label="matches" />}
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: 'minmax(0, 1fr)', sm: 'repeat(2, minmax(0, 1fr))' }, gap: 1.5, alignItems: 'start' }}>
          {carried ? <Stack spacing={0.5}>
            <TextField size="small" label="Published candidate" value={`v${prefill.candidate.number}${alreadyCurrent ? ` (in ${label})` : ''}`} InputProps={{ readOnly: true }} helperText="The version you just published." />
            <Button size="small" sx={{ alignSelf: 'flex-start' }} disabled={busy} onClick={() => changeCandidate('')}>Choose another version</Button>
          </Stack> : resourceId && <Stack spacing={0.5}>
            <TextField select size="small" label="Published candidate" value={candidateId} disabled={busy || versions.isFetching} onChange={(event) => changeCandidate(event.target.value)}>
              <MenuItem value="">Choose a published version</MenuItem>
              {versions.data?.results.map((item) => <MenuItem key={item.id} value={item.id}>v{item.number}{item.number === previousVersion ? ` (in ${label})` : ''}{item.note ? ` — ${item.note}` : ''}</MenuItem>)}
            </TextField>
            {versions.data?.total_pages > 1 && <PageButtons query={versions} page={versionPage} setPage={(value) => { setVersionPage(value); changeCandidate(''); }} label="versions" />}
          </Stack>}
          {options.data && <TextField select size="small" label="Application target" SelectProps={{ displayEmpty: true }} InputLabelProps={{ shrink: true }} value={targetId} disabled={busy} onChange={(event) => { targetTouched.current = true; setTargetId(event.target.value); setSelected([]); }}>
            <MenuItem value="">No application target — text-only decision</MenuItem>
            {options.data.targets.map((item) => <MenuItem key={item.target.bindingId} value={item.target.bindingId}>Application target · {item.name}</MenuItem>)}
          </TextField>}
        </Box>
        {options.isFetching && !options.data && <ReviewState state="loading" />}
        {/* The release this request asks for, in one visible line: what moves, from which version, and where it was verified. */}
        {carried && <Stack role="group" aria-label="Carried release candidate" spacing={0.5} sx={{ minWidth: 0 }}><Typography variant="s1" sx={{ overflowWrap: 'anywhere' }}>{prefill.name || 'Published candidate'} · {label} {previousVersion != null ? `v${previousVersion}` : '(current version not loaded)'} → v{candidate.number}</Typography><Typography variant="s3" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>{target ? `Application verification on ${targetOption?.name || 'the selected target'} · configuration r${target.revision}` : 'Compare instructions; no application target'}</Typography></Stack>}
        {alreadyCurrent && <Typography variant="s1" role="status">{currentReason}</Typography>}
      </Stack></Section>
      {candidate && <PeoplePicker label="Assigned reviewer" people={options.data?.recipients || []} value={recipientId} onChange={setRecipientId} loading={options.isFetching} error={options.error} onRetry={options.refetch} disabled={busy} helperText="An independent reviewer approves this release." />}
      {candidate && options.data && <Box ref={evidenceSection} tabIndex={-1} sx={{ outline: 'none', scrollMarginTop: 16 }}><Section title="Evidence to share and attach">
        <Stack spacing={1.5}>
          <ShareAllEvidence candidateNumber={candidate.number} baselineNumber={previousVersion} packet={packet} evidence={options.data.evidence} selected={selected} onShare={(input) => share.mutateAsync(input)}
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
          {fromTest.length > 0 && <Typography variant="s3" role="status">{counted(fromTest.length, 'result')} attached from your test</Typography>}
          <EvidenceSummary items={[...options.data.evidence].sort((a, b) => Number(carriedKeys.has(evidenceKey(b))) - Number(carriedKeys.has(evidenceKey(a)))).map((ref) => ({
            id: ref.sourceId, source: ref.source, kind: ref.source === 'skills_text' ? 'text_comparison' : 'application_verification',
            title: evidenceTitle(ref, candidate.number, ref.baselineNumber ?? previousVersion), origin: carriedKeys.has(evidenceKey(ref)) ? 'from_test' : undefined, suggested: suggested.includes(evidenceKey(ref)), outcome: ref.outcome, counts: ref.counts,
            status: ref.status, visibility: ref.visibility, expiresAt: ref.payloadExpiresAt,
            candidateNumber: candidate.number, completedAt: ref.completedAt,
            targetMatch: ref.source === 'simulation_native' ? ref.configuration?.digest === target?.configurationDigest : undefined,
          }))} renderAction={(item) => item.visibility === 'private' && <ShareAllEvidence row candidateNumber={candidate.number} baselineNumber={previousVersion} packet={packet} evidence={options.data.evidence.filter((ref) => ref.sourceId === item.id && ref.source === item.source)} onShare={share.mutateAsync} onShared={async (refs) => {
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
          {!options.data.evidence.length && <Button href={testsUrl} sx={{ alignSelf: 'flex-start', ml: -1 }}>Run tests</Button>}
          {inspecting && <TypedEvidenceList packet={packet} evidence={options.data.evidence.filter((ref) => evidenceKey(ref) === inspecting)} selected={[]} />}
          {selected.filter((ref) => !options.data.evidence.some((item) => evidenceKey(item) === evidenceKey(ref))).map((ref) => <Alert key={evidenceKey(ref)} severity="warning">Selected evidence {ref.sourceId} is unavailable. Refresh and review again.</Alert>)}
        </Stack>
      </Section></Box>}
      <TechnicalDetails title="External pipeline (optional)"><Stack spacing={1.5}>
        <Typography variant="s3">Operator-recorded, not a verified Hub verdict. Leave all three fields empty if no external pipeline applies.</Typography>
        <TextField size="small" label="Pipeline result URL" value={pipelineUrl} onChange={(event) => setPipelineUrl(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
        <TextField size="small" label="Pipeline configuration identity" value={pipelineConfig} onChange={(event) => setPipelineConfig(event.target.value)} disabled={busy} inputProps={{ maxLength: 255 }} />
        <TextField size="small" label="Recorded pipeline decision" value={pipelineDecision} onChange={(event) => setPipelineDecision(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
      </Stack></TechnicalDetails>
      <ComposerActions><Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'center' }} justifyContent="space-between">
      {passingUnattached.length > 0 && !alreadyCurrent && candidate && pipelineComplete
        ? <Stack direction="row" alignItems="center" flexWrap="wrap" columnGap={1} role="status" sx={{ minWidth: 0 }}>
          <Typography variant="s3" sx={{ color: 'warning.dark' }}>{counted(passingUnattached.length, 'passing result')} {passingUnattached.length === 1 ? "isn't" : "aren't"} attached. The reviewer will see no test evidence.</Typography>
          <Button size="small" onClick={attachResults}>Attach results</Button>
        </Stack>
        : <Typography variant="s3" color="text.secondary">{alreadyCurrent ? currentReason : !candidate ? 'Choose a published version to continue.' : !pipelineComplete ? 'Complete all three pipeline fields or leave them empty.' : 'Next, inspect the changes and confirm before sending.'}</Typography>}
      <Stack direction="row" spacing={1}>
        <Button variant="outlined" disabled={busy} onClick={() => navigate(paths.dashboard.skills.reviews)}>Cancel</Button>
        <Button variant="contained" color="primary" disabled={alreadyCurrent || busy || failed || options.isFetching || !candidate || (Boolean(targetId) && !target) || !options.data || !pipelineComplete || !labelValid} onClick={() => preparePacket()}>Prepare review</Button>
      </Stack></Stack></ComposerActions>
    </>}
  </Stack>;
}
ReleaseRequestComposer.propTypes = { scopeKey: PropTypes.string.isRequired, renewal: PropTypes.object, prefill: PropTypes.object, state: PropTypes.string };
