import { useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import { v4 as uuid } from 'uuid';
import {
  Alert, Box, Button, Checkbox, Drawer, FormControlLabel, MenuItem, Stack, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, TextField, Typography,
} from '@mui/material';
import {
  useCreateNativeRun, useCreateNativeTarget, useNativeRun, useNativeRuns, useNativeTargets, usePreviewNativeRun,
  usePrepareReleasePacket, useQualifyNativeTarget, useRuntimeConfiguration,
} from 'src/api/skills/context-native';
import { useAuthContext } from 'src/auth/hooks';
import { useAppendEvidenceAnnotation, useEvidenceAnnotations, useEvidenceReference } from 'src/api/skills/evidence-annotations';
import ClosableDialog from 'src/components/custom-dialog/closable-dialog';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';

// Intent: a responder proves a repair on the real application, not on text.
// Hierarchy: target identity, then the reviewed plan and its paid ceiling, then the
// matched result with a ship/hold decision, then per-cell observation and disagreement.
// Palette/depth/surfaces: neutral flat tables, status roles only for outcomes.
// Typography/spacing: m3 headings, s1 values, s3 secondary; 12px groups.
const ARM = { candidate: 'Candidate', reference: 'Recorded reference', production: 'Production' };
const FAMILIES = [['selection', 'Skill selected'], ['delivery', 'File delivered'], ['tools', 'Business tool'], ['answer', 'Answer']];
const short = (digest) => (digest || '').replace('sha256:', '').slice(0, 12);
const VERDICTS = [['agree', 'Agree with the automatic outcome'], ['disagree', 'Disagree with the automatic outcome'], ['cannot_assess', 'Cannot assess']];

function Verdict({ value }) {
  return value ? <StatusBadge status={value === 'pass' ? 'passed' : value === 'fail' ? 'failed' : 'unknown'} /> : <Typography variant="s3" color="text.secondary">—</Typography>;
}
Verdict.propTypes = { value: PropTypes.string };

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
  return <Section title="Application target" description={`Runtime: ${targets.data.runtime.runtime} · evidence class: ${targets.data.runtime.evidenceClass || 'unstated'}`}>
    {!target ? <Stack direction="row" spacing={1.5} alignItems="center">
      <Typography variant="s1">No application is registered for this context.</Typography>
      <Button size="small" variant="outlined" disabled={!canWrite || !version || create.isPending} onClick={() => create.mutate({ version: version.number })}>Add application target</Button>
    </Stack> : <Stack direction="row" spacing={1.5} alignItems="center">
      <Typography variant="s1">{target.name}</Typography>
      <StatusBadge status={target.qualified ? 'passed' : 'unknown'} label={target.qualified ? 'Configuration pinned' : 'Configuration not pinned'} />
      {target.qualified && <Typography variant="s3" color="text.secondary">Pinned {short(target.configurationDigest)}</Typography>}
      {!target.qualified && <Button size="small" variant="outlined" disabled={!canWrite} onClick={() => setReview(target)}>Review runtime configuration</Button>}
    </Stack>}
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
  const annotations = useEvidenceAnnotations('simulation_native', run.id, 1);
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
  return <Drawer anchor="right" open onClose={onClose} PaperProps={{ sx: { width: 460, maxWidth: '100vw', p: 3 } }}>
    <Stack spacing={1.5}>
      <Typography variant="m1">{caseTitle}</Typography>
      <Typography variant="s3" color="text.secondary">{ARM[cell.variant]} · repeat {cell.repeatIndex + 1}</Typography>
      <Section title="Observed by the application">
        {seen ? <Stack spacing={0.5}>
          <Typography variant="s1">Skills opened: {seen.selected.length ? seen.selected.join(', ') : 'none'}</Typography>
          <Typography variant="s1">Business actions: {seen.actions.length ? seen.actions.map((a) => `${a.action} (${a.status})`).join(', ') : 'none attempted'}</Typography>
          <Typography variant="s1" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>Answer: {seen.answer || '—'}</Typography>
          <Typography variant="s3" color="text.secondary">Context {short(seen.context_digest)} · session {short(seen.session_id)}</Typography>
        </Stack> : <Typography variant="s1">This cell did not return an observation.</Typography>}
      </Section>
      <Section title="Automatic outcomes">
        <Stack spacing={0.5}>{FAMILIES.map(([key, label]) => <Stack key={key} direction="row" justifyContent="space-between"><Typography variant="s1">{label}</Typography><Verdict value={cell[key]} /></Stack>)}</Stack>
      </Section>
      <Section title="Human review" description="Appended beside the automatic grade; it never replaces it.">
        {mine.map((row) => <Typography key={row.id} variant="s1">{row.verdict.replace('_', ' ')}: {row.comment || 'no comment'}</Typography>)}
        <Stack spacing={1} sx={{ mt: 1 }}>
          <TextField select size="small" label="Your assessment" value={verdict} onChange={(event) => setVerdict(event.target.value)} disabled={!canWrite}>
            {VERDICTS.map(([value, label]) => <MenuItem key={value} value={value}>{label}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Why" value={comment} onChange={(event) => setComment(event.target.value)} multiline minRows={2} disabled={!canWrite} />
          {error && <Alert severity="error">{error}</Alert>}
          <Button variant="outlined" disabled={!canWrite || !verdict || !reference.data || append.isPending} onClick={submit}>Record review</Button>
        </Stack>
      </Section>
      <Button onClick={onClose}>Close</Button>
    </Stack>
  </Drawer>;
}
CellDrawer.propTypes = { run: PropTypes.object.isRequired, cell: PropTypes.object.isRequired, caseTitle: PropTypes.string, onClose: PropTypes.func.isRequired, canWrite: PropTypes.bool.isRequired };

function ReleasePacket({ contextId, run, target, canWrite }) {
  const { user } = useAuthContext();
  const prepare = usePrepareReleasePacket(contextId);
  const [pipeline, setPipeline] = useState({ url: '', configurationIdentity: '', decision: '' });
  const set = (key) => (event) => setPipeline({ ...pipeline, [key]: event.target.value });
  const valid = pipeline.url.trim() && pipeline.configurationIdentity.trim() && pipeline.decision.trim();
  return <Section title="Release packet" description="Prepares the exact version, runtime target and shared evidence for the separate release review. Nothing is released here.">
    <Stack spacing={1.5} sx={{ maxWidth: 560 }}>
      <TextField size="small" label="Retained pipeline run URL" value={pipeline.url} onChange={set('url')} />
      <TextField size="small" label="Pipeline configuration identity" value={pipeline.configurationIdentity} onChange={set('configurationIdentity')} />
      <TextField size="small" label="Pipeline decision" value={pipeline.decision} onChange={set('decision')} />
      {prepare.isError && <Alert severity="error">{prepare.error?.response?.data?.error?.message || 'The packet could not be prepared. Check the shared evidence, target and pipeline record.'}</Alert>}
      {prepare.data && <Alert severity="success">Packet ready · v{prepare.data.candidateVersion} · {prepare.data.evidence} shared evidence · target {short(prepare.data.targetDigest)} · {short(prepare.data.fingerprint)}</Alert>}
      <Box><Button variant="outlined" size="small" disabled={!canWrite || !target || !valid || prepare.isPending} onClick={() => prepare.mutate({ run, target, label: 'production', pipeline, userId: user?.id })}>Share evidence and prepare packet</Button></Box>
    </Stack>
  </Section>;
}
ReleasePacket.propTypes = { contextId: PropTypes.string.isRequired, run: PropTypes.object.isRequired, target: PropTypes.object, canWrite: PropTypes.bool.isRequired };

function RunDetail({ contextId, evidenceId, canWrite, target }) {
  const query = useNativeRun(contextId, evidenceId);
  const [open, setOpen] = useState(null);
  if (query.isLoading) return <ContentSkeleton label="Loading verification result" />;
  if (query.isError) return <ErrorState title="Could not load this verification" onRetry={query.refetch} />;
  const run = query.data;
  const titles = Object.fromEntries((run.cases || []).map((item) => [item.id, item.title]));
  const active = run.status === 'running';
  return <Stack spacing={1.5}>
    <Stack direction="row" spacing={1.5} alignItems="center">
      <Typography variant="m3">Verification {short(run.executionFingerprint)}</Typography>
      <StatusBadge status={active ? 'running' : run.status === 'completed' ? 'completed' : 'failed'} />
      <Typography variant="s3" color="text.secondary">{run.received} of {run.planned} cells returned</Typography>
    </Stack>
    {run.failure && <Alert severity="error">The run stopped: {run.failure.replaceAll('_', ' ')}. No outcome is inferred.</Alert>}
    {run.decision && !active && <Alert severity={run.decision.state === 'release_ready' ? 'success' : 'warning'}>
      <Typography variant="s1" component="div">{run.decision.state === 'release_ready' ? 'Release-ready on the application' : 'Hold this release'}</Typography>
      {run.decision.reasons.map((reason) => <Typography key={reason} variant="s1" component="div">{reason}</Typography>)}
    </Alert>}
    {run.result && !active && <Typography variant="s1">Historical failure: {run.result.reproduction.replaceAll('_', ' ')} · Repair {run.result.repair_verified ? 'verified' : 'not verified'}{run.result.regressed_cases?.length ? ` · ${run.result.regressed_cases.length} neighboring case(s) regressed` : ''}</Typography>}
    {run.runtime && <Typography variant="s3" color="text.secondary">Runtime proved read-only source {String(run.runtime.read_only_source)}, read-only context {String(run.runtime.read_only_context)}, closed paid egress {String(run.runtime.controlled_paid_egress)}.</Typography>}
    {run.cells && <TableContainer>
      <Table size="small" aria-label="Verification cells"><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
        <TableCell>Example</TableCell><TableCell>Context</TableCell><TableCell>Run</TableCell>{FAMILIES.map(([key, label]) => <TableCell key={key}>{label}</TableCell>)}
      </TableRow></TableHead><TableBody>{run.cells.map((cell) => <TableRow key={`${cell.caseId}/${cell.variant}/${cell.repeatIndex}`} hover sx={{ height: ROW_HEIGHT.default, cursor: cell.attemptId ? 'pointer' : 'default' }} onClick={() => cell.attemptId && setOpen(cell)}>
        <TableCell>{titles[cell.caseId] || cell.caseId}</TableCell><TableCell>{ARM[cell.variant]}</TableCell><TableCell>{cell.repeatIndex + 1}</TableCell>
        {FAMILIES.map(([key]) => <TableCell key={key}><Verdict value={cell[key]} /></TableCell>)}
      </TableRow>)}</TableBody></Table>
    </TableContainer>}
    {run.decision?.state === 'release_ready' && !active && <ReleasePacket contextId={contextId} run={run} target={target} canWrite={canWrite} />}
    {open && <CellDrawer run={run} cell={open} caseTitle={titles[open.caseId]} canWrite={canWrite} onClose={() => setOpen(null)} />}
  </Stack>;
}
RunDetail.propTypes = { contextId: PropTypes.string.isRequired, evidenceId: PropTypes.string.isRequired, canWrite: PropTypes.bool.isRequired, target: PropTypes.object };

function PlanSection({ contextId, versions, viewed, cases, target, canWrite, onStarted }) {
  const preview = usePreviewNativeRun(contextId);
  const create = useCreateNativeRun(contextId);
  const reviewed = cases.filter((item) => item.reviewed);
  const numbers = versions.map((item) => item.number);
  const [candidate, setCandidate] = useState(viewed?.number ?? numbers[0] ?? '');
  const [reference, setReference] = useState('');
  const [production, setProduction] = useState('');
  const [repetitions, setRepetitions] = useState(1);
  const [chosen, setChosen] = useState({});
  const [historical, setHistorical] = useState({});
  const [ack, setAck] = useState(false);
  const [key, setKey] = useState(uuid);
  const [error, setError] = useState('');
  const caseIds = reviewed.filter((item) => chosen[item.id]).map((item) => item.id);
  const baselines = { ...(reference ? { reference: Number(reference) } : {}), ...(production ? { production: Number(production) } : {}) };
  const plan = useMemo(() => ({
    bindingId: target?.bindingId, bindingRevision: target?.revision, candidateVersion: Number(candidate), baselines, caseIds,
    repetitions, historicalCaseIds: caseIds.filter((id) => historical[id]),
  }), [target, candidate, reference, production, caseIds.join(), repetitions, historical]); // eslint-disable-line react-hooks/exhaustive-deps
  const ready = target?.qualified && candidate && Object.keys(baselines).length > 0 && caseIds.length > 0;
  const result = preview.data;
  const run = async () => {
    setError('');
    try { onStarted(await create.mutateAsync({ idempotencyKey: key, plan, executionFingerprint: result.executionFingerprint, acknowledgeSpend: ack })); setKey(uuid()); setAck(false); preview.reset(); }
    catch (reason) { setError(reason?.response?.data?.error?.message || 'The verification did not start. Preview it again before retrying.'); }
  };
  const versionOptions = numbers.map((number) => <MenuItem key={number} value={number}>v{number}</MenuItem>);
  return <Section title="Matched application run" description="Every selected example runs on the same pinned application with each context version.">
    <Stack spacing={1.5}>
      <Stack direction={{ xs: 'column', md: 'row' }} spacing={1.5}>
        <TextField select size="small" label="Candidate version" value={candidate} onChange={(event) => setCandidate(event.target.value)} sx={{ minWidth: 170 }}>{versionOptions}</TextField>
        <TextField select size="small" label="Recorded reference version" value={reference} onChange={(event) => setReference(event.target.value)} sx={{ minWidth: 210 }}><MenuItem value="">None</MenuItem>{versionOptions}</TextField>
        <TextField select size="small" label="Production version" value={production} onChange={(event) => setProduction(event.target.value)} sx={{ minWidth: 170 }}><MenuItem value="">None</MenuItem>{versionOptions}</TextField>
        <TextField select size="small" label="Repetitions" value={repetitions} onChange={(event) => setRepetitions(Number(event.target.value))} sx={{ minWidth: 130 }}><MenuItem value={1}>1 each</MenuItem><MenuItem value={3}>3 each</MenuItem></TextField>
      </Stack>
      <Box role="group" aria-label="Examples to run">
        {reviewed.length ? reviewed.map((item) => <Stack key={item.id} direction="row" spacing={2} alignItems="center">
          <FormControlLabel control={<Checkbox size="small" checked={Boolean(chosen[item.id])} onChange={(event) => setChosen({ ...chosen, [item.id]: event.target.checked })} />} label={<Typography variant="s1">{item.title}</Typography>} />
          <FormControlLabel control={<Checkbox size="small" disabled={!chosen[item.id]} checked={Boolean(historical[item.id])} onChange={(event) => setHistorical({ ...historical, [item.id]: event.target.checked })} />} label={<Typography variant="s3" color="text.secondary">Recorded failure to reproduce</Typography>} />
        </Stack>) : <Typography variant="s1">Review at least one example above before running it on the application.</Typography>}
      </Box>
      <Stack direction="row" spacing={1}>
        <Button variant="outlined" size="small" disabled={!ready || preview.isPending} onClick={() => { setAck(false); preview.mutate(plan); }}>Preview application run</Button>
      </Stack>
      {preview.isError && <Alert severity="error">{preview.error?.response?.data?.error?.message || 'Could not plan this run. Check the target, versions and reviewed examples.'}</Alert>}
      {result && <Stack spacing={1}>
        {!result.reproducible ? <Alert severity="warning">
          <Typography variant="s1" component="div">Not reproduced: required historical inputs are missing, so nothing can launch.</Typography>
          {result.missingInputs.map((item) => <Typography key={`${item.caseId}/${item.input}`} variant="s1" component="div">{(cases.find((c) => c.id === item.caseId) || {}).title || item.caseId}: {item.input}. {item.detail}</Typography>)}
        </Alert> : <Alert severity="info">{result.cells} cells · paid ceiling ${result.ceilingUsd} · {result.arms.map((arm) => `${ARM[arm.variant]} v${arm.version}`).join(' · ')}</Alert>}
        <FormControlLabel control={<Checkbox size="small" checked={ack} disabled={!result.reproducible} onChange={(event) => setAck(event.target.checked)} />} label={<Typography variant="s1">I accept up to ${result.ceilingUsd} in provider charges for this run.</Typography>} />
        {error && <Alert severity="error">{error}</Alert>}
        <Box><Button variant="contained" size="small" disabled={!canWrite || !result.reproducible || !ack || create.isPending} onClick={run}>Run on the application</Button></Box>
      </Stack>}
    </Stack>
  </Section>;
}
PlanSection.propTypes = { contextId: PropTypes.string.isRequired, versions: PropTypes.array.isRequired, viewed: PropTypes.object, cases: PropTypes.array.isRequired, target: PropTypes.object, canWrite: PropTypes.bool.isRequired, onStarted: PropTypes.func.isRequired };

export default function ContextNativePanel({ contextId, versions, viewed, cases, canWrite }) {
  const targets = useNativeTargets(contextId);
  const runs = useNativeRuns(contextId);
  const [selected, setSelected] = useState(null);
  if (targets.isLoading) return <ContentSkeleton label="Loading application verification" />;
  if (targets.isError) return <ErrorState title="Could not load application verification" onRetry={targets.refetch} />;
  if (!targets.data.runtime.available) {
    return <Alert severity="warning">Application verification unavailable. This deployment has no hosted application runtime. Captured text is not a reproduced historical failure or release-ready native evidence.</Alert>;
  }
  const target = targets.data.targets[0];
  const focus = selected || runs.data?.[0]?.id;
  return <Stack spacing={2}>
    <TargetSection contextId={contextId} version={viewed} canWrite={canWrite} targets={targets} />
    <PlanSection contextId={contextId} versions={versions} viewed={viewed} cases={cases} target={target} canWrite={canWrite} onStarted={setSelected} />
    <Section title="Application verifications">
      {runs.data?.length ? <Stack spacing={1.5}>
        <TextField select size="small" label="Verification" value={focus || ''} onChange={(event) => setSelected(event.target.value)} sx={{ maxWidth: 360 }}>
          {runs.data.map((run) => <MenuItem key={run.id} value={run.id}>{short(run.executionFingerprint)} · {run.status} · {new Date(run.createdAt).toLocaleString()}</MenuItem>)}
        </TextField>
        {focus && <RunDetail key={focus} contextId={contextId} evidenceId={focus} canWrite={canWrite} target={target} />}
      </Stack> : <Typography variant="s1">No application verification has run for this context.</Typography>}
    </Section>
  </Stack>;
}
ContextNativePanel.propTypes = { contextId: PropTypes.string.isRequired, versions: PropTypes.array.isRequired, viewed: PropTypes.object, cases: PropTypes.array.isRequired, canWrite: PropTypes.bool.isRequired };
