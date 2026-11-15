import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink, useLocation, useNavigate } from 'react-router-dom';
import { visuallyHidden } from '@mui/utils';
import { v4 as uuid } from 'uuid';
import {
  Alert, Box, Button, Checkbox, Collapse, Drawer, FormControl, FormControlLabel, FormLabel, IconButton, LinearProgress, MenuItem, Radio, RadioGroup, Stack, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import {
  useCreateNativeRun, useCreateNativeTarget, useNativeRun, useNativeRuns, useNativeTargets, usePreviewNativeRun,
  usePrepareReleasePacket, useQualifyNativeTarget, useRuntimeConfiguration,
} from 'src/api/skills/context-testing';
import { useAuthContext } from 'src/auth/hooks';
import { useContextDetail } from 'src/api/skills/context-authoring';
import { useRecoveryReviewers } from 'src/api/skills/j6-recovery';
import { paths } from 'src/routes/paths';
import { useSkillsSpendPolicy } from 'src/api/skills/r3-spend';
import { getSkillErrorCode } from 'src/api/skills/operation-errors';
import OperationFeedback from '../components/OperationFeedback';
import { noReviewsYet } from '../utils/conclusions';
import { answerOutcome, applicationConclusion, applicationOutcome, coverageLine, inProductionHint, isInProduction } from '../tests/resultConclusion';
import ResultConclusionCard from '../tests/ResultConclusionCard';
import AdminAllowanceRequest, { allowanceRequestText } from '../tests/AdminAllowanceRequest';
import { BEHAVIOR_ACTIONS } from '../tests/applicationBehavior';
import ActionReason, { reasonProps } from '../components/ActionReason';
import When from '../components/When';
import { useSkillsAccess } from '../utils/access';
import { useAppendEvidenceAnnotation, useAllEvidenceAnnotations, useEvidenceReference } from 'src/api/skills/evidence-annotations';
import ClosableDialog from 'src/components/custom-dialog/closable-dialog';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import Iconify from 'src/components/iconify';
import IdentityChip from '../components/IdentityChip';
import TechnicalDetails from '../components/TechnicalDetails';
import CapabilityNotice from '../components/CapabilityNotice';
import { reviewComposerUrl } from '../utils/review-links';
import '../tests/testIcons';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';

// Intent: a responder proves a repair on the real application, not on text.
// Hierarchy: target identity, then the reviewed plan and its paid ceiling, then the
// matched result with a ship/hold decision, then per-cell observation and disagreement.
// Palette/depth/surfaces: neutral flat tables, status roles only for outcomes.
// Typography/spacing: m3 headings, s1 values, s3 secondary; 12px groups.
const ARM = { candidate: 'Version tested', reference: 'Was failing on', production: 'Production' };
// A cell names its version plainly: the version under test is just "v2"; the others say what they are.
const armText = (variant, version) => (variant === 'candidate' ? `v${version}` : `${ARM[variant]} v${version}`);
const lowerFirst = (text) => text.charAt(0).toLowerCase() + text.slice(1);
const FAMILIES = [['selection', 'Skill selected'], ['delivery', 'File delivered'], ['tools', 'Business tool'], ['answer', 'Answer']];
const disagreementLabel = (count) => `${count} disagreement${count === 1 ? '' : 's'}`;
const ACTIVE_STATES = ['queued', 'provisioning', 'preparing', 'running'];
const VERDICTS = [['agree', 'Agree with the automatic outcome'], ['disagree', 'Disagree with the automatic outcome'], ['cannot_assess', 'Cannot assess']];
// Business actions read as the trace dialog names them ("Issue a refund (declined)"), never as IDs.
const ACTION_NAMES = Object.fromEntries(BEHAVIOR_ACTIONS);
const humanize = (value) => { const text = String(value ?? '').replaceAll('_', ' ').trim(); return text ? text.charAt(0).toUpperCase() + text.slice(1) : ''; };
const actionName = (id) => ACTION_NAMES[id] || humanize(id);
const actionSummary = (action) => `${actionName(action.action)}${action.status ? ` (${String(action.status).replaceAll('_', ' ')})` : ''}`;

// Move only the page's own scroller (the closest <main>) so `element` sits near its top. scrollIntoView
// and a bare focus() scroll every scrollable ancestor, the dashboard frame and the document included,
// which pushed the whole shell sideways. Focus always goes with { preventScroll: true }.
function revealInMain(element) {
  const main = element?.closest?.('main');
  if (!main || typeof main.scrollTo !== 'function') return;
  const top = main.scrollTop + element.getBoundingClientRect().top - main.getBoundingClientRect().top - 16;
  main.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
}
const focusQuietly = (element) => element?.focus?.({ preventScroll: true });
// The SH1 shell shift had one source here: a screen-reader-only header label with sx width 1 (MUI
// reads that as 100%) and no positioned ancestor, so a viewport-wide span sat past the right edge
// and gave the document its own sideways and vertical scroll. It now uses MUI's 1px visuallyHidden
// inside a positioned cell.

// A pass is the expected outcome, so it reads as quiet text; only failures and incomplete checks get a
// coloured badge, which keeps the two failures in a grid of twenty passes easy to find.
function Verdict({ value, status }) {
  if (value === 'pass') return <Typography component="span" variant="s1" color="text.secondary">Passed</Typography>;
  return value ? <StatusBadge status={value === 'fail' ? 'failed' : 'unknown'} /> : <StatusBadge status={status === 'failed' ? 'failed' : 'pending'} />;
}
Verdict.propTypes = { value: PropTypes.string, status: PropTypes.string };

function TargetSection({ contextId, version, canWrite, targets }) {
  const create = useCreateNativeTarget(contextId);
  const qualify = useQualifyNativeTarget(contextId);
  const [review, setReview] = useState(null);
  const configuration = useRuntimeConfiguration(contextId, Boolean(review));
  const [error, setError] = useState('');
  const target = targets.data.targets[0];
  const pin = async () => {
    setError('');
    try { await qualify.mutateAsync({ bindingId: review.bindingId, revision: review.revision, configuration: configuration.data }); setReview(null); }
    catch { setError('Could not pin this configuration. Reopen the review to compare the latest target revision.'); }
  };
  return <Section title={<Typography component="span" variant="m3">Application target</Typography>}>
    {!target ? <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }} sx={{ minWidth: 0 }}>
      <Typography variant="s1">No application is registered for this context.</Typography>
      <Button size="small" variant="outlined" disabled={!canWrite || !version || create.isPending} onClick={() => create.mutate({ version: version.number })}>Add application target</Button>
    </Stack> : <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }} sx={{ minWidth: 0 }}>
      <Typography variant="s1">{target.name}</Typography>
      <StatusBadge status={target.qualified ? 'info' : 'unknown'} role={target.qualified ? 'info' : 'warning'} label={target.qualified ? 'App configuration pinned' : 'App configuration not pinned'} />
      <Button size="small" variant="outlined" disabled={!canWrite} onClick={() => setReview(target)}>Show app configuration</Button>
      <Button size="small" variant="outlined" disabled={!canWrite} onClick={() => setReview(target)}>Change</Button>
    </Stack>}
    <TechnicalDetails title="App version info"><Stack spacing={1}><Typography variant="s3" color="text.secondary">Runtime: {targets.data.runtime.runtime} · evidence class: {targets.data.runtime.evidenceClass || 'unstated'}</Typography>{target?.qualified && <IdentityChip value={target.configurationDigest} label="Configuration" />}</Stack></TechnicalDetails>
    {create.isError && <Alert severity="error">Could not add the target. Check the runtime and your access, then retry.</Alert>}
    {review && <ClosableDialog open onClose={() => setReview(null)} maxWidth="sm" fullWidth>
      <Stack spacing={1.5} sx={{ p: 3 }}>
        <Typography variant="m1">Pin the application configuration</Typography>
        <Typography variant="s1">The runtime measured these identities from its live image and mounts. Each verification launch measures them again and refuses a mismatch.</Typography>
        {configuration.isLoading ? <ContentSkeleton label="Reading the runtime's measured configuration" /> : configuration.isError ? <ErrorState title="The runtime did not describe itself" onRetry={configuration.refetch} /> : <Stack spacing={0.5} component="dl" sx={{ m: 0 }}>
          {[['Source commit', configuration.data.code_commit ?? configuration.data.codeCommit], ['Entrypoint', configuration.data.entrypoint], ['Runtime', configuration.data.runtime_version ?? configuration.data.runtimeVersion],
            ['Packages', `${configuration.data.packages.length} pinned`], ['Model', configuration.data.model.model_id ?? configuration.data.model.modelId], ['Side effects', configuration.data.side_effect_policy ?? configuration.data.sideEffectPolicy]]
            .map(([label, value]) => <Stack key={label} direction="row" spacing={1}><Typography component="dt" variant="s3" color="text.secondary" sx={{ width: 120 }}>{label}</Typography><Typography component="dd" variant="s1" sx={{ m: 0, overflowWrap: 'anywhere' }}>{value}</Typography></Stack>)}
        </Stack>}
        {error && <Alert severity="error">{error}</Alert>}
        <Stack direction="row" justifyContent="flex-end" spacing={1}>
          <Button onClick={() => setReview(null)}>Cancel</Button>
          <Button color="primary" variant="contained" disabled={!configuration.data || qualify.isPending} onClick={pin}>Pin this configuration</Button>
        </Stack>
      </Stack>
    </ClosableDialog>}
  </Section>;
}
TargetSection.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object, canWrite: PropTypes.bool.isRequired, targets: PropTypes.object.isRequired };

function CellDrawer({ run, cell, caseTitle, onClose, canWrite }) {
  const reference = useEvidenceReference('simulation_native', run.id);
  const members = useRecoveryReviewers();
  const { user } = useAuthContext();
  const authorName = (id) => (id === user?.id ? user.displayName || user.name : members.data?.find((member) => member.id === id)?.name) || 'A workspace member';
  const annotations = useAllEvidenceAnnotations('simulation_native', run.id);
  const append = useAppendEvidenceAnnotation('simulation_native', run.id);
  const [verdict, setVerdict] = useState('');
  const [comment, setComment] = useState('');
  const [error, setError] = useState('');
  const mine = (annotations.data?.results || []).filter((row) => row.attemptId === cell.attemptId);
  const submit = async () => {
    setError('');
    try {
      await append.mutateAsync({ source: 'simulation_native', sourceId: run.id, annotationRevision: reference.data.annotationRevision, checkId: null, attemptId: cell.attemptId, verdict, citedText: '', comment });
      setVerdict(''); setComment('');
    } catch { setError('Could not record this assessment. Reload the evidence and retry; the automatic outcome was not changed.'); }
  };
  const seen = cell.observed;
  const version = run.arms.find((arm) => arm.variant === cell.variant)?.version;
  const repeats = (run.cells || []).filter((other) => other.caseId === cell.caseId && other.variant === cell.variant).length || 1;
  // A human assessment is recorded against this one run of one case (attemptId), never the whole test run.
  const scope = `Applies to this run only: ${lowerFirst(ARM[cell.variant])}${version ? ` v${version}` : ''} · ${caseTitle || 'this case'} · run ${cell.repeatIndex + 1}`;
  // Focus goes back to the opener without scrolling (see revealInMain), so MUI's own restore is off.
  return <Drawer anchor="right" open onClose={onClose} disableRestoreFocus PaperProps={{ role: 'dialog', 'aria-modal': true, 'aria-labelledby': 'attempt-heading', sx: { width: 460, maxWidth: '100vw', overflow: 'hidden' } }}>
    <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ p: 2, borderBottom: 1, borderColor: 'divider', flexShrink: 0 }}><Typography id="attempt-heading" component="h2" variant="m1">{caseTitle}</Typography><IconButton aria-label="Close" onClick={onClose}><Iconify icon="solar:close-linear" /></IconButton></Stack>
    <Stack spacing={1.5} sx={{ p: 2, flex: 1, minHeight: 0, overflowY: 'auto' }}>
      <Typography variant="s3" color="text.secondary">{ARM[cell.variant]}{version ? ` v${version}` : ''} · Run {cell.repeatIndex + 1} of {Math.max(repeats, cell.repeatIndex + 1)}</Typography>
      <Section title={<Typography component="span" variant="m3">Observed by the application</Typography>}>
        {seen ? <Stack spacing={0.5}>
          <Typography variant="s1">Skills opened: {seen.selected.length ? seen.selected.join(', ') : 'none'}</Typography>
          <Typography variant="s1">Business actions: {seen.actions.length ? seen.actions.map(actionSummary).join(', ') : 'none attempted'}</Typography>
          <Typography variant="s1" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>Answer: {seen.answer || '—'}</Typography>
          <TechnicalDetails title="Technical details"><IdentityChip value={seen.context_digest} label="Context" /><IdentityChip value={seen.session_id} label="Session" kind="id" /></TechnicalDetails>
        </Stack> : <Typography variant="s1">This run did not return an observation.</Typography>}
      </Section>
      <Section title={<Typography component="span" variant="m3">Automatic outcomes</Typography>}>
        <Stack spacing={0.5}>{FAMILIES.map(([key, label]) => <Stack key={key} direction="row" justifyContent="space-between"><Typography variant="s1">{label}</Typography><Verdict value={cell[key]} /></Stack>)}</Stack>
      </Section>
      <Section title={<Typography component="span" variant="m3">Human review</Typography>} description="Your assessment is added next to the automatic result; it doesn't change it.">
        {mine.some((row) => row.verdict === 'disagree') && <Typography variant="s3" color="text.secondary">{disagreementLabel(mine.filter((row) => row.verdict === 'disagree').length)} below</Typography>}
        {annotations.isLoading ? <ContentSkeleton variant="rows" label="Loading human reviews" /> : annotations.isError ? <ErrorState title="Couldn't load human reviews" onRetry={annotations.refetch} /> : !mine.length ? <Typography variant="s3" color="text.secondary">No assessments for this run.</Typography> : mine.map((row) => <Stack key={row.id} spacing={0.5} sx={{ py: 1.5, borderBottom: 1, borderColor: 'divider' }}>
          <StatusBadge status={row.verdict === 'disagree' ? 'warning' : 'info'} role={row.verdict === 'disagree' ? 'warning' : 'info'} label={{ agree: 'Agrees', disagree: 'Disagrees', cannot_assess: "Can’t assess" }[row.verdict]} sx={{ alignSelf: 'flex-start' }} />
          <Typography variant="s3" color="text.secondary"><Box component="span">{authorName(row.actorId)}</Box> · <When value={row.createdAt} /></Typography>
          <Typography variant="s1" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{row.comment || 'No comment'}</Typography>
          <Typography variant="s3" color="text.secondary">{scope}</Typography>
          <TechnicalDetails title="Author details"><IdentityChip value={row.actorId} label="Author" kind="id" /></TechnicalDetails>
        </Stack>)}
      </Section>
    </Stack>
        <Stack spacing={1} sx={{ p: 2, borderTop: 1, borderColor: 'divider', bgcolor: 'background.paper', flexShrink: 0, maxHeight: '60%', overflowY: 'auto' }}>
          <FormControl disabled={!canWrite}>
            <FormLabel id="assessment-label" sx={{ color: 'text.primary' }}>Your assessment</FormLabel>
            <RadioGroup aria-labelledby="assessment-label" value={verdict} onChange={(event) => setVerdict(event.target.value)}>
              {VERDICTS.map(([value, label]) => <FormControlLabel key={value} value={value} control={<Radio size="small" />} label={<Typography variant="s1">{label}</Typography>} />)}
            </RadioGroup>
          </FormControl>
          <TextField size="small" label="Why" value={comment} onChange={(event) => setComment(event.target.value)} multiline minRows={2} disabled={!canWrite} />
          {error && <Alert severity="error">{error}</Alert>}
          {(!canWrite || !verdict || !reference.data || append.isPending) && <Typography id="review-disabled-reason" variant="s3" color="text.secondary">{!canWrite ? 'Only a workspace author can record an assessment.' : !reference.data ? 'Waiting for the current evidence revision.' : append.isPending ? 'Recording your assessment…' : 'Choose an assessment to record it.'}</Typography>}
          {reference.isError && <ErrorState title="Couldn't load the evidence revision" onRetry={reference.refetch} />}
          <Button color="primary" variant="contained" sx={{ alignSelf: 'flex-end' }} aria-describedby={(!canWrite || !verdict || !reference.data || append.isPending) ? 'review-disabled-reason' : undefined} disabled={!canWrite || !verdict || !reference.data || append.isPending} onClick={submit}>Record assessment</Button>
        </Stack>
  </Drawer>;
}
CellDrawer.propTypes = { run: PropTypes.object.isRequired, cell: PropTypes.object.isRequired, caseTitle: PropTypes.string, onClose: PropTypes.func.isRequired, canWrite: PropTypes.bool.isRequired };

// The CI attachment is optional: the release review can start from this run alone. Attaching a CI
// run shares this run with the workspace and prepares the packet the reviewer will see; nothing is
// released here and sharing is never implied by opening the release review. It is shown open below
// the per-case table (and can be folded away) so teams that do attach CI need no extra step; its
// title says it is optional and "Request release review" stays the one release action.
function CiAttachment({ contextId, run, target, canWrite, earlier }) {
  const { user } = useAuthContext();
  const prepare = usePrepareReleasePacket(contextId);
  const [pipeline, setPipeline] = useState({ url: '', configurationIdentity: '', decision: '' });
  const set = (key) => (event) => setPipeline({ ...pipeline, [key]: event.target.value });
  const valid = pipeline.url.trim() && pipeline.configurationIdentity.trim() && pipeline.decision.trim();
  const reason = !canWrite ? 'Only a workspace author can share this run.' : !target ? 'Add an application target to share this run.' : !valid ? 'Fill in the three CI fields to attach your CI run.' : prepare.isPending ? 'Preparing…' : null;
  return <TechnicalDetails title="Attach your CI run (optional)" defaultOpen keepMounted>
    <Stack spacing={1.5}>
      <Typography variant="s3" color="text.secondary">Adds your CI result to the release evidence and shares this run with your workspace. Nothing is released here.</Typography>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} sx={{ '& > *': { flex: 1 } }}>
        <TextField size="small" label="CI run link" value={pipeline.url} onChange={set('url')} />
        <TextField size="small" label="CI pipeline name" value={pipeline.configurationIdentity} onChange={set('configurationIdentity')} />
        <TextField size="small" label="CI result (pass or hold)" value={pipeline.decision} onChange={set('decision')} />
      </Stack>
      {prepare.isError && <OperationFeedback error={prepare.error} action="share this run" onRetry={() => prepare.mutate({ run, target, label: 'production', pipeline, userId: user?.id })} />}
      {prepare.data && <>
        <Alert severity="success">Packet ready — this run is shared with your workspace.</Alert>
        <TechnicalDetails title="Packet details"><IdentityChip value={prepare.data.fingerprint} label="Packet fingerprint" /><IdentityChip value={prepare.data.targetDigest} label="Target configuration" /></TechnicalDetails>
      </>}
      {earlier && <Typography variant="s3">This applies to the earlier run, not your current selection.</Typography>}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }}>
        <Button color="primary" variant="outlined" size="small" disabled={Boolean(reason)} {...reasonProps('ci-attach-reason', reason)} onClick={() => prepare.mutate({ run, target, label: 'production', pipeline, userId: user?.id })}>Share run and attach CI result</Button>
        <ActionReason id="ci-attach-reason" reason={reason} />
      </Stack>
    </Stack>
  </TechnicalDetails>;
}
CiAttachment.propTypes = { earlier: PropTypes.bool, contextId: PropTypes.string.isRequired, run: PropTypes.object.isRequired, target: PropTypes.object, canWrite: PropTypes.bool.isRequired };

// Row and cell rules are hairlines from the theme at every width. A responsive `borderBottom`
// shorthand inside a breakpoint resets the colour to currentColor (near-black in light mode), so
// only the width is responsive and the colour is the divider token.
const cellsTableSx = {
  '& thead': { display: { xs: 'none', sm: 'table-header-group' } },
  '& tbody': { display: { xs: 'block', sm: 'table-row-group' } },
  '& tbody td:first-of-type, & tbody td:last-of-type': { gridColumn: '1 / -1' },
  '& tbody td.mobile-hidden': { display: { xs: 'none', sm: 'table-cell' } },
  '& tbody td': { display: { xs: 'block', sm: 'table-cell' }, whiteSpace: 'normal', overflowWrap: 'anywhere', borderBottomStyle: 'solid', borderBottomColor: 'divider', borderBottomWidth: { xs: 0, sm: 1 }, py: { xs: 0.5, sm: 1 } },
  '& tbody tr': { display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: '1fr 1fr', height: { xs: 'auto', sm: ROW_HEIGHT.default }, py: { xs: 1, sm: 0 }, borderBottomStyle: 'solid', borderBottomColor: 'divider', borderBottomWidth: { xs: 1, sm: 0 } },
};

// The next step after a passing result for the version production already serves: its release
// history, or a newer draft or version to test. Never a release request the composer would refuse.
export function InProductionAction({ contextId, newer }) {
  const href = newer ? paths.dashboard.skills.contextEdit(contextId) : `${paths.dashboard.skills.contextDetail(contextId)}?tab=versions`;
  return <Button variant="outlined" component={RouterLink} to={href}>{newer ? 'Test a newer draft' : 'View release history'}</Button>;
}
InProductionAction.propTypes = { contextId: PropTypes.string.isRequired, newer: PropTypes.bool };

function RunDetail({ contextId, evidenceId, canWrite, target, selection, cases, onReady, onRunCurrent, onEarlier, formPrimary, productionVersion, versions = [], planCeiling }) {
  const query = useNativeRun(contextId, evidenceId);
  const context = useContextDetail(contextId);
  const navigate = useNavigate();
  // Human reviews attach to finished evidence. Read them only once the run detail says the run
  // is finished, so a request made while it was still running can never show an error over a
  // valid result; a finished run with no review record yet reads as "no human reviews".
  const finished = Boolean(query.data) && !ACTIVE_STATES.includes(query.data.jobState || query.data.status);
  const annotationQuery = useAllEvidenceAnnotations('simulation_native', finished ? evidenceId : null);
  const annotations = noReviewsYet(annotationQuery);
  const origin = useRef(null);
  const [open, setOpen] = useState(null);
  // Focus returns to the opener once the drawer and its focus trap are gone, without scrolling.
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (open || !restoreFocus.current) return;
    restoreFocus.current = false;
    focusQuietly(origin.current);
  }, [open]);
  const selectedCandidate = query.data?.arms.find((arm) => arm.variant === 'candidate');
  const stale = Boolean(selection && query.data && (selectedCandidate?.version !== selection.candidateVersion || JSON.stringify((query.data.cases || []).map((item) => item.id).sort()) !== JSON.stringify([...selection.caseIds].sort())));
  const releaseReady = Boolean(query.data) && finished && query.data.decision?.state === 'release_ready';
  // Readiness is read against current production: a pass for the version production already serves
  // confirms that release and offers no new release request.
  const inProduction = isInProduction(selectedCandidate?.version, productionVersion);
  // What an earlier result leads with: its release review when it passed for a releasable version, a rerun
  // on the current selection when it did not, and nothing at all for the version production already serves.
  const earlierKind = !stale ? false : inProduction ? 'production' : releaseReady ? 'review' : 'rerun';
  useEffect(() => { onEarlier?.(earlierKind); return () => onEarlier?.(false); }, [earlierKind, onEarlier]);
  useEffect(() => { const offered = releaseReady && !inProduction; onReady?.(offered); return () => onReady?.(false); }, [releaseReady, inProduction, onReady]);
  if (query.isLoading) return <ContentSkeleton label="Loading application test result" />;
  if (query.isError) return <ErrorState title="Couldn't load this application test" onRetry={query.refetch} />;
  const run = query.data;
  if (!run) return null;
  const titles = Object.fromEntries((run.cases || []).map((item) => [item.id, item.title]));
  const jobState = run.jobState || run.status;
  const active = ACTIVE_STATES.includes(jobState);
  const received = run.received || 0;
  const status = active && received > 0 ? 'running' : jobState === 'provisioning' ? 'preparing' : jobState;
  const candidate = run.arms.find((arm) => arm.variant === 'candidate');
  const runCases = (run.cases || []).map((item) => item.id).sort();
  const earlier = stale;
  const disagreements = annotations.data?.results.filter((row) => row.verdict === 'disagree') || [];
  const inspect = (cell, event) => { origin.current = event.currentTarget.closest?.('tr')?.querySelector('button') || event.currentTarget; setOpen(cell); };
  const referenceArm = run.arms.find((arm) => arm.variant === 'reference');
  const productionArm = run.arms.find((arm) => arm.variant === 'production');
  const comparedWith = referenceArm ? ` vs v${referenceArm.version} (was failing)` : productionArm ? ` vs production v${productionArm.version}` : '';
  // What the current selection changes against this earlier result, and what rerunning it would cost.
  const caseTitle = (id) => titles[id] || cases.find((item) => item.id === id)?.title || 'a case';
  const added = earlier ? selection.caseIds.filter((id) => !runCases.includes(id)) : [];
  const dropped = earlier ? runCases.filter((id) => !selection.caseIds.includes(id)) : [];
  const caseCount = (count, word) => `${count} ${word} case${count === 1 ? '' : 's'}`;
  const selectionChanges = [
    earlier && Number(selection.candidateVersion) !== Number(candidate?.version) && `tests v${selection.candidateVersion} instead of v${candidate?.version}`,
    added.length > 0 && `adds ${added.map(caseTitle).join(', ')} (${caseCount(added.length, 'more')})`,
    dropped.length > 0 && `drops ${dropped.map(caseTitle).join(', ')} (${caseCount(dropped.length, 'fewer')})`,
  ].filter(Boolean);
  const differs = selectionChanges.length ? `Your selection ${selectionChanges.join(' and ')}${planCeiling != null && !inProduction ? ` · up to $${planCeiling}` : ''}.` : 'Your current selection is different.';
  const firstDisagreement = run.cells?.find((cell) => cell.attemptId && disagreements.some((row) => row.attemptId === cell.attemptId));
  // A verdict for the disagreements: one on a baseline (the version it was failing on, or production) is about
  // the comparison, not the version under test. Only when every disagreement maps to a baseline cell does it
  // say so; anything on the tested version, or not matched to a cell, asks for a look before the release request.
  const disagreedCells = disagreements.map((row) => run.cells?.find((cell) => cell.attemptId && cell.attemptId === row.attemptId));
  const onBaselineOnly = disagreements.length > 0 && disagreedCells.every((cell) => cell && ['reference', 'production'].includes(cell.variant));
  const baselineVersions = [...new Set(disagreedCells.filter(Boolean).map((cell) => run.arms.find((arm) => arm.variant === cell.variant)?.version).filter((value) => value != null))];
  const many = disagreements.length > 1;
  const disagreementVerdict = !disagreements.length ? null : onBaselineOnly
    ? `On the ${baselineVersions.length ? `${baselineVersions.map((value) => `v${value}`).join(' and ')} ` : ''}baseline${baselineVersions.length > 1 ? 's' : ''}; ${many ? "they don't" : "it doesn't"} block v${candidate?.version}.`
    : `On v${candidate?.version}; check ${many ? 'them' : 'it'} before requesting release.`;
  const ready = releaseReady && !active;
  const confirmed = ready && inProduction;
  const offerReview = ready && !inProduction;
  // One contained action per view state. A current result's release review leads unless the reviewer is
  // working in the setup (formPrimary). An earlier result (a different version or a different set of cases)
  // leads with the rerun on the current selection, even when it passed: its release request is outlined and
  // says how much of the context it left untested, so a 1-of-4-case pass never reads as the way forward.
  const rerunLeads = (earlierKind === 'rerun' || earlierKind === 'review') && !formPrimary;
  const continueLeads = offerReview && !earlier && !formPrimary;
  const totalCases = Math.max(cases.length, runCases.length);
  const untested = totalCases - runCases.length;
  const partialHint = earlier && untested > 0 ? `${untested} of ${totalCases} case${totalCases === 1 ? '' : 's'} not tested` : null;
  const newer = Boolean(context.data?.ownDraft) || versions.some((item) => Number(item.number) > Number(candidate?.version));
  const continueReason = !canWrite ? 'Only a workspace author can request a release review.' : null;
  const composerUrl = reviewComposerUrl({ kind: 'agent_context', subjectId: contextId, name: context.data?.name, candidateId: candidate?.versionId, number: candidate?.version, digest: candidate?.digest, label: 'production', evidence: [{ source: 'simulation_native', sourceId: run.id }], target: target?.bindingId });
  const reviews = annotations.isError ? <ErrorState title="Couldn't load human reviews" onRetry={annotations.refetch} /> : annotations.isLoading ? <Typography variant="s3">Loading human reviews…</Typography> : firstDisagreement ? <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 0, sm: 1 }} alignItems={{ xs: 'flex-start', sm: 'center' }}><Button variant="text" size="small" onClick={(event) => inspect(firstDisagreement, event)}>{disagreementLabel(disagreements.length)} to inspect</Button><Typography variant="s3" color="text.secondary">{disagreementVerdict}</Typography></Stack> : <Typography variant="s3" color="text.secondary">{disagreements.length ? `${disagreementLabel(disagreements.length)} recorded; attempt details unavailable` : annotations.data?.results?.length ? 'No reviewer disagreed with these grades' : 'No human reviews yet'}</Typography>;
  const decided = (run.decision || run.configurationDigest) && <Stack spacing={1}>
    {run.decision?.reasons.map((reason) => <Typography key={reason} variant="s1" component="div">{reason}</Typography>)}
    {run.configurationDigest && <IdentityChip value={run.configurationDigest} label="Configuration" />}
  </Stack>;
  return <Stack spacing={1.5}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }} sx={{ minWidth: 0 }}>
      <Typography component="h2" variant="m3" data-execution-fingerprint={run.executionFingerprint}>{`Application test · v${candidate?.version}${comparedWith}`}</Typography>
      <StatusBadge status={status === 'preparing' ? 'running' : status} label={status === 'preparing' ? 'Preparing' : undefined} />
      <Typography variant="s3" color="text.secondary" role="status">{received} of {run.planned} runs finished{active && received === run.planned ? ' · Finalizing result' : ''}</Typography>
    </Stack>
    <Typography variant="s3" color="text.secondary">{coverageLine(runCases.length, Math.max(cases.length, runCases.length))} · <When value={run.createdAt} /></Typography>
    {active && <LinearProgress aria-label="Application run progress" variant={run.planned ? "determinate" : "indeterminate"} value={run.planned ? Math.min(100, received / run.planned * 100) : 0} />}
    {earlier && <Stack spacing={1} sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1 }}><StatusBadge status="unknown" label="Earlier result" sx={{ alignSelf: 'flex-start' }} /><Typography variant="s1">This result is for v{candidate?.version} with {runCases.length} case{runCases.length === 1 ? '' : 's'}. {differs}</Typography>{!inProduction && <Button color="primary" variant={rerunLeads ? 'contained' : 'outlined'} onClick={onRunCurrent} sx={{ alignSelf: 'flex-start' }}>Run on current selection</Button>}</Stack>}
    {run.failure && <Alert severity="error">The run stopped: {run.failure.replaceAll('_', ' ')}. No outcome is inferred.</Alert>}
    {!active && (run.result || run.decision) && <ResultConclusionCard sentence={applicationConclusion(run, Math.max(cases.length, runCases.length))}
      badge={offerReview ? { status: 'passed', label: 'Ready for release review' } : confirmed ? { status: 'released', role: 'neutral', label: 'Already in production' } : undefined}
      actions={offerReview ? <Button color="primary" variant={continueLeads ? 'contained' : 'outlined'} href={continueReason ? undefined : composerUrl} disabled={Boolean(continueReason)} {...reasonProps('continue-review-reason', continueReason)}>Request release review</Button>
        : confirmed ? <InProductionAction contextId={contextId} newer={newer} /> : undefined}
      hint={offerReview ? <ActionReason id="continue-review-reason" reason={continueReason || partialHint || "You'll choose a reviewer and confirm sharing this run there."} />
        : confirmed ? <Typography variant="s3" color="text.secondary">{inProductionHint(candidate?.version)}</Typography> : undefined}
      decided={decided}>
      {reviews}
    </ResultConclusionCard>}
    {!active && canWrite && run.result?.reproduction === 'reproduced' && referenceArm && <Box>
      <Button variant="outlined" size="small" onClick={() => navigate(`${paths.dashboard.skills.contextEdit(contextId)}?from=${referenceArm.version}&preserve=1`)}>{`Repair from the failing v${referenceArm.version}`}</Button>
    </Box>}
    {run.runtime && <TechnicalDetails title="Technical details"><Typography variant="s3" color="text.secondary">Runtime proved read-only source {String(run.runtime.read_only_source)}, read-only context {String(run.runtime.read_only_context)}, closed paid egress {String(run.runtime.controlled_paid_egress)}.</Typography><IdentityChip value={run.executionFingerprint} label="Execution fingerprint" /></TechnicalDetails>}
    {run.cells && <TableContainer>
      <Table size="small" aria-label="Verification cells" sx={cellsTableSx}><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
        <TableCell>Test case</TableCell><TableCell>Version</TableCell><TableCell>Run</TableCell>{FAMILIES.map(([key, label]) => <TableCell key={key}>{label}</TableCell>)}<TableCell>Human review</TableCell><TableCell />
      </TableRow></TableHead><TableBody>{run.cells.map((cell) => <TableRow key={`${cell.caseId}/${cell.variant}/${cell.repeatIndex}`} hover sx={{ height: ROW_HEIGHT.default, cursor: cell.attemptId ? 'pointer' : 'default' }} onClick={(event) => cell.attemptId && inspect(cell, event)}>
        <TableCell>{titles[cell.caseId] || cell.caseId}<Typography variant="s3" color="text.secondary" sx={{ display: { xs: 'block', sm: 'none' } }}>{armText(cell.variant, run.arms.find((arm) => arm.variant === cell.variant)?.version)} · run {cell.repeatIndex + 1}</Typography></TableCell><TableCell className="mobile-hidden">{armText(cell.variant, run.arms.find((arm) => arm.variant === cell.variant)?.version)}</TableCell><TableCell className="mobile-hidden">{cell.repeatIndex + 1}</TableCell>
        {FAMILIES.map(([key, label]) => <TableCell key={key} className={cell.status === 'completed' && cell[key] === 'pass' && !disagreements.some((row) => row.attemptId === cell.attemptId) ? 'mobile-hidden' : undefined}><Typography variant="s3" color="text.secondary" sx={{ display: { xs: 'block', sm: 'none' } }}>{label}</Typography><Verdict value={cell.status === 'completed' || !active ? cell[key] : undefined} status={cell.status} /></TableCell>)}
        <TableCell className={!annotations.isError && !annotations.isLoading && !disagreements.some((row) => row.attemptId === cell.attemptId) ? 'mobile-hidden' : undefined}>{annotations.isError ? 'Unavailable' : annotations.isLoading ? 'Loading…' : disagreements.filter((row) => row.attemptId === cell.attemptId).length ? <StatusBadge status="warning" label={disagreementLabel(disagreements.filter((row) => row.attemptId === cell.attemptId).length)} /> : '—'}</TableCell>
        <TableCell>{cell.status === 'completed' && FAMILIES.every(([key]) => cell[key] === 'pass') && <Typography variant="s3" color="text.secondary" sx={{ display: { xs: 'block', sm: 'none' }, mb: 0.5 }}>All 4 checks passed</Typography>}<Button size="small" variant="outlined" disabled={!cell.attemptId} aria-label={`Inspect ${titles[cell.caseId] || cell.caseId} ${ARM[cell.variant]} repeat ${cell.repeatIndex + 1}`} onClick={(event) => { event.stopPropagation(); inspect(cell, event); }}>Inspect</Button></TableCell>
      </TableRow>)}</TableBody></Table>
    </TableContainer>}
    {/* Attaching CI prepares a release packet, so it is offered only while a release review is: never for the version production already serves. */}
    {offerReview && <CiAttachment contextId={contextId} run={run} target={target} canWrite={canWrite} earlier={earlier} />}
    {open && <CellDrawer run={run} cell={open} caseTitle={titles[open.caseId]} canWrite={canWrite} onClose={() => { restoreFocus.current = true; setOpen(null); }} />}
  </Stack>;
}
RunDetail.propTypes = { planCeiling: PropTypes.oneOfType([PropTypes.number, PropTypes.string]), productionVersion: PropTypes.number, versions: PropTypes.array, formPrimary: PropTypes.bool, onEarlier: PropTypes.func, onReady: PropTypes.func, onRunCurrent: PropTypes.func, contextId: PropTypes.string.isRequired, evidenceId: PropTypes.string.isRequired, canWrite: PropTypes.bool.isRequired, target: PropTypes.object, selection: PropTypes.object, cases: PropTypes.array.isRequired };

function PlanSection({ contextId, versions, viewed, cases, target, canWrite, onStarted, runs, productionVersion, seed, onSelectionChange, onCeilingChange, onEngage, primary }) {
  const preview = usePreviewNativeRun(contextId);
  const create = useCreateNativeRun(contextId);
  const reviewed = cases.filter((item) => item.reviewed);
  const numbers = versions.map((item) => item.number);
  // Defaults come from stored data, never from the screen: the version being viewed, the
  // production label, the last verification's recorded reference and historical examples
  // (or the example the reviewer just captured). Every default is visible and editable.
  const previous = (runs || []).find((run) => run.arms.some((arm) => arm.variant === 'reference'));
  const previousDetail = useNativeRun(contextId, previous?.id);
  const previousReference = previous?.arms.find((arm) => arm.variant === 'reference')?.version;
  const previousHistorical = (previousDetail.data?.cases || []).filter((item) => item.historical).map((item) => item.id);
  const [over, setOverState] = useState({});
  // Any edit to the plan (versions, runs, cases, the spend acceptance) is the reviewer working here, so the
  // run button becomes the page's one primary.
  const engaged = (setter) => (value) => { setter(value); onEngage?.(); };
  const setOver = engaged(setOverState);
  const pick = (name, fallback) => (name in over ? over[name] : fallback);
  const candidate = pick('candidate', viewed?.number ?? numbers[0] ?? '');
  const reference = pick('reference', seed?.reference ?? previousReference ?? '');
  const production = pick('production', productionVersion && Number(productionVersion) !== Number(candidate) && numbers.includes(productionVersion) ? productionVersion : '');
  const [repetitions, setRepetitionsState] = useState(1);
  const [chosenOver, setChosenOverState] = useState({});
  const [historicalOver, setHistoricalOverState] = useState({});
  const setRepetitions = engaged(setRepetitionsState);
  const setChosenOver = engaged(setChosenOverState);
  const setHistoricalOver = engaged(setHistoricalOverState);
  const isChosen = (id) => (id in chosenOver ? chosenOver[id] : seed?.caseId ? id === seed.caseId : true);
  const isHistorical = (id) => (id in historicalOver ? historicalOver[id] : seed?.caseId ? id === seed.caseId : previousHistorical.includes(id));
  const [fresh, setFresh] = useState('');
  const [ack, setAck] = useState(false);
  const [key, setKey] = useState(uuid);
  const [error, setError] = useState(null);
  // The paid run needs a workspace allowance. It is read (never changed) before consent, so a
  // missing allowance is a calm prerequisite beside the run, not an error after it.
  const spend = useSkillsSpendPolicy();
  const access = useSkillsAccess();
  const policyOff = Boolean(spend?.data) && (!spend.data.enabled || spend.data.periodLimitUsd == null);
  const spendRefused = getSkillErrorCode(error) === 'spend_policy_required';
  // A refusal while the policy is unknown also disables the run; once the re-read policy shows an
  // allowance, the reviewer may retry with the same operation key and the server checks again.
  const allowanceOff = policyOff || (spendRefused && !spend?.data);
  // The spend acceptance only means something once an allowance exists: while paid runs are off it is
  // held back (disabled and cleared), and it comes back unticked when an admin sets one. It stays its own
  // checkbox, separate from any sharing consent, and is never pre-ticked.
  useEffect(() => { if (allowanceOff) setAck(false); }, [allowanceOff]);
  const ackReason = allowanceOff ? 'Set an allowance first' : null;
  const caseIds = reviewed.filter((item) => isChosen(item.id)).map((item) => item.id);
  const duplicateBaseline = reference !== '' && production !== '' && Number(reference) === Number(production);
  const candidateIsReference = reference !== '' && Number(reference) === Number(candidate);
  const candidateIsProduction = production !== '' && Number(production) === Number(candidate);
  const baselines = { ...(reference !== '' ? { reference: Number(reference) } : {}), ...(production !== '' && !duplicateBaseline ? { production: Number(production) } : {}) };
  const plan = useMemo(() => ({
    bindingId: target?.bindingId, bindingRevision: target?.revision, candidateVersion: Number(candidate), baselines, caseIds,
    repetitions, historicalCaseIds: caseIds.filter((id) => isHistorical(id)),
  }), [target, candidate, reference, production, caseIds.join(), repetitions, historicalOver, seed, previousHistorical.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const planKey = JSON.stringify(plan);
  useEffect(() => { onSelectionChange?.(JSON.parse(planKey)); }, [planKey, onSelectionChange]);
  const ready = Boolean(!candidateIsReference && !candidateIsProduction && target?.qualified && candidate !== '' && Object.keys(baselines).length > 0 && caseIds.length > 0);
  // Planning is read-only (no launch, no spend): it runs as soon as the defaults or the reviewer's
  // changes make a plan, so the ceiling is already on the button when the reviewer decides.
  useEffect(() => {
    setFresh('');
    setAck(false);
    if (!ready) { preview.reset(); return; }
    preview.mutate(plan, { onSuccess: () => setFresh(planKey) });
  }, [planKey, ready]); // eslint-disable-line react-hooks/exhaustive-deps
  const result = ready ? preview.data : undefined;
  const current = Boolean(result) && fresh === planKey;
  // The live plan's ceiling, so an earlier result can say what rerunning the current selection costs.
  const ceiling = current && result.reproducible ? result.ceilingUsd : null;
  useEffect(() => { onCeilingChange?.(ceiling); }, [ceiling, onCeilingChange]);
  useEffect(() => () => onCeilingChange?.(null), [onCeilingChange]);
  // "Set allowance" carries the way back to this test (SH7 reads ?returnTo= on the allowance page).
  const location = useLocation();
  const allowanceHref = `${paths.dashboard.skills.allowance}?returnTo=${encodeURIComponent(`${location.pathname}${location.search}`)}`;
  const run = async () => {
    setError(null);
    try { onStarted(await create.mutateAsync({ idempotencyKey: key, plan, executionFingerprint: result.executionFingerprint, acknowledgeSpend: ack })); setKey(uuid()); setFresh(''); setAck(false); preview.reset(); }
    catch (reason) {
      // The same operation key is kept until a run starts, so a retry reconciles instead of starting twice.
      setError(reason || new Error('The verification did not start.'));
      if (getSkillErrorCode(reason) === 'spend_policy_required') spend?.refetch?.();
    }
  };
  const versionOptions = numbers.map((number) => <MenuItem key={number} value={number}>v{number}</MenuItem>);
  const compareOptions = numbers.map((number) => <MenuItem key={number} value={number}>v{number}{Number(productionVersion) === number ? ' (production)' : ''}</MenuItem>);
  const runReason = !canWrite ? 'Only a workspace author can run this test.' : allowanceOff ? 'Paid runs are off for this workspace.' : !result?.reproducible ? 'Add the missing historical inputs to continue.' : !current ? 'Wait for the updated plan before confirming.' : create.isPending ? 'Starting the application test…' : !ack ? 'Accept the provider-charge limit to continue.' : null;
  return <Section title={<Typography component="span" variant="m3">Application test</Typography>} description="Runs reviewed cases on the pinned application. Checks skill selection, file delivery, business actions and answers.">
    <Stack spacing={1.5}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
        <TextField select size="small" label="Test version" value={candidate} onChange={(event) => setOver({ ...over, candidate: event.target.value })} sx={{ minWidth: 150 }}>{versionOptions}</TextField>
        <TextField select size="small" label="Compare with" InputLabelProps={{ shrink: true }} SelectProps={{ displayEmpty: true }} error={candidateIsProduction} helperText={candidateIsProduction ? 'Choose a different version, or No comparison.' : production === '' && Number(productionVersion) === Number(candidate) ? `v${candidate} is production, so it runs once as the version tested.` : undefined} value={production} onChange={(event) => setOver({ ...over, production: event.target.value })} sx={{ minWidth: 190 }}><MenuItem value="">No comparison</MenuItem>{compareOptions}</TextField>
        <TextField select size="small" label="Was failing on" InputLabelProps={{ shrink: true }} SelectProps={{ displayEmpty: true }} error={candidateIsReference && ('candidate' in over || 'reference' in over)} helperText={candidateIsReference ? ('candidate' in over || 'reference' in over ? 'Choose a version different from the one being tested.' : `v${reference} is where it was failing. Choose the version that should fix it.`) : duplicateBaseline ? `v${reference} is also production, so it runs once.` : undefined} value={reference} onChange={(event) => setOver({ ...over, reference: event.target.value })} sx={{ minWidth: 170 }}><MenuItem value="">Not set</MenuItem>{versionOptions}</TextField>
        <TextField select size="small" label="Runs per case" value={repetitions} onChange={(event) => setRepetitions(Number(event.target.value))} sx={{ minWidth: 130 }}><MenuItem value={1}>1</MenuItem><MenuItem value={3}>3</MenuItem></TextField>
      </Stack>
      <Box role="group" aria-label="Examples to run">
        {reviewed.length ? <Table size="small" aria-label="Cases to run"><TableHead><TableRow><TableCell>Test case</TableCell><TableCell sx={{ position: 'relative' }}><Box component="span" sx={visuallyHidden}>Was failing</Box></TableCell></TableRow></TableHead><TableBody>{reviewed.map((item) => <TableRow key={item.id}><TableCell>
          <FormControlLabel control={<Checkbox size="small" checked={isChosen(item.id)} onChange={(event) => setChosenOver({ ...chosenOver, [item.id]: event.target.checked })} />} label={<Typography variant="s1">{item.title}</Typography>} />
          </TableCell><TableCell><FormControlLabel control={<Checkbox size="small" disabled={!isChosen(item.id)} checked={isChosen(item.id) && isHistorical(item.id)} onChange={(event) => setHistoricalOver({ ...historicalOver, [item.id]: event.target.checked })} />} label={<Typography variant="s3" color="text.secondary">{reference !== '' ? `Was failing on v${reference}` : 'Was failing before'}</Typography>} />
        </TableCell></TableRow>)}</TableBody></Table> : <Typography variant="s1">Review at least one case below before running it on the application.</Typography>}
      </Box>
      {!ready && <Typography variant="s3" color="text.secondary">{!target?.qualified ? 'Pin the configuration above to plan a run.' : !caseIds.length ? 'Select at least one reviewed case.' : candidateIsReference || candidateIsProduction ? 'Choose versions that differ from the one being tested.' : 'Choose a version to compare with, or the version it was failing on.'}</Typography>}
      {ready && preview.isPending && <ContentSkeleton label="Planning the application run" />}
      {preview.isError && <Alert severity="error">{preview.error?.response?.data?.error?.message || 'Could not plan this run. Check the target, versions and reviewed examples.'}</Alert>}
      {result && <Stack spacing={1}>
        {!result.reproducible ? <Alert severity="warning">
          <Typography variant="s1" component="div">Not reproduced: required historical inputs are missing, so nothing can launch.</Typography>
          {result.missingInputs.map((item) => <Typography key={`${item.caseId}/${item.input}`} variant="s1" component="div">{(cases.find((c) => c.id === item.caseId) || {}).title || item.caseId}: {item.input}. {item.detail}</Typography>)}
        </Alert> : <Typography variant="s3">Up to {result.cells} runs · at most ${result.ceilingUsd} · {result.arms.map((arm) => `${ARM[arm.variant]} v${arm.version}`).join(' · ')}</Typography>}
        {/* The plan is made for the reviewer; the paid run still needs the reviewer's own acceptance of its ceiling. */}
        {(policyOff || spendRefused) && <CapabilityNotice title="Paid runs need a workspace allowance" description={access.canAdmin ? 'Set a limit for paid test runs in this workspace, then run this test.' : 'Ask a workspace admin to set an allowance.'}
          actions={access.canAdmin ? <Button variant="outlined" size="small" component={RouterLink} to={allowanceHref}>Set allowance</Button>
            : <AdminAllowanceRequest request={allowanceRequestText({ purpose: 'an application test', ceilingUsd: result.reproducible ? result.ceilingUsd : null, href: allowanceHref })} />} />}
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={{ xs: 0, sm: 1.5 }} alignItems={{ xs: 'flex-start', sm: 'center' }}>
          <FormControlLabel control={<Checkbox size="small" checked={ack && !allowanceOff} disabled={!result.reproducible || !current || allowanceOff} inputProps={ackReason ? { 'aria-describedby': 'native-ack-reason' } : undefined} onChange={(event) => { setAck(event.target.checked); onEngage?.(); }} />} label={<Typography variant="s1">I accept up to ${result.ceilingUsd} in provider charges for this run.</Typography>} />
          <ActionReason id="native-ack-reason" reason={ackReason} />
        </Stack>
        {getSkillErrorCode(error) !== 'spend_policy_required' && <OperationFeedback error={error} action="start this run" onRetry={run} onReconcile={run} onRefresh={() => { setError(null); setFresh(''); preview.mutate(plan, { onSuccess: () => setFresh(planKey) }); }} />}
        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }}>
          <Button variant={primary ? "contained" : "outlined"} color="primary" disabled={Boolean(runReason)} {...reasonProps('native-run-reason', runReason)} onClick={run}>Run application test</Button>
          <ActionReason id="native-run-reason" reason={runReason} />
        </Stack>
      </Stack>}
    </Stack>
  </Section>;
}
PlanSection.propTypes = { onCeilingChange: PropTypes.func, onEngage: PropTypes.func, primary: PropTypes.bool, contextId: PropTypes.string.isRequired, versions: PropTypes.array.isRequired, viewed: PropTypes.object, cases: PropTypes.array.isRequired, target: PropTypes.object, canWrite: PropTypes.bool.isRequired, onStarted: PropTypes.func.isRequired, runs: PropTypes.array, productionVersion: PropTypes.number, seed: PropTypes.object, onSelectionChange: PropTypes.func };

// One History row: what was tested against what, how many cases, and a separate outcome badge.
// Application rows read their cases and decision from the run detail (shared with the result view);
// until it loads the outcome is a neutral placeholder, never a verdict.
const plural = (count, one) => `${count} ${one}${count === 1 ? '' : 's'}`;
function HistoryRow({ contextId, run, selected, onInspect }) {
  const application = run.type === 'application';
  const detail = useNativeRun(contextId, application ? run.id : null);
  let description;
  let outcome;
  if (application) {
    const arm = (variant) => run.arms.find((item) => item.variant === variant);
    const against = [arm('production') && `production v${arm('production').version}`, arm('reference') && `v${arm('reference').version} (was failing)`].filter(Boolean);
    const cases = detail.data?.cases?.length;
    description = [`Application test`, `v${arm('candidate')?.version ?? '?'}${against.length ? ` vs ${against.join(' and ')}` : ''}`, cases ? plural(cases, 'case') : null].filter(Boolean).join(' · ');
    outcome = detail.isError ? ['unknown', 'Not reported'] : applicationOutcome(detail.data ?? { jobState: run.jobState, status: run.status });
  } else {
    const cases = run.selected_case_ids?.length;
    description = [`Answer check`, `${run.candidate?.version ? `v${run.candidate.version}` : 'Saved draft'} vs ${run.reference?.version ? `v${run.reference.version}` : 'no context'}`, cases ? plural(cases, 'case') : null].filter(Boolean).join(' · ');
    outcome = answerOutcome(run);
  }
  return <TableRow hover selected={selected} sx={{ height: ROW_HEIGHT.default }}>
    <TableCell><Typography variant="s1">{description}</Typography></TableCell>
    <TableCell>{outcome ? <StatusBadge status={outcome[0]} label={outcome[1]} /> : <Typography variant="s3" color="text.secondary">Loading…</Typography>}</TableCell>
    <TableCell><Typography variant="s1"><When value={run.createdAt} /></Typography></TableCell>
    <TableCell align="right"><Button variant="outlined" size="small" aria-label={`Inspect ${description}`} onClick={onInspect}>Inspect</Button></TableCell>
  </TableRow>;
}
HistoryRow.propTypes = { contextId: PropTypes.string.isRequired, run: PropTypes.object.isRequired, selected: PropTypes.bool, onInspect: PropTypes.func.isRequired };

export default function ContextNativePanel({ contextId, versions, viewed, cases, canWrite, productionVersion, seed, children, reviewedCases, state, answerHistory, renderAnswer, answerRunId, historyPagination, latestAnswer, draftHandoff = false }) {
  const targets = useNativeTargets(contextId);
  const runs = useNativeRuns(contextId);
  const [selected, setSelected] = useState(null);
  const [historyFilter, setHistoryFilter] = useState('all');
  const [selection, setSelection] = useState(null);
  const [chosenKind, setKind] = useState(null);
  // false, or what the earlier result leads with: 'review', 'rerun' or 'production' (see RunDetail).
  const [earlier, setEarlier] = useState(false);
  const [planCeiling, setPlanCeiling] = useState(null);
  const [currentForm, setCurrentForm] = useState(false);
  const [packetReady, setPacketReady] = useState(false);
  const [startedForm, setStartedForm] = useState(false);
  const [formOpen, setFormOpen] = useState(null);
  const resultRegion = useRef(null);
  const formRegion = useRef(null);
  const answerForm = useRef(null);
  const applicationForm = useRef(null);
  useEffect(() => { if (answerRunId) setSelected({ type: 'answers', id: answerRunId }); }, [answerRunId]);
  useEffect(() => {
    setCurrentForm(false);
    if (!selected) return;
    focusQuietly(resultRegion.current);
    revealInMain(resultRegion.current);
  }, [selected]);
  const history = [
    ...(runs.data || []).map((run) => ({ ...run, type: 'application' })),
    ...(answerHistory?.data?.results || []).map((run) => ({ ...run, createdAt: run.created_at, type: 'answers' })),
  ].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  const visibleHistory = history.filter((run) => historyFilter === 'all' || run.type === historyFilter);
  const latest = [...(runs.data?.[0] ? [{ ...runs.data[0], type: 'application' }] : []), ...(latestAnswer ? [{ ...latestAnswer, createdAt: latestAnswer.created_at, type: 'answers' }] : history.filter((run) => run.type === 'answers').slice(0, 1))].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))[0];
  const focus = state === 'empty' ? null : selected || latest;
  const target = targets.data?.targets[0];
  // The application test is the default only where it can run: a reproduction handoff or a registered
  // application. With nothing registered (or no hosted runtime) the answer check comes first. Until the
  // targets load, the saved cases decide, as before. The reviewer's own choice always wins.
  const runtimeOff = state === 'off' || (targets.data && !targets.data.runtime?.available);
  const defaultKind = seed ? 'application' : draftHandoff || runtimeOff ? 'answers' : targets.data ? (targets.data.targets.length ? 'application' : 'answers') : (cases.length ? 'application' : 'answers');
  const kind = chosenKind ?? defaultKind;
  // The default depends on the registered targets, so the choice waits for them: showing a
  // provisional default would flip under the reviewer (and swallow a click on the flipped option).
  const kindPending = !chosenKind && !seed && !draftHandoff && !runtimeOff && Boolean(targets.isLoading) && !targets.data && state !== 'error';
  // "Run a test" starts open unless a current result is the subject of the page. The form stays
  // mounted while collapsed: it owns the run's idempotency key and the selection that marks an
  // earlier result, and neither may be lost by folding the section.
  const runOpen = formOpen ?? (!focus || Boolean(seed) || draftHandoff || earlier || startedForm || currentForm);
  const focusForm = (requestedKind) => {
    const nextKind = typeof requestedKind === 'string' ? requestedKind : kind;
    setStartedForm(true);
    setFormOpen(true);
    requestAnimationFrame(() => {
      revealInMain(formRegion.current);
      const form = nextKind === 'answers' ? answerForm.current : applicationForm.current;
      const field = form?.querySelector('textarea:not([disabled]),input:not([type="radio"]):not([aria-hidden="true"]):not([disabled]),[role="combobox"]:not([aria-disabled="true"])');
      focusQuietly(field || form?.querySelector('button:not([disabled])'));
    });
  };
  const runCurrent = () => { setCurrentForm(true); setKind('application'); focusForm('application'); };
  const runOnApplication = () => { setKind('application'); setCurrentForm(true); focusForm('application'); };
  // One contained primary at a time. With no result, a handoff, or once the reviewer edits the setup
  // (choosing a test type, changing the plan, accepting the spend, typing an answer check), it is the
  // setup's run or preview button. Opening the setup alone changes nothing: a result that offers its
  // release review keeps it as the primary. An earlier result leads with its own action (RunDetail),
  // except one for the version production already serves, which offers none, so the setup leads.
  // A reproduction handoff (seed) is engagement until the result in focus covers it and is ready.
  const engaged = !focus || currentForm || (Boolean(seed) && !packetReady) || draftHandoff;
  const formPrimary = runOpen && (earlier ? (currentForm || Boolean(seed) || earlier === 'production') : (!packetReady || engaged));
  const markEdited = useCallback(() => setCurrentForm(true), []);
  const historySection = <Section title={<Typography component="span" variant="m3">History</Typography>} description="Both kinds of test, newest first. Open a run to inspect the version it tested.">
    <TextField select size="small" label="Test type" value={historyFilter} onChange={(event) => setHistoryFilter(event.target.value)} sx={{ minWidth: 180, mb: 1.5 }}><MenuItem value="all">All tests</MenuItem><MenuItem value="answers">Answer checks</MenuItem><MenuItem value="application">Application tests</MenuItem></TextField>
    {runs.isError && <ErrorState title="Couldn't load application test history." onRetry={runs.refetch} />}
    {answerHistory?.isError && <ErrorState title="Couldn't load answer-check history." onRetry={answerHistory.refetch} />}
    {runs.isLoading || answerHistory?.isLoading ? <ContentSkeleton variant="rows" /> : visibleHistory.length && state !== 'empty' ? <TableContainer><Table size="small" aria-label="Test history"><TableHead><TableRow><TableCell>Test run</TableCell><TableCell>Outcome</TableCell><TableCell>Started</TableCell><TableCell /></TableRow></TableHead><TableBody>{visibleHistory.map((run) => <HistoryRow key={`${run.type}/${run.id}`} contextId={contextId} run={run} selected={focus?.type === run.type && focus?.id === run.id} onInspect={() => setSelected({ type: run.type, id: run.id })} />)}</TableBody></Table></TableContainer> : historyFilter !== 'all' ? <EmptyState variant="no-results" title="No matching test runs" description="Try the other test type or show all runs." onClearFilters={() => setHistoryFilter('all')} /> : <Typography component="div" variant="s3" color="text.secondary">Your test runs will appear here.</Typography>}
    {historyPagination}
  </Section>;
  const showHistoryFirst = history.length > 0 && state !== 'empty';
  return <Stack spacing={3}>
    <Box ref={resultRegion} tabIndex={-1} role="region" aria-label="Latest result">
      <Section>
        {state === 'loading' ? <ContentSkeleton variant="panel" label="Loading test results" /> : focus ? focus.type === 'answers' ? renderAnswer?.(focus.id, { onRunApplication: runOnApplication, onReady: setPacketReady, secondary: formPrimary, hasTarget: Boolean(target), applicationAvailable: Boolean(targets.data) && !runtimeOff }) : <RunDetail key={focus.id} contextId={contextId} evidenceId={focus.id} canWrite={canWrite} target={target} selection={selection} cases={cases} onReady={setPacketReady} onRunCurrent={runCurrent} onEarlier={setEarlier} formPrimary={formPrimary} productionVersion={productionVersion} versions={versions} planCeiling={planCeiling} /> : runs.isLoading || answerHistory?.isLoading ? <ContentSkeleton variant="panel" label="Loading test results" /> : runs.isError || answerHistory?.isError ? <ErrorState title="Couldn't load latest results." onRetry={() => { runs.refetch(); answerHistory?.refetch(); }} /> : <EmptyState sx={{ py: 3 }} title="No tests yet" description="Choose an answer check or an application test below. Neither changes production." />}
      </Section>
    </Box>
    {showHistoryFirst && historySection}
    <Box ref={formRegion}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1.5} sx={{ minWidth: 0 }}>
        <Typography component="h2" id="run-a-test-heading" variant="m3">Run a test</Typography>
        <Button size="small" color="inherit" aria-expanded={runOpen} aria-controls="run-a-test-body" onClick={() => setFormOpen(!runOpen)} startIcon={<Iconify icon={runOpen ? 'solar:alt-arrow-down-linear' : 'solar:alt-arrow-right-linear'} width={16} />}>{runOpen ? 'Hide setup' : 'Show setup'}</Button>
      </Stack>
      <Collapse in={runOpen} id="run-a-test-body" role="region" aria-labelledby="run-a-test-heading">
        <Stack spacing={2} sx={{ pt: 1.5 }}>
          <FormControl><FormLabel id="test-kind-label" sx={{ color: 'text.primary' }}>Choose what to test</FormLabel>
            {kindPending ? <ContentSkeleton variant="rows" rows={1} label="Loading test options" /> : <RadioGroup aria-labelledby="test-kind-label" value={kind} onChange={(event) => { setKind(event.target.value); setStartedForm(true); setCurrentForm(true); }} sx={{ flexDirection: { xs: 'column', sm: 'row' }, gap: 1.5 }}>
              <FormControlLabel value="answers" control={<Radio size="small" />} label={<Stack><Typography variant="s1" sx={{ position: 'relative' }}>Check answers<Box component="span" sx={visuallyHidden}> with this context</Box></Typography><Typography variant="s3" color="text.secondary">Compare answers using your instructions and linked skills. No workspace allowance needed.</Typography></Stack>} />
              <FormControlLabel value="application" control={<Radio size="small" />} label={<Stack><Typography variant="s1">Test on the application</Typography><Typography variant="s3" color="text.secondary">Run reviewed cases on your app. Uses your workspace allowance.</Typography></Stack>} />
            </RadioGroup>}
          </FormControl>
          <Box ref={answerForm} hidden={kindPending || kind !== 'answers'}>{typeof children === 'function' ? children(formPrimary && kind === 'answers', markEdited) : children}</Box>
          <Box ref={applicationForm} hidden={kindPending || kind !== 'application'}><Stack spacing={2}>
            {state === 'loading' || (targets.isLoading && !targets.data) ? <ContentSkeleton variant="panel" label="Loading application target" /> : state === 'error' || targets.isError ? <ErrorState title="Couldn't load application test options." detail="You can still open past results and reviewed cases." onRetry={targets.refetch} sx={{ alignItems: 'flex-start', textAlign: 'left', py: 1.5, px: 0 }} /> : state === 'off' || !targets.data?.runtime.available ? <CapabilityNotice title="Application tests are off" description="This workspace has no hosted application runtime. You can still run an answer check, but a text answer doesn't prove the application is repaired." /> : <>
              <TargetSection contextId={contextId} version={viewed} canWrite={canWrite} targets={targets} />
              <PlanSection contextId={contextId} versions={versions} viewed={viewed} cases={cases} target={target} canWrite={canWrite} onStarted={(id) => { setSelected({ type: 'application', id }); setFormOpen(null); setStartedForm(false); }} onSelectionChange={setSelection} onCeilingChange={setPlanCeiling} onEngage={markEdited} runs={runs.data} productionVersion={productionVersion} seed={seed} primary={formPrimary && kind === 'application'} />
            </>}
          </Stack></Box>
        </Stack>
      </Collapse>
    </Box>
    {reviewedCases}
    {!showHistoryFirst && historySection}
  </Stack>;
}
ContextNativePanel.propTypes = { contextId: PropTypes.string.isRequired, versions: PropTypes.array.isRequired, viewed: PropTypes.object, cases: PropTypes.array.isRequired, canWrite: PropTypes.bool.isRequired, productionVersion: PropTypes.number, seed: PropTypes.object, children: PropTypes.oneOfType([PropTypes.node, PropTypes.func]), reviewedCases: PropTypes.node, state: PropTypes.string, answerHistory: PropTypes.object, renderAnswer: PropTypes.func, answerRunId: PropTypes.string, historyPagination: PropTypes.node, latestAnswer: PropTypes.object, draftHandoff: PropTypes.bool };
