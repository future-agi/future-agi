import { useEffect, useMemo, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate } from 'react-router-dom';
import { v4 as uuid } from 'uuid';
import {
  Alert, Box, Button, Checkbox, Drawer, FormControl, FormControlLabel, FormLabel, IconButton, LinearProgress, MenuItem, Radio, RadioGroup, Stack, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import {
  useCreateNativeRun, useCreateNativeTarget, useNativeRun, useNativeRuns, useNativeTargets, usePreviewNativeRun,
  usePrepareReleasePacket, useQualifyNativeTarget, useRuntimeConfiguration,
} from 'src/api/skills/context-testing';
import { useAuthContext } from 'src/auth/hooks';
import { useContextDetail } from 'src/api/skills/context-authoring';
import { fDateTime } from 'src/utils/format-time';
import { useRecoveryReviewers } from 'src/api/skills/j6-recovery';
import { paths } from 'src/routes/paths';
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
const ARM = { candidate: 'Candidate', reference: 'Recorded', production: 'Production' };
const FAMILIES = [['selection', 'Skill selected'], ['delivery', 'File delivered'], ['tools', 'Business tool'], ['answer', 'Answer']];
const localTime = (value) => value ? fDateTime(value, 'MMM d, h:mm a') : 'Time not reported';
const disagreementLabel = (count) => `${count} disagreement${count === 1 ? '' : 's'}`;
const VERDICTS = [['agree', 'Agree with the automatic outcome'], ['disagree', 'Disagree with the automatic outcome'], ['cannot_assess', 'Cannot assess']];

function Verdict({ value, status }) {
  return value ? <StatusBadge status={value === 'pass' ? 'passed' : value === 'fail' ? 'failed' : 'unknown'} /> : <StatusBadge status={status === 'failed' ? 'failed' : 'pending'} />;
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
      <StatusBadge status={target.qualified ? 'info' : 'unknown'} role={target.qualified ? 'info' : 'warning'} label={target.qualified ? 'Configuration pinned' : 'Configuration not pinned'} />
      {target.qualified && <IdentityChip value={target.configurationDigest} label="Configuration" />}
      <Button size="small" variant="outlined" disabled={!canWrite} onClick={() => setReview(target)}>Review runtime configuration</Button>
      <Button size="small" variant="outlined" disabled={!canWrite} onClick={() => setReview(target)}>Change</Button>
    </Stack>}
    <TechnicalDetails title="Runtime details"><Typography variant="s3">Runtime: {targets.data.runtime.runtime} · evidence class: {targets.data.runtime.evidenceClass || 'unstated'}</Typography></TechnicalDetails>
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
          <Button variant="contained" disabled={!configuration.data || qualify.isPending} onClick={pin}>Pin this configuration</Button>
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
    } catch { setError('Could not record this review. Reload the evidence and retry; the automatic outcome was not changed.'); }
  };
  const seen = cell.observed;
  return <Drawer anchor="right" open onClose={onClose} aria-labelledby="attempt-heading" PaperProps={{ sx: { width: 460, maxWidth: '100vw', p: 3 } }}>
    <Stack spacing={1.5}>
      <Stack direction="row" justifyContent="space-between" alignItems="center"><Typography id="attempt-heading" component="h2" variant="m1">{caseTitle}</Typography><IconButton aria-label="Close" onClick={onClose}><Iconify icon="solar:close-circle-linear" /></IconButton></Stack>
      <Typography variant="s3" color="text.secondary">{ARM[cell.variant]} · repeat {cell.repeatIndex + 1}</Typography>
      <Section title={<Typography component="span" variant="m3">Observed by the application</Typography>}>
        {seen ? <Stack spacing={0.5}>
          <Typography variant="s1">Skills opened: {seen.selected.length ? seen.selected.join(', ') : 'none'}</Typography>
          <Typography variant="s1">Business actions: {seen.actions.length ? seen.actions.map((a) => `${a.action} (${a.status})`).join(', ') : 'none attempted'}</Typography>
          <Typography variant="s1" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>Answer: {seen.answer || '—'}</Typography>
          <TechnicalDetails title="Technical details"><IdentityChip value={seen.context_digest} label="Context" /><IdentityChip value={seen.session_id} label="Session" kind="id" /></TechnicalDetails>
        </Stack> : <Typography variant="s1">This cell did not return an observation.</Typography>}
      </Section>
      <Section title={<Typography component="span" variant="m3">Automatic outcomes</Typography>} description={mine.some((row) => row.verdict === 'disagree') ? `${disagreementLabel(mine.filter((row) => row.verdict === 'disagree').length)} below` : undefined}>
        <Stack spacing={0.5}>{FAMILIES.map(([key, label]) => <Stack key={key} direction="row" justifyContent="space-between"><Typography variant="s1">{label}</Typography><Verdict value={cell[key]} /></Stack>)}</Stack>
      </Section>
      <Section title={<Typography component="span" variant="m3">Human review</Typography>} description="Appended beside the automatic grade; it never replaces it.">
        {annotations.isLoading ? <ContentSkeleton variant="rows" label="Loading human reviews" /> : annotations.isError ? <ErrorState title="Couldn't load human reviews" onRetry={annotations.refetch} /> : !mine.length ? <Typography variant="s3" color="text.secondary">No human reviews for this attempt.</Typography> : mine.map((row) => <Stack key={row.id} spacing={0.5}>
          <StatusBadge status={row.verdict === 'disagree' ? 'warning' : 'info'} role={row.verdict === 'disagree' ? 'warning' : 'info'} label={{ agree: 'Agrees', disagree: 'Disagrees', cannot_assess: "Can’t assess" }[row.verdict]} sx={{ alignSelf: 'flex-start' }} />
          <Typography variant="s1">{row.comment || 'No comment'}</Typography>
          <Typography variant="s3">{authorName(row.actorId)}</Typography>
          <TechnicalDetails title="Author details"><IdentityChip value={row.actorId} label="Author" kind="id" /></TechnicalDetails>
          <Typography variant="s3" color="text.secondary">{localTime(row.createdAt)} · Applies to the whole attempt</Typography>
        </Stack>)}
        <Stack spacing={1} sx={{ mt: 1 }}>
          <FormControl disabled={!canWrite}>
            <FormLabel id="assessment-label">Your assessment</FormLabel>
            <RadioGroup aria-labelledby="assessment-label" value={verdict} onChange={(event) => setVerdict(event.target.value)}>
              {VERDICTS.map(([value, label]) => <FormControlLabel key={value} value={value} control={<Radio size="small" />} label={<Typography variant="s1">{label}</Typography>} />)}
            </RadioGroup>
          </FormControl>
          <TextField size="small" label="Why" value={comment} onChange={(event) => setComment(event.target.value)} multiline minRows={2} disabled={!canWrite} />
          {error && <Alert severity="error">{error}</Alert>}
          <Button variant="outlined" disabled={!canWrite || !verdict || !reference.data || append.isPending} onClick={submit}>Record review</Button>
        </Stack>
      </Section>
    </Stack>
  </Drawer>;
}
CellDrawer.propTypes = { run: PropTypes.object.isRequired, cell: PropTypes.object.isRequired, caseTitle: PropTypes.string, onClose: PropTypes.func.isRequired, canWrite: PropTypes.bool.isRequired };

function ReleasePacket({ contextId, run, target, canWrite, earlier, onReady }) {
  const { user } = useAuthContext();
  const prepare = usePrepareReleasePacket(contextId);
  const context = useContextDetail(contextId);
  const [pipeline, setPipeline] = useState({ url: '', configurationIdentity: '', decision: '' });
  const set = (key) => (event) => setPipeline({ ...pipeline, [key]: event.target.value });
  useEffect(() => { onReady?.(Boolean(prepare.data)); return () => onReady?.(false); }, [prepare.data, onReady]);
  const candidate = run.arms.find((arm) => arm.variant === 'candidate');
  const valid = pipeline.url.trim() && pipeline.configurationIdentity.trim() && pipeline.decision.trim();
  return <Section title={<Typography component="span" variant="m3">Release packet</Typography>} description="Prepares the exact version, runtime target and shared evidence for the separate release review. Nothing is released here.">
    <Stack spacing={1.5}>
      <Typography variant="s1">Ready to request release review for v{candidate?.version} — add your CI run to attach it as evidence.</Typography>
      <TechnicalDetails title="Attach your CI run">
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5} sx={{ '& > *': { flex: 1 } }}>
      <TextField size="small" label="Retained pipeline run URL" helperText="CI run link" value={pipeline.url} onChange={set('url')} />
      <TextField size="small" label="Pipeline configuration identity" helperText="CI configuration ID" value={pipeline.configurationIdentity} onChange={set('configurationIdentity')} />
      <TextField size="small" label="Pipeline decision" helperText="CI result (pass/hold)" value={pipeline.decision} onChange={set('decision')} />
      </Stack>
      </TechnicalDetails>
      {prepare.isError && <Alert severity="error">{prepare.error?.response?.data?.error?.message || 'The packet could not be prepared. Check the shared evidence, target and pipeline record.'}</Alert>}
      {prepare.data && <>
        <Alert severity="success">Packet ready — this run is shared with your workspace.</Alert>
        <Button variant={earlier ? "outlined" : "contained"} href={reviewComposerUrl({ kind: 'agent_context', subjectId: contextId, name: context.data?.name, candidateId: candidate?.versionId, number: candidate?.version, digest: candidate?.digest, label: 'production', evidence: [{ source: 'simulation_native', sourceId: run.id }], target: target?.bindingId })}>Continue to release review</Button>
        <TechnicalDetails title="Packet details"><IdentityChip value={prepare.data.fingerprint} label="Packet fingerprint" /><IdentityChip value={prepare.data.targetDigest} label="Target configuration" /></TechnicalDetails>
      </>}
      {earlier && <Typography variant="s3">This packet applies to the earlier run, not your current selection.</Typography>}
      <Box><Button variant="outlined" size="small" disabled={!canWrite || !target || !valid || prepare.isPending} onClick={() => prepare.mutate({ run, target, label: 'production', pipeline, userId: user?.id })}>Share evidence and prepare packet</Button></Box>
      <Typography variant="s3" color="text.secondary">{!canWrite ? 'Only a workspace author can share this run.' : !target ? 'Add an application target to share this run.' : !valid ? 'Fill the three CI fields to share this run.' : prepare.isPending ? 'Preparing the packet…' : 'The packet needs a separate release review; nothing is released here.'}</Typography>
    </Stack>
  </Section>;
}
ReleasePacket.propTypes = { earlier: PropTypes.bool, onReady: PropTypes.func, contextId: PropTypes.string.isRequired, run: PropTypes.object.isRequired, target: PropTypes.object, canWrite: PropTypes.bool.isRequired };

function RunDetail({ contextId, evidenceId, canWrite, target, selection, cases, onReady, onRunCurrent, onEarlier, currentForm }) {
  const query = useNativeRun(contextId, evidenceId);
  const navigate = useNavigate();
  const annotations = useAllEvidenceAnnotations('simulation_native', evidenceId);
  const origin = useRef(null);
  const [open, setOpen] = useState(null);
  const selectedCandidate = query.data?.arms.find((arm) => arm.variant === 'candidate');
  const stale = Boolean(selection && query.data && (selectedCandidate?.version !== selection.candidateVersion || JSON.stringify((query.data.cases || []).map((item) => item.id).sort()) !== JSON.stringify([...selection.caseIds].sort())));
  useEffect(() => { onEarlier?.(stale); return () => onEarlier?.(false); }, [stale, onEarlier]);
  if (query.isLoading) return <ContentSkeleton label="Loading verification result" />;
  if (query.isError) return <ErrorState title="Could not load this verification" onRetry={query.refetch} />;
  const run = query.data;
  if (!run) return null;
  const titles = Object.fromEntries((run.cases || []).map((item) => [item.id, item.title]));
  const jobState = run.jobState || run.status;
  const active = ['queued', 'provisioning', 'preparing', 'running'].includes(jobState);
  const received = run.received || 0;
  const status = active && received > 0 ? 'running' : jobState === 'provisioning' ? 'preparing' : jobState;
  const candidate = run.arms.find((arm) => arm.variant === 'candidate');
  const runCases = (run.cases || []).map((item) => item.id).sort();
  const earlier = stale;
  const disagreements = annotations.data?.results.filter((row) => row.verdict === 'disagree') || [];
  const untested = cases.filter((item) => !runCases.includes(item.id)).length;
  const inspect = (cell, event) => { origin.current = event.currentTarget; setOpen(cell); };
  const referenceArm = run.arms.find((arm) => arm.variant === 'reference');
  return <Stack spacing={1.5}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }} sx={{ minWidth: 0 }}>
      <Typography component="h3" variant="m3" data-execution-fingerprint={run.executionFingerprint}>Application verification</Typography>
      <StatusBadge status={status === 'preparing' ? 'running' : status} label={status === 'preparing' ? 'Preparing' : undefined} />
      <Typography variant="s3" color="text.secondary" role="status">{received} of {run.planned} runs finished{active && received === run.planned ? ' · Finalizing result' : ''}</Typography>
    </Stack>
    <Stack direction="row" spacing={1.5} flexWrap="wrap" alignItems="center"><Typography variant="s3">v{candidate?.version}{referenceArm ? ` vs recorded v${referenceArm.version}` : ''} · {runCases.length} of {cases.length} cases · {untested} not tested · {localTime(run.createdAt)}</Typography><IdentityChip value={run.configurationDigest} label="Configuration" /></Stack>
    {active && <LinearProgress aria-label="Application run progress" variant={run.planned ? "determinate" : "indeterminate"} value={run.planned ? Math.min(100, received / run.planned * 100) : 0} />}
    {earlier && <Stack spacing={1} sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1 }}><StatusBadge status="unknown" label="Earlier result" sx={{ alignSelf: 'flex-start' }} /><Typography variant="s1">This result is for v{candidate?.version} with {runCases.length} case{runCases.length === 1 ? '' : 's'}; your current selection is different.</Typography><Button variant={currentForm ? "outlined" : "contained"} onClick={onRunCurrent} sx={{ alignSelf: 'flex-start' }}>Run on current selection</Button></Stack>}
    {run.failure && <Alert severity="error">The run stopped: {run.failure.replaceAll('_', ' ')}. No outcome is inferred.</Alert>}
    {run.decision && !active && <Box sx={{ p: 1.5, border: 1, borderColor: 'divider', borderRadius: 1, color: earlier ? 'text.secondary' : 'text.primary' }}>
      <Typography variant="s1" component="div">{run.decision.state === 'release_ready' ? 'Application checks passed for these cases' : 'Hold this release'}</Typography>
      {run.decision.state === 'release_ready' && <Typography variant="s3" component="div">Ready for release review</Typography>}
      <TechnicalDetails title="How it was decided">{run.decision.reasons.map((reason) => <Typography key={reason} variant="s1" component="div">{reason}</Typography>)}</TechnicalDetails>
    </Box>}
    {run.result && !active && <Typography variant="s1">Historical failure: {run.result.reproduction.replaceAll('_', ' ')} · Repair {run.result.repair_verified ? 'verified' : 'not verified'}{run.result.regressed_cases?.length ? ` · ${run.result.regressed_cases.length} neighboring case(s) regressed` : ''}</Typography>}
    {!active && canWrite && run.result?.reproduction === 'reproduced' && referenceArm && <Box>
      <Button variant="outlined" size="small" onClick={() => navigate(`${paths.dashboard.skills.contextEdit(contextId)}?from=${referenceArm.version}&preserve=1`)}>{`Repair from recorded v${referenceArm.version}`}</Button>
    </Box>}
    {run.runtime && <TechnicalDetails title="Runtime details"><Typography variant="s3" color="text.secondary">Runtime proved read-only source {String(run.runtime.read_only_source)}, read-only context {String(run.runtime.read_only_context)}, closed paid egress {String(run.runtime.controlled_paid_egress)}.</Typography><IdentityChip value={run.executionFingerprint} label="Execution fingerprint" /></TechnicalDetails>}
    {annotations.isError ? <ErrorState title="Couldn't load human reviews" onRetry={annotations.refetch} /> : <Typography variant="s3">{annotations.isLoading ? 'Loading human reviews…' : `${disagreementLabel(disagreements.length)} to inspect`}</Typography>}
    {run.cells && <TableContainer>
      <Table size="small" aria-label="Verification cells" sx={{
        '& thead': { display: { xs: 'none', sm: 'table-header-group' } },
        '& tbody': { display: { xs: 'block', sm: 'table-row-group' } },
        '& tbody tr': { display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: '1fr 1fr', height: { xs: 'auto', sm: ROW_HEIGHT.default }, py: { xs: 1, sm: 0 } },
        '& tbody td': { display: { xs: 'block', sm: 'table-cell' }, whiteSpace: 'normal', overflowWrap: 'anywhere' },
        '& tbody td:first-of-type, & tbody td:last-of-type': { gridColumn: '1 / -1' },
      }}><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
        <TableCell>Example</TableCell><TableCell>Context</TableCell><TableCell>Run</TableCell>{FAMILIES.map(([key, label]) => <TableCell key={key}>{label}</TableCell>)}<TableCell>Human review</TableCell><TableCell />
      </TableRow></TableHead><TableBody>{run.cells.map((cell) => <TableRow key={`${cell.caseId}/${cell.variant}/${cell.repeatIndex}`} hover sx={{ height: ROW_HEIGHT.default, cursor: cell.attemptId ? 'pointer' : 'default' }} onClick={(event) => cell.attemptId && inspect(cell, event)}>
        <TableCell>{titles[cell.caseId] || cell.caseId}</TableCell><TableCell>{ARM[cell.variant]} v{run.arms.find((arm) => arm.variant === cell.variant)?.version}</TableCell><TableCell>{cell.repeatIndex + 1}</TableCell>
        {FAMILIES.map(([key, label]) => <TableCell key={key}><Typography variant="s3" color="text.secondary" sx={{ display: { xs: 'block', sm: 'none' } }}>{label}</Typography><Verdict value={cell.status === 'completed' || !active ? cell[key] : undefined} status={cell.status} /></TableCell>)}
        <TableCell>{annotations.isError ? 'Unavailable' : annotations.isLoading ? 'Loading…' : disagreements.filter((row) => row.attemptId === cell.attemptId).length ? <StatusBadge status="warning" label={disagreementLabel(disagreements.filter((row) => row.attemptId === cell.attemptId).length)} /> : '—'}</TableCell>
        <TableCell><Button size="small" variant="outlined" disabled={!cell.attemptId} aria-label={`Inspect ${titles[cell.caseId] || cell.caseId} ${ARM[cell.variant]} repeat ${cell.repeatIndex + 1}`} onClick={(event) => { event.stopPropagation(); inspect(cell, event); }}>Inspect</Button></TableCell>
      </TableRow>)}</TableBody></Table>
    </TableContainer>}
    {run.decision?.state === 'release_ready' && !active && <ReleasePacket contextId={contextId} run={run} target={target} canWrite={canWrite} earlier={earlier} onReady={onReady} />}
    {open && <CellDrawer run={run} cell={open} caseTitle={titles[open.caseId]} canWrite={canWrite} onClose={() => { setOpen(null); origin.current?.focus(); }} />}
  </Stack>;
}
RunDetail.propTypes = { currentForm: PropTypes.bool, onEarlier: PropTypes.func, onReady: PropTypes.func, onRunCurrent: PropTypes.func, contextId: PropTypes.string.isRequired, evidenceId: PropTypes.string.isRequired, canWrite: PropTypes.bool.isRequired, target: PropTypes.object, selection: PropTypes.object, cases: PropTypes.array.isRequired };

function PlanSection({ contextId, versions, viewed, cases, target, canWrite, onStarted, runs, productionVersion, seed, onSelectionChange, primary }) {
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
  const [over, setOver] = useState({});
  const pick = (name, fallback) => (name in over ? over[name] : fallback);
  const candidate = pick('candidate', viewed?.number ?? numbers[0] ?? '');
  const reference = pick('reference', seed?.reference ?? previousReference ?? '');
  const production = pick('production', productionVersion && Number(productionVersion) !== Number(candidate) && numbers.includes(productionVersion) ? productionVersion : '');
  const [repetitions, setRepetitions] = useState(1);
  const [chosenOver, setChosenOver] = useState({});
  const [historicalOver, setHistoricalOver] = useState({});
  const isChosen = (id) => (id in chosenOver ? chosenOver[id] : seed?.caseId ? id === seed.caseId : true);
  const isHistorical = (id) => (id in historicalOver ? historicalOver[id] : seed?.caseId ? id === seed.caseId : previousHistorical.includes(id));
  const [fresh, setFresh] = useState('');
  const [ack, setAck] = useState(false);
  const [key, setKey] = useState(uuid);
  const [error, setError] = useState('');
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
  const run = async () => {
    setError('');
    try { onStarted(await create.mutateAsync({ idempotencyKey: key, plan, executionFingerprint: result.executionFingerprint, acknowledgeSpend: ack })); setKey(uuid()); setFresh(''); setAck(false); preview.reset(); }
    catch (reason) { setError(reason?.response?.data?.error?.message || 'The verification did not start. Review the plan again before retrying.'); }
  };
  const versionOptions = numbers.map((number) => <MenuItem key={number} value={number}>v{number}</MenuItem>);
  return <Section title={<Typography component="span" variant="m3">Verify on the application</Typography>} description="Runs reviewed cases on the pinned application. Checks skill selection, file delivery, business actions and answers.">
    <Stack spacing={1.5}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
        <TextField select size="small" label="Candidate version" value={candidate} onChange={(event) => setOver({ ...over, candidate: event.target.value })} sx={{ minWidth: 170 }}>{versionOptions}</TextField>
        <TextField select size="small" label="Recorded reference version" error={candidateIsReference} helperText={candidateIsReference ? 'Choose a reference different from the candidate.' : duplicateBaseline ? `Recorded reference v${reference} · also production` : undefined} value={reference} onChange={(event) => setOver({ ...over, reference: event.target.value })} sx={{ minWidth: 210 }}><MenuItem value="">None</MenuItem>{versionOptions}</TextField>
        <TextField select size="small" label="Production version" error={candidateIsProduction} helperText={candidateIsProduction ? 'Choose production different from the candidate, or None.' : undefined} value={production} onChange={(event) => setOver({ ...over, production: event.target.value })} sx={{ minWidth: 170 }}><MenuItem value="">None</MenuItem>{versionOptions}</TextField>
        <TextField select size="small" label="Repetitions" value={repetitions} onChange={(event) => setRepetitions(Number(event.target.value))} sx={{ minWidth: 130 }}><MenuItem value={1}>1 each</MenuItem><MenuItem value={3}>3 each</MenuItem></TextField>
      </Stack>
      <Box role="group" aria-label="Examples to run">
        {reviewed.length ? <Table size="small" aria-label="Cases to run"><TableHead><TableRow><TableCell>Run</TableCell><TableCell>Reproduce recorded failure</TableCell></TableRow></TableHead><TableBody>{reviewed.map((item) => <TableRow key={item.id}><TableCell>
          <FormControlLabel control={<Checkbox size="small" checked={isChosen(item.id)} onChange={(event) => setChosenOver({ ...chosenOver, [item.id]: event.target.checked })} />} label={<Typography variant="s1">{item.title}</Typography>} />
          </TableCell><TableCell><FormControlLabel control={<Checkbox size="small" disabled={!isChosen(item.id)} checked={isChosen(item.id) && isHistorical(item.id)} onChange={(event) => setHistoricalOver({ ...historicalOver, [item.id]: event.target.checked })} />} label={<Typography variant="s3" color="text.secondary">Recorded failure to reproduce</Typography>} />
        </TableCell></TableRow>)}</TableBody></Table> : <Typography variant="s1">Review at least one example below before running it on the application.</Typography>}
      </Box>
      {!ready && <Typography variant="s3" color="text.secondary">Pin an application configuration, choose two different versions and select at least one reviewed case.</Typography>}
      {ready && preview.isPending && <ContentSkeleton label="Planning the application run" />}
      {preview.isError && <Alert severity="error">{preview.error?.response?.data?.error?.message || 'Could not plan this run. Check the target, versions and reviewed examples.'}</Alert>}
      {result && <Stack spacing={1}>
        {!result.reproducible ? <Alert severity="warning">
          <Typography variant="s1" component="div">Not reproduced: required historical inputs are missing, so nothing can launch.</Typography>
          {result.missingInputs.map((item) => <Typography key={`${item.caseId}/${item.input}`} variant="s1" component="div">{(cases.find((c) => c.id === item.caseId) || {}).title || item.caseId}: {item.input}. {item.detail}</Typography>)}
        </Alert> : <Typography variant="s3">Up to {result.cells} runs · at most ${result.ceilingUsd} · {result.arms.map((arm) => `${ARM[arm.variant]} v${arm.version}`).join(' · ')}</Typography>}
        {/* The plan is made for the reviewer; the paid run still needs the reviewer's own acceptance of its ceiling. */}
        <FormControlLabel control={<Checkbox size="small" checked={ack} disabled={!result.reproducible || !current} onChange={(event) => setAck(event.target.checked)} />} label={<Typography variant="s1">I accept up to ${result.ceilingUsd} in provider charges for this run.</Typography>} />
        {error && <Alert severity="error">{error}</Alert>}
        <Box><Button variant={primary ? "contained" : "outlined"} size="small" disabled={!canWrite || !result.reproducible || !current || !ack || create.isPending} onClick={run}>Run on the application</Button></Box>
        {(!canWrite || !result.reproducible || !current || !ack || create.isPending) && <Typography variant="s3" color="text.secondary">{!canWrite ? 'Only a workspace author can run this test.' : !result.reproducible ? 'Add the missing historical inputs to continue.' : !current ? 'Wait for the updated plan before confirming.' : create.isPending ? 'Starting the application run…' : 'Confirm the provider-charge acknowledgment to continue.'}</Typography>}
      </Stack>}
    </Stack>
  </Section>;
}
PlanSection.propTypes = { primary: PropTypes.bool, contextId: PropTypes.string.isRequired, versions: PropTypes.array.isRequired, viewed: PropTypes.object, cases: PropTypes.array.isRequired, target: PropTypes.object, canWrite: PropTypes.bool.isRequired, onStarted: PropTypes.func.isRequired, runs: PropTypes.array, productionVersion: PropTypes.number, seed: PropTypes.object, onSelectionChange: PropTypes.func };

export default function ContextNativePanel({ contextId, versions, viewed, cases, canWrite, productionVersion, seed, children, reviewedCases, state, answerHistory, renderAnswer, answerRunId, historyPagination, latestAnswer }) {
  const targets = useNativeTargets(contextId);
  const runs = useNativeRuns(contextId);
  const [selected, setSelected] = useState(null);
  const [historyFilter, setHistoryFilter] = useState('all');
  const [selection, setSelection] = useState(null);
  const [kind, setKind] = useState(seed || cases.length ? 'application' : 'answers');
  const [earlier, setEarlier] = useState(false);
  const [currentForm, setCurrentForm] = useState(false);
  const [packetReady, setPacketReady] = useState(false);
  const [startedForm, setStartedForm] = useState(false);
  const resultRegion = useRef(null);
  const formRegion = useRef(null);
  const answerForm = useRef(null);
  const applicationForm = useRef(null);
  useEffect(() => { if (answerRunId) setSelected({ type: 'answers', id: answerRunId }); }, [answerRunId]);
  useEffect(() => { setCurrentForm(false); if (selected) resultRegion.current?.focus(); }, [selected]);
  const history = [
    ...(runs.data || []).map((run) => ({ ...run, type: 'application' })),
    ...(answerHistory?.data?.results || []).map((run) => ({ ...run, createdAt: run.created_at, type: 'answers' })),
  ].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt));
  const visibleHistory = history.filter((run) => historyFilter === 'all' || run.type === historyFilter);
  const latest = [...(runs.data?.[0] ? [{ ...runs.data[0], type: 'application' }] : []), ...(latestAnswer ? [{ ...latestAnswer, createdAt: latestAnswer.created_at, type: 'answers' }] : history.filter((run) => run.type === 'answers').slice(0, 1))].sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))[0];
  const focus = state === 'empty' ? null : selected || latest;
  const firstRun = !focus && (state === 'empty' || !history.length) && !startedForm;
  const target = targets.data?.targets[0];
  const focusForm = (requestedKind) => {
    const chosenKind = typeof requestedKind === 'string' ? requestedKind : kind;
    setStartedForm(true);
    requestAnimationFrame(() => {
      formRegion.current?.scrollIntoView({ block: 'start', behavior: 'smooth' });
      const form = chosenKind === 'answers' ? answerForm.current : applicationForm.current;
      const field = form?.querySelector('textarea:not([disabled]),input:not([type="radio"]):not([aria-hidden="true"]):not([disabled]),[role="combobox"]:not([aria-disabled="true"])');
      (field || form?.querySelector('button:not([disabled])'))?.focus();
    });
  };
  const runCurrent = () => { setCurrentForm(true); setKind('application'); focusForm('application'); };
  return <Stack spacing={3}>
    <Box ref={resultRegion} tabIndex={-1} role="region" aria-label="Latest result">
      <Section title={<Typography component="span" variant="m3">Latest result</Typography>}>
        {state === 'loading' ? <ContentSkeleton variant="panel" label="Loading test results" /> : focus ? focus.type === 'answers' ? renderAnswer?.(focus.id) : <RunDetail key={focus.id} contextId={contextId} evidenceId={focus.id} canWrite={canWrite} target={target} selection={selection} cases={cases} onReady={setPacketReady} onRunCurrent={runCurrent} onEarlier={setEarlier} currentForm={currentForm} /> : runs.isLoading || answerHistory?.isLoading ? <ContentSkeleton variant="panel" label="Loading test results" /> : runs.isError || answerHistory?.isError ? <ErrorState title="Couldn't load latest results." onRetry={() => { runs.refetch(); answerHistory?.refetch(); }} /> : <EmptyState sx={{ py: 3 }} title="No tests yet" description="Choose an answer check or verify reviewed cases on your application. Neither changes production." action={firstRun ? <Button variant="contained" onClick={focusForm}>Run your first test</Button> : undefined} />}
      </Section>
    </Box>
    <Box ref={formRegion}>
      <Section title={<Typography component="span" variant="m3">Run a test</Typography>}>
        <Stack spacing={2}>
          <FormControl><FormLabel id="test-kind-label">Choose what to test</FormLabel>
            <RadioGroup aria-labelledby="test-kind-label" value={kind} onChange={(event) => { setKind(event.target.value); setStartedForm(true); }} sx={{ flexDirection: { xs: 'column', sm: 'row' }, gap: 1.5 }}>
              <FormControlLabel value="answers" control={<Radio size="small" />} label={<Stack><Typography variant="s1">Check answers with this context</Typography><Typography variant="s3" color="text.secondary">Compare answers using your instructions and linked skills.</Typography></Stack>} />
              <FormControlLabel value="application" control={<Radio size="small" />} label={<Stack><Typography variant="s1">Verify on the application</Typography><Typography variant="s3" color="text.secondary">Check skill selection, delivered files, actions and answers.</Typography></Stack>} />
            </RadioGroup>
          </FormControl>
          <Box ref={answerForm} hidden={kind !== 'answers'}>{typeof children === 'function' ? children(!firstRun && !packetReady && (!earlier || currentForm) && kind === 'answers') : children}</Box>
          <Box ref={applicationForm} hidden={kind !== 'application'}><Stack spacing={2}>
            {state === 'loading' || (targets.isLoading && !targets.data) ? <ContentSkeleton variant="panel" label="Loading application target" /> : state === 'error' || targets.isError ? <ErrorState title="Couldn't load application test options." onRetry={targets.refetch} sx={{ alignItems: 'flex-start', textAlign: 'left', py: 1.5, px: 0 }} /> : state === 'off' || !targets.data?.runtime.available ? <CapabilityNotice title="Application verification is off" description="This workspace has no hosted application runtime. You can still check answers with the whole context; text results don't prove an application repair." /> : <>
              <TargetSection contextId={contextId} version={viewed} canWrite={canWrite} targets={targets} />
              <PlanSection contextId={contextId} versions={versions} viewed={viewed} cases={cases} target={target} canWrite={canWrite} onStarted={(id) => setSelected({ type: 'application', id })} onSelectionChange={setSelection} runs={runs.data} productionVersion={productionVersion} seed={seed} primary={!firstRun && (!packetReady || earlier) && (!earlier || currentForm) && kind === 'application'} />
            </>}
          </Stack></Box>
        </Stack>
      </Section>
    </Box>
    {reviewedCases}
    <Section title={<Typography component="span" variant="m3">History</Typography>} description="Both kinds of test, newest first. Open a run to inspect the version it tested.">
      <TextField select size="small" label="Test type" value={historyFilter} onChange={(event) => setHistoryFilter(event.target.value)} sx={{ minWidth: 180, mb: 1.5 }}><MenuItem value="all">All tests</MenuItem><MenuItem value="answers">Answer checks</MenuItem><MenuItem value="application">Application verifications</MenuItem></TextField>
      {runs.isError && <ErrorState title="Couldn't load application test history." onRetry={runs.refetch} />}
      {answerHistory?.isError && <ErrorState title="Couldn't load answer-check history." onRetry={answerHistory.refetch} />}
      {runs.isLoading || answerHistory?.isLoading ? <ContentSkeleton variant="rows" /> : visibleHistory.length && state !== 'empty' ? <TableContainer><Table size="small" aria-label="Test history"><TableHead><TableRow><TableCell>Type</TableCell><TableCell>Version</TableCell><TableCell>Status</TableCell><TableCell>Started</TableCell><TableCell /></TableRow></TableHead><TableBody>{visibleHistory.map((run) => <TableRow key={`${run.type}/${run.id}`} hover sx={{ height: ROW_HEIGHT.default }}>
        <TableCell>{run.type === 'application' ? 'Application verification' : 'Answer check'}</TableCell>
        <TableCell>{run.type === 'application' ? `v${run.arms.find((arm) => arm.variant === 'candidate')?.version ?? '?'}` : run.candidate?.version ? `v${run.candidate.version}` : 'Saved draft'}</TableCell>
        <TableCell><StatusBadge status={run.jobState || run.status} /></TableCell><TableCell>{localTime(run.createdAt)}</TableCell>
        <TableCell><Button variant="outlined" size="small" onClick={() => setSelected({ type: run.type, id: run.id })}>Inspect run</Button></TableCell>
      </TableRow>)}</TableBody></Table></TableContainer> : historyFilter !== 'all' ? <EmptyState variant="no-results" title="No matching test runs" description="Try the other test type or show all runs." onClearFilters={() => setHistoryFilter('all')} /> : <Typography component="div" variant="s3" color="text.secondary">Your test runs will appear here.</Typography>}
      {historyPagination}
    </Section>
  </Stack>;
}
ContextNativePanel.propTypes = { contextId: PropTypes.string.isRequired, versions: PropTypes.array.isRequired, viewed: PropTypes.object, cases: PropTypes.array.isRequired, canWrite: PropTypes.bool.isRequired, productionVersion: PropTypes.number, seed: PropTypes.object, children: PropTypes.oneOfType([PropTypes.node, PropTypes.func]), reviewedCases: PropTypes.node, state: PropTypes.string, answerHistory: PropTypes.object, renderAnswer: PropTypes.func, answerRunId: PropTypes.string, historyPagination: PropTypes.node, latestAnswer: PropTypes.object };
