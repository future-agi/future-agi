import { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Autocomplete, Box, Button, MenuItem, Stack, TextField, Tooltip, Typography } from '@mui/material';
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
import { PRIVATE_RESULTS_NOTE, ReviewState, TypedEvidenceList, evidenceKey, evidenceProblems, evidenceSelection } from './ReleaseReviewBody';
import DecisionFooterRow from './DecisionFooterRow';
import PeoplePicker from '../components/PeoplePicker';
import ActionReason, { reasonProps } from '../components/ActionReason';
import StatusBadge from 'src/components/status-badge';
import { evidenceTitle, evidenceCoverage, evidenceOutcome } from '../utils/review-display';
import EvidenceSummary from '../components/EvidenceSummary';
import TechnicalDetails from '../components/TechnicalDetails';
import { suggestedEvidence } from './evidenceSuggestions';
import { useStickyFooterHeight } from '../components/stickyFooter';
import { counted, responsiveEvidenceSx, unchangedUntilRelease } from './reviewPresentation';
import { skillsRouteSx } from '../components/routeGutter';
import ReleaseRuleNote from '../components/ReleaseRuleNote';
import { COMPOSER_CONTINUE, RENEW_REQUEST, requestReleaseFor } from '../utils/terms';

// Intent: choose immutable records by name, inspect a frozen packet, then assign
// an independent reviewer. Existing page/section/form tokens; no identity entry.
function PageButtons({ query, page, setPage, label }) {
  return <Stack direction="row" spacing={1}>
    <Button disabled={page <= 1 || query.isFetching} onClick={() => setPage(page - 1)}>Previous {label}</Button>
    <Button disabled={!query.data || page >= query.data.total_pages || query.isFetching} onClick={() => setPage(page + 1)}>Next {label}</Button>
  </Stack>;
}
PageButtons.propTypes = { query: PropTypes.object.isRequired, page: PropTypes.number.isRequired, setPage: PropTypes.func.isRequired, label: PropTypes.string.isRequired };

// J4 sharing consent (UXF-17): one explicit decision names every result that
// becomes workspace-visible and every result attached. Nothing private is
// shared implicitly; cancelling shares and attaches nothing. Opened for one
// row when a private row is ticked, or for every selected result from the one
// bulk button. Opened from Continue (onSkip given), it also offers the explicit
// second choice of continuing without the private results; that shares nothing.
export function ShareConsentDialog({ open, share = [], attach = [], onShare, onShared, onClose, onSkip, candidateNumber, baselineNumber }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const all = [...share, ...attach.filter((ref) => !share.some((item) => evidenceKey(item) === evidenceKey(ref)))];
  return <ConfirmDialog open={open} onClose={() => { if (!busy) { setError(''); onClose(); } }} closeDisabled={busy}
    title={`Share ${counted(share.length, 'private result')} with the workspace?`}
    content={<Stack spacing={1}>
      <Typography variant="s1">Workspace members will be able to see {share.length === 1 ? 'this test result' : `these ${share.length} test results`}, including the inputs, model answers and files used. Sharing doesn&apos;t extend their 30-day expiry. You&apos;ll review the request before it&apos;s sent.</Typography>
      {/* Every record this decision shares or attaches is named here by title with its visibility, so the
          author confirms the exact set without reading identifiers; each full ID is one hover or focus away. */}
      {all.map((ref) => <Stack key={evidenceKey(ref)} data-evidence-id={ref.sourceId} spacing={0.5} sx={{ minWidth: 0 }}>
        <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><Tooltip title={ref.sourceId} describeChild><Typography variant="s1" tabIndex={0} sx={{ overflowWrap: 'anywhere' }}>{evidenceTitle(ref, candidateNumber, ref.baselineNumber ?? baselineNumber)}</Typography></Tooltip>{ref.visibility === 'private' ? <StatusBadge status="private" role="neutral" /> : <Typography variant="s3" color="text.secondary">Already shared</Typography>}</Stack>
        <Stack direction="row" gap={1} alignItems="center" flexWrap="wrap"><StatusBadge {...(evidenceOutcome(ref) || { status: 'not_recorded', label: 'Not recorded', role: 'neutral' })} /><Typography variant="s3" color="text.secondary">{evidenceCoverage(ref.counts)}</Typography></Stack>
      </Stack>)}
      {error && <Alert severity="error">{error}</Alert>}
    </Stack>}
    action={<>
      {onSkip && <Button size="small" variant="outlined" color="inherit" disabled={busy} onClick={() => { setError(''); onSkip(); }}>
        {share.length === 1 ? 'Continue without it' : 'Continue without them'}
      </Button>}
      <Button variant="contained" color="primary" disabled={busy || !share.length} onClick={async () => {
        setBusy(true);
        setError('');
        try {
          for (const ref of share) await onShare({ ...evidenceSelection(ref), acknowledgeSnapshotSharing: true });
          await onShared(all);
          onClose();
        } catch { setError('Could not confirm sharing for every result. Refresh the evidence state; nothing was attached.'); } finally { setBusy(false); }
      }}>Share and attach</Button>
    </>} />;
}
ShareConsentDialog.propTypes = { open: PropTypes.bool.isRequired, share: PropTypes.array, attach: PropTypes.array, onShare: PropTypes.func.isRequired, onShared: PropTypes.func.isRequired, onClose: PropTypes.func.isRequired, onSkip: PropTypes.func, candidateNumber: PropTypes.number, baselineNumber: PropTypes.number };

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
    <Button sx={{ display: { xs: 'block', sm: 'none' }, position: 'sticky', bottom: 0, zIndex: 2, bgcolor: 'background.paper', borderTop: 1, borderColor: 'divider' }} onClick={() => footer.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })}>{COMPOSER_CONTINUE} ↓</Button>
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
  // Private results ticked but not yet shared ("From your test"). Shown ticked;
  // never attached or sent until the person confirms sharing them.
  const [pendingShare, setPendingShare] = useState([]);
  const [consent, setConsent] = useState(null);
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
  const changeResource = (value) => { setResourceId(value); setCandidateId(''); setVersionPage(1); setSelected([]); setPendingShare([]); setTargetId(''); setError(''); };
  const changeCandidate = (value) => { setCandidateId(value); setSelected([]); setPendingShare([]); setTargetId(''); targetTouched.current = false; setError(''); };
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
    const scopePacket = { subjectId: resourceId, subjectKind: kind, candidateDigest: candidate.digest, target };
    const refs = renewal ? [] : suggestedEvidence(options.data.evidence, scopePacket, carried ? prefill?.evidence : []);
    // The run the author came from is pre-ticked even when private; it waits for consent before it is attached.
    const waiting = renewal || !carried ? [] : options.data.evidence.filter((ref) => (prefill?.evidence || []).includes(evidenceKey(ref))
      && ref.visibility === 'private' && evidenceProblems(ref, scopePacket).every((reason) => reason.startsWith('Private')));
    setSelected(refs);
    setPendingShare(waiting.map(evidenceKey));
    setSuggested(refs.map(evidenceKey));
    setInspecting(null);
  }, [scopeKey, kind, resourceId, candidateId, targetId, target, candidate, options.data, options.isFetching, renewal, carried, prefill?.evidence]);
  // The run the author came from is marked and listed first whatever its visibility or selection.
  const carriedKeys = new Set(carried ? prefill?.evidence || [] : []);
  const fromTest = selected.filter((ref) => carriedKeys.has(evidenceKey(ref)));
  const evidenceItems = (options.data?.evidence || []);
  // One explanation per table, never one per row; the run the author came from gets its own line when it is private.
  const hasPrivate = evidenceItems.some((ref) => ref.visibility === 'private');
  // Available = a settled result for this exact content that could be attached now (a private one after sharing).
  const availableRefs = candidate ? evidenceItems.filter((ref) => evidenceProblems(ref, packet).every((reason) => reason.startsWith('Private'))) : [];
  const pendingRefs = evidenceItems.filter((ref) => pendingShare.includes(evidenceKey(ref)));
  const evidenceSection = useRef(null);
  const attachResults = () => { evidenceSection.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' }); evidenceSection.current?.focus?.({ preventScroll: true }); };
  // "Attach" on the confirm step returns here and lands on the evidence.
  const [backToEvidence, setBackToEvidence] = useState(false);
  useEffect(() => { if (backToEvidence && !prepared) { setBackToEvidence(false); attachResults(); } });
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
    } catch { setError('Could not prepare the request. Reload the available records and inspect any changed selections. Nothing was submitted.'); }
  };
  const protectedNames = [subject?.protected_label_names, subject?.protectedLabelNames].find(Array.isArray) || null;
  const requestTitle = candidate?.number != null ? requestReleaseFor(candidate.number) : 'Request release';
  // The label the request moves names itself in the lead ("Staging stays unchanged…"), never a fixed "Production".
  const unchanged = labelValid ? ` ${unchangedUntilRelease(label)}` : '';
  const header = <ObjectHeader crumbs={prefill?.kind === 'agent_context' ? [{ label: 'Agent contexts', to: paths.dashboard.skills.contexts }, { label: prefill.name || subject?.name || 'Agent context', to: paths.dashboard.skills.contextDetail(prefill.subjectId) }, { label: 'Request release' }] : [{ label: 'Reviews', to: paths.dashboard.skills.reviews }, { label: 'New request' }]} title={renewal ? RENEW_REQUEST : prepared ? 'Confirm and send' : requestTitle} purpose={prepared ? `Inspect the changes and confirm before sending.${unchanged}` : `Choose a reviewer and inspect the changes.${unchanged}`} />;
  const formState = state && state !== 'ready' ? state : failed ? 'error' : subjects.isLoading ? 'loading' : resourceId && !carried && versions.data?.results.length === 0 ? 'empty' : null;
  if (formState) return <Stack spacing={3} sx={{ ...skillsRouteSx, width: 1 }}>{header}{formState === 'loading' ? <ContentSkeleton variant="panel" label="Loading request form" rows={4} /> : formState === 'error' ? <ErrorState title="Couldn't load the request form." onRetry={refresh} /> : formState === 'empty' ? <EmptyState title="No published versions yet" description="Publish a version before requesting a review." secondaryAction={<Button variant="contained" color="primary" href={paths.dashboard.skills.root}>Go to Skills</Button>} /> : <ReviewState state={formState} onRetry={refresh} />}</Stack>;
  // UXF-17: Continue waits for a reviewer; no evidence is a warning beside it, never a block.
  const reviewerChosen = Boolean(recipientId) && (options.data?.recipients || []).some((person) => person.id === recipientId);
  const prepareReady = !(alreadyCurrent || busy || failed || options.isFetching || !candidate || (Boolean(targetId) && !target) || !options.data || !pipelineComplete || !labelValid || !reviewerChosen);
  const prepareReason = alreadyCurrent ? currentReason : !candidate ? 'Choose a published version to continue.' : !pipelineComplete ? 'Complete all three pipeline fields or leave them empty.' : options.data && !reviewerChosen ? 'Choose a reviewer' : '';
  // Attach what the person confirmed sharing: re-read the options so each result carries its shared state.
  const attachShared = async (refs) => {
    const keys = new Set(refs.map(evidenceKey));
    const fresh = refs.some((ref) => ref.visibility !== 'workspace') ? (await options.refetch()).data : options.data;
    const attached = (fresh?.evidence || []).filter((ref) => keys.has(evidenceKey(ref)) && !evidenceProblems(ref, packet).length).map(evidenceSelection);
    setSelected((previous) => [...previous.filter((ref) => !keys.has(evidenceKey(ref))), ...attached]);
    setPendingShare((previous) => previous.filter((key) => !keys.has(key)));
    return attached;
  };
  // UXF-17: a ticked private row is never sent unshared. Continue with ticked private rows opens the
  // same share consent for them, with "Continue without" as the explicit second choice; closing it does
  // neither. Only a confirmed share attaches them, and the request is prepared with what is attached.
  const continueRequest = () => {
    if (!pendingRefs.length) { preparePacket(); return; }
    setConsent({ share: pendingRefs, attach: options.data.evidence.filter((ref) => selected.some((item) => evidenceKey(item) === evidenceKey(ref))), proceed: true });
  };
  const sharedThenContinue = async (refs) => {
    const keys = new Set(refs.map(evidenceKey));
    const attached = await attachShared(refs);
    const attachedKeys = new Set(attached.map(evidenceKey));
    // A shared result that can no longer be attached (expired, changed) keeps the person on this step.
    if (consent?.share.some((ref) => !attachedKeys.has(evidenceKey(ref)))) {
      setError('A shared result could not be attached. Check the evidence below, then continue.');
      return;
    }
    await preparePacket([...selected.filter((ref) => !keys.has(evidenceKey(ref))), ...attached]);
  };
  const continueWithout = () => {
    // The skipped rows are unticked, so the page shows exactly what the request carries.
    setPendingShare([]);
    setConsent(null);
    preparePacket(selected);
  };
  const selectedCount = selected.length + pendingRefs.filter((ref) => !selected.some((item) => evidenceKey(item) === evidenceKey(ref))).length;
  const carriedPending = pendingRefs.some((ref) => carriedKeys.has(evidenceKey(ref)));
  // One reading of the evidence for the footer, the helper line and Continue (UXF-17): ticked but
  // private rows are named as not attached; no evidence is a warning beside Continue, never a block.
  const evidenceState = !candidate || !options.data ? { text: '' }
    : pendingRefs.length ? { warning: true, text: `${counted(pendingRefs.length, 'ticked result')} not attached until shared${selected.length ? ` · ${counted(selected.length, 'test result')} attached` : ''}. Continue asks you to share ${pendingRefs.length === 1 ? 'it' : 'them'}.` }
      : selected.length === 0 ? { warning: true, text: 'No test evidence attached' }
        : { text: `${counted(selected.length, 'test result')} attached.${prepareReady ? ' Next, inspect the changes and confirm before sending.' : ''}` };
  const testsUrl = resourceId ? `${kind === 'agent_context' ? paths.dashboard.skills.contextDetail(resourceId) : paths.dashboard.skills.detail(resourceId)}?version=${candidate?.number}&tab=tests` : paths.dashboard.skills.root;
  return <Stack spacing={3} sx={{ ...skillsRouteSx, width: 1, ...responsiveEvidenceSx }}>
    {header}
    <RequestSteps step={prepared ? 2 : 1} />
    {renewal && <Alert severity="info">Renew request revision {renewal.revision}. Select and inspect current evidence explicitly; previous approval will not carry forward.</Alert>}
    {prepared ? <ReleaseRequestPanel scopeKey={scopeKey} state="ready" {...prepared}
      availableEvidence={availableRefs.length} initialRecipientId={recipientId} recipients={options.data?.recipients || []} recipientsLoading={options.isFetching} recipientsError={options.error} onRetryRecipients={options.refetch} permissions={{ canSubmit: true }}
      onEdit={() => setPrepared(null)} onEditEvidence={() => { setPrepared(null); setBackToEvidence(true); }} onCancel={() => navigate(paths.dashboard.skills.reviews)}
      onRefresh={() => { setPrepared(null); refresh(); }}
      onSubmit={async (input) => {
        const result = await submit.mutateAsync({ ...input, prepared, ...(renewal ? { requestId: renewal.id, revision: renewal.revision } : {}) });
        navigate(`${paths.dashboard.skills.reviews}?mine=1`);
        return result;
      }} /> : <>
      {failed && <ReviewState state="error" onRetry={refresh} />}
      {error && <Alert severity="error">{error}</Alert>}
      <Section title="Release details"><Stack spacing={1.5}>
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
            <TextField size="small" label="Version to release" value={`v${prefill.candidate.number}${alreadyCurrent ? ` (in ${label})` : ''}`} InputProps={{ readOnly: true }} helperText="The version you just published." />
            <Button size="small" sx={{ alignSelf: 'flex-start' }} disabled={busy} onClick={() => changeCandidate('')}>Choose another version</Button>
          </Stack> : resourceId && <Stack spacing={0.5}>
            <TextField select size="small" label="Version to release" value={candidateId} disabled={busy || versions.isFetching} onChange={(event) => changeCandidate(event.target.value)}>
              <MenuItem value="">Choose a published version</MenuItem>
              {versions.data?.results.map((item) => <MenuItem key={item.id} value={item.id}>v{item.number}{item.number === previousVersion ? ` (in ${label})` : ''}{item.note ? ` — ${item.note}` : ''}</MenuItem>)}
            </TextField>
            {versions.data?.total_pages > 1 && <PageButtons query={versions} page={versionPage} setPage={(value) => { setVersionPage(value); changeCandidate(''); }} label="versions" />}
          </Stack>}
          {options.data && <TextField select size="small" label="Application target" SelectProps={{ displayEmpty: true }} InputLabelProps={{ shrink: true }} value={targetId} disabled={busy} onChange={(event) => { targetTouched.current = true; setTargetId(event.target.value); setSelected([]); }}>
            <MenuItem value="">No application target — text-only decision</MenuItem>
            {options.data.targets.map((item) => <MenuItem key={item.target.bindingId} value={item.target.bindingId}>{(item.name || 'Application').replace(/\s+application$/i, ' app')}</MenuItem>)}
          </TextField>}
        </Box>
        {options.isFetching && !options.data && <ReviewState state="loading" />}
        {/* The release this request asks for, in one visible line: what moves, from which version, and where it was verified. */}
        {carried && <Stack role="group" aria-label="Carried release candidate" spacing={0.5} sx={{ minWidth: 0 }}><Typography variant="s1" sx={{ overflowWrap: 'anywhere' }}>{(prefill.name || subject?.name) ? `${prefill.name || subject.name} · ` : ''}{label} {previousVersion != null ? `v${previousVersion}` : Array.isArray(subject?.labels) ? '(no release yet)' : '(current version not loaded)'} → v{candidate.number}</Typography><Typography variant="s3" color="text.secondary" sx={{ overflowWrap: 'anywhere' }}>{target ? `Application test on ${targetOption?.name || 'the selected target'}` : 'Compare instructions; no application target'}</Typography></Stack>}
        {alreadyCurrent && <Typography variant="s1" role="status">{currentReason}</Typography>}
      </Stack></Section>
      {candidate && <PeoplePicker label="Assigned reviewer" people={options.data?.recipients || []} value={recipientId} onChange={setRecipientId} loading={options.isFetching} error={options.error} onRetry={options.refetch} disabled={busy} helperText="An independent reviewer approves this release." />}
      {/* UXF-09: the rule that applies here, built from what the workspace returned (never hard-coded label names). */}
      {candidate && <ReleaseRuleNote rules={{ label, ...(protectedNames ? { protectedLabels: protectedNames } : {}), reviewRequired: true, subjectKind: kind }} />}
      {candidate && options.data && <Box ref={evidenceSection} tabIndex={-1} sx={{ outline: 'none', scrollMarginTop: 16 }}><Section title="Evidence to share and attach">
        <Stack spacing={1.5}>
          {/* UXF-17: one line above the table; each row has a checkbox and a neutral Private tag. */}
          {hasPrivate && <Stack spacing={0.5}>
            <Typography variant="s3" color="text.secondary">{PRIVATE_RESULTS_NOTE}</Typography>
            {carriedPending && <Typography variant="s3" color="text.secondary">Your test result from Tests is ticked but private, so it isn&apos;t attached until you share it.</Typography>}
          </Stack>}
          {/* The one bulk button: share the ticked private results and attach everything selected. */}
          {pendingRefs.length > 0 && <Button variant="outlined" size="small" sx={{ alignSelf: 'flex-start' }} disabled={busy}
            onClick={() => setConsent({ share: pendingRefs, attach: [...options.data.evidence.filter((ref) => selected.some((item) => evidenceKey(item) === evidenceKey(ref)))] })}>
            Share and attach {selectedCount} selected
          </Button>}
          {fromTest.length > 0 && <Typography variant="s3" role="status">{counted(fromTest.length, 'result')} attached from your test</Typography>}
          <EvidenceSummary rowActions privateSelectable items={[...options.data.evidence].sort((a, b) => Number(carriedKeys.has(evidenceKey(b))) - Number(carriedKeys.has(evidenceKey(a)))).map((ref) => ({
            id: ref.sourceId, source: ref.source, kind: ref.source === 'skills_text' ? 'text_comparison' : 'application_verification',
            title: evidenceTitle(ref, candidate.number, ref.baselineNumber ?? previousVersion), origin: carriedKeys.has(evidenceKey(ref)) ? 'from_test' : undefined, suggested: suggested.includes(evidenceKey(ref)), outcome: ref.outcome, counts: ref.counts,
            status: ref.status, visibility: ref.visibility, expiresAt: ref.payloadExpiresAt,
            candidateNumber: candidate.number, completedAt: ref.completedAt,
            targetMatch: ref.source === 'simulation_native' ? ref.configuration?.digest === target?.configurationDigest : undefined,
          }))} selectable selected={[...selected.map(evidenceKey), ...pendingShare]} onInspect={(item) => setInspecting(`${item.source}:${item.id}`)}
            onToggle={(item) => {
              const key = `${item.source}:${item.id}`;
              const ref = options.data.evidence.find((entry) => evidenceKey(entry) === key);
              if (pendingShare.includes(key)) setPendingShare(pendingShare.filter((entry) => entry !== key));
              else if (selected.some((entry) => evidenceKey(entry) === key)) setSelected(selected.filter((entry) => evidenceKey(entry) !== key));
              else if (ref) {
                const reasons = evidenceProblems(ref, packet);
                // A private row opens the share consent for that row only; it is attached once confirmed.
                if (reasons.length && reasons.every((reason) => reason.startsWith('Private'))) { setError(''); setConsent({ share: [ref], attach: [] }); }
                else if (reasons.length) { setError(reasons.join('. ')); setInspecting(key); }
                else { setError(''); setSelected([...selected, evidenceSelection(ref)]); }
              }
            }} />
          <ShareConsentDialog open={Boolean(consent)} share={consent?.share} attach={consent?.attach} candidateNumber={candidate.number} baselineNumber={previousVersion}
            onShare={(input) => share.mutateAsync(input)} onShared={consent?.proceed ? sharedThenContinue : attachShared} onClose={() => setConsent(null)}
            onSkip={consent?.proceed ? continueWithout : undefined} />
          {!options.data.evidence.length && <Button variant="outlined" size="small" href={testsUrl} sx={{ alignSelf: 'flex-start' }}>Run tests on v{candidate.number}</Button>}
          {inspecting && <TypedEvidenceList showPrivateNote={false} packet={packet} evidence={options.data.evidence.filter((ref) => evidenceKey(ref) === inspecting)} selected={[]} />}
          {selected.filter((ref) => !options.data.evidence.some((item) => evidenceKey(item) === evidenceKey(ref))).map((ref) => <Alert key={evidenceKey(ref)} severity="warning">Selected evidence {ref.sourceId} is unavailable. Refresh and review again.</Alert>)}
        </Stack>
      </Section></Box>}
      <TechnicalDetails title="Attach your CI run (optional)"><Stack spacing={1.5}>
        <Typography variant="s3">Operator-recorded, not a verified Hub verdict. Leave all three fields empty if no external pipeline applies.</Typography>
        <TextField size="small" label="Pipeline result URL" value={pipelineUrl} onChange={(event) => setPipelineUrl(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
        <TextField size="small" label="Pipeline configuration identity" value={pipelineConfig} onChange={(event) => setPipelineConfig(event.target.value)} disabled={busy} inputProps={{ maxLength: 255 }} />
        <TextField size="small" label="Recorded pipeline decision" value={pipelineDecision} onChange={(event) => setPipelineDecision(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
      </Stack></TechnicalDetails>
      <ComposerActions><DecisionFooterRow
        left={<Typography variant="s3" role="status" sx={{ color: evidenceState.warning ? 'warning.main' : 'text.secondary', minWidth: 0 }}>{evidenceState.text}</Typography>}
        reason={<ActionReason id="prepare-review-reason" reason={prepareReason} sx={{ textAlign: { sm: 'right' } }} />}
        secondary={<Button variant="outlined" disabled={busy} onClick={() => navigate(paths.dashboard.skills.reviews)}>Cancel</Button>}
        primary={<Button variant="contained" color="primary" {...reasonProps('prepare-review-reason', prepareReason)} disabled={!prepareReady} onClick={continueRequest}>{COMPOSER_CONTINUE}</Button>} /></ComposerActions>
    </>}
  </Stack>;
}
ReleaseRequestComposer.propTypes = { scopeKey: PropTypes.string.isRequired, renewal: PropTypes.object, prefill: PropTypes.object, state: PropTypes.string };
