import { useEffect, useMemo, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Box, Button, FormControl, FormControlLabel, FormLabel, Radio, RadioGroup, MenuItem, Pagination, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography } from '@mui/material';
import { useSkillTestingOptions, useSkillV11Scope, isActiveSkillJob } from 'src/api/skills/testing';
import {
  useContextTestSuite, useSaveContextTestSuite,
  contextTestCandidate, usePreviewContextTest, useCreateContextTest, useContextTestRuns, useContextTestRun, useContextTestResults, useContextTestAttempt, useCancelContextTest,
} from 'src/api/skills/context-testing';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import IdentityChip from '../components/IdentityChip';
import VersionLabel from '../components/VersionLabel';
import TechnicalDetails from '../components/TechnicalDetails';
import TestCaseEditor from '../tests/TestCaseEditor';
import TestInventoryReview, { centsUp, providerName, sendSentence } from '../tests/TestInventoryReview';
import Acknowledgment, { useAcknowledgment } from '../components/Acknowledgment';
import { RowActionsCell } from '../components/RowActions';
import { rowLinkProps, stackedTableSx } from '../components/responsiveTable';
import { requestReleaseFor, TEST_CASE_APPROVED, TEST_CASE_NEEDS_APPROVAL } from '../utils/terms';
import { AuthoringError, useAuthoringOperation } from '../editor/HotfixHeadReview';
import ContextNativePanel, { InProductionAction } from './ContextNativePanel';
import ResultConclusionCard from '../tests/ResultConclusionCard';
import { inProductionHint, isInProduction } from '../tests/resultConclusion';
import When from '../components/When';
import { answerConclusion, passedAll } from '../utils/conclusions';
import { reviewComposerUrl } from '../utils/review-links';
import { paths } from 'src/routes/paths';

// The one Tests panel for an agent context, in the shared sub-tabs (Results, Run a test, Cases;
// tests/TestsSubTabs.jsx, UXF-04). Each part keeps its own loading, error and empty states so a
// failure in one never hides the other:
//   - Regression cases and the application test: retained, reviewed examples captured from
//     Observe, plus the pinned application target, matched native run, per-cell grades,
//     ship/hold decision and human disagreement (ContextNativePanel).
//   - Answer checks: the frozen standing root plus accepted linked skills, run privately
//     against an exact saved draft or published release.
// Intent: incident responders inspect retained cases without mistaking a text answer for a
// repaired application; authors test the standing root plus accepted dependencies, not a leaf.
// Hierarchy: viewed release and native limitation, reviewed requests, then the answer check.
// Palette/depth/surfaces: neutral, flat sections and tables; status roles only for outcomes.
// Typography/spacing: m3/s1/s3, 12px groups and standard dense table rows.
// Reject a single green run badge as a release verdict, editable identity JSON,
// and automatically accepted provider consent. Each preview owns a fresh tick (UXF-03: one send tick
// bound to the preview fingerprint; a saved, approved test case asks no second tick).
// Last-used test settings are a per-viewer convenience: a person testing several contexts
// in a row keeps the same request, model, ceiling and reference. Nothing is pre-attested.
// Keyed by the organization/workspace/user identity so one login never prefills another's request text.
const lastTestKey = (identity) => `skills.contextTest.last:${identity}`;
const readLastTest = (identity) => { try { const value = JSON.parse(window.localStorage.getItem(lastTestKey(identity)) || '{}'); return value && typeof value === 'object' ? value : {}; } catch { return {}; } };
const writeLastTest = (identity, value) => { try { window.localStorage.setItem(lastTestKey(identity), JSON.stringify(value)); } catch { /* Storage is optional. */ } };
const emptyReview = () => ({ fingerprint: '', reviewedAllFiles: false, noKnownAnswerKeys: false, classifications: [], falsePositiveSecretFindings: [] });


// Saved cases an answer check can use as they are: approved, with at least one check (the server
// refuses a saved case without either).
const usableCase = (item) => Boolean(item.reviewed && item.checks?.length);

function RegressionCases({ contextId, version, versions, canWrite, skills, productionVersion, seed, children, state, answerHistory, renderAnswer, answerRunId, historyPagination, latestAnswer, draftHandoff }) {
  const suite = useContextTestSuite(contextId);
  const save = useSaveContextTestSuite(contextId);
  const [editor, setEditor] = useState(null);
  const [error, setError] = useState('');
  if (!suite.isLoading && !suite.isError && !suite.data) return <EmptyState title="Select an authenticated workspace" description="Test cases stay scoped to their context and workspace." />;
  const cases = suite.data?.cases || [];
  const changed = editor && editor.revision !== suite.data?.revision;
  const open = (item) => { setError(''); setEditor({ value: item, revision: suite.data.revision, cases: suite.data.cases }); };
  const savedCases = cases.filter(usableCase);
  const answerForm = typeof children === 'function' ? (primary, onEngage) => children(primary, onEngage, { savedCases, suiteRevision: suite.data?.revision ?? null }) : children;
  const casesSection = suite.isLoading ? <ContentSkeleton variant="panel" label="Loading test cases" />
    : suite.isError ? <ErrorState title="Couldn't load test cases" onRetry={suite.refetch} />
      : <Section title={<Typography component="span" variant="m3">Test cases</Typography>} description={<Typography component="span" variant="s3">To turn a failure into a test case, open the trace in Tracing and choose Save as test case.</Typography>}
        actions={<Button size="small" variant="outlined" component={RouterLink} to={paths.dashboard.observe} disabled={!canWrite}>Add test case from a trace</Button>}>
        {!cases.length ? <EmptyState title="No test cases yet" description="Save one from a trace, then approve its redaction and expected behavior. Cases that already work belong here too." /> : <TableContainer>
          <Table size="small" aria-label="Context regression cases" sx={stackedTableSx}><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Test case</TableCell><TableCell>Approval</TableCell><TableCell>Checks</TableCell><TableCell aria-label="Actions" /></TableRow></TableHead>
            <TableBody>{cases.map((item) => {
              const primary = { label: !item.checks.length ? 'Add checks' : item.reviewed ? 'Open test case' : 'Approve test case', onClick: () => open(item), disabled: !canWrite, reason: 'Only a workspace author can edit test cases.' };
              const link = rowLinkProps(primary);
              return <TableRow key={item.id} {...link} sx={[...(link.sx ? [link.sx] : []), { height: ROW_HEIGHT.default }]}>
                <TableCell><Stack spacing={0.5}><Typography component="div" variant="s1">{item.title}</Typography><Typography component="div" variant="s3" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.prompt}</Typography></Stack></TableCell>
                <TableCell>{item.reviewed ? TEST_CASE_APPROVED : TEST_CASE_NEEDS_APPROVAL}</TableCell><TableCell>{item.checks.length ? item.checks.length : <StatusBadge status="warning" label="No checks yet" />}</TableCell>
                <RowActionsCell name={item.title} primary={primary} />
              </TableRow>;
            })}</TableBody></Table>
        </TableContainer>}
      </Section>;
  return <Stack spacing={1.5}>
    <ContextNativePanel contextId={contextId} versions={versions} viewed={version} cases={cases} casesState={suite.isLoading ? 'loading' : suite.isError ? 'error' : undefined} onRetryCases={suite.refetch} canWrite={canWrite} productionVersion={productionVersion} seed={seed} state={state} answerHistory={answerHistory} renderAnswer={renderAnswer} answerRunId={answerRunId} historyPagination={historyPagination} latestAnswer={latestAnswer} draftHandoff={draftHandoff} reviewedCases={casesSection}>{answerForm}</ContextNativePanel>
    {editor && <TestCaseEditor open subjectKind="agent_context" initialCase={editor.value} applicationBehavior={{ skills }} canSave={canWrite && !changed} isSaving={save.isPending}
      error={changed ? 'The test cases changed. Close and reopen this case after checking the latest ones; your edits were not submitted.' : error}
      onClose={() => { if (!save.isPending) setEditor(null); }}
      onSave={async (value) => {
        if (changed) return;
        try {
          await save.mutateAsync({ revision: editor.revision, cases: editor.cases.map((item) => item.id === value.id ? value : item) });
          setEditor(null);
        } catch { setError('Could not save this test case. Your edits are preserved; retry after checking your access and the latest test cases.'); }
      }} />}
  </Stack>;
}
RegressionCases.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object, versions: PropTypes.array.isRequired, skills: PropTypes.array.isRequired, canWrite: PropTypes.bool.isRequired, productionVersion: PropTypes.number, seed: PropTypes.object, children: PropTypes.oneOfType([PropTypes.node, PropTypes.func]), state: PropTypes.string, answerHistory: PropTypes.object, renderAnswer: PropTypes.func, answerRunId: PropTypes.string, historyPagination: PropTypes.node, latestAnswer: PropTypes.object, draftHandoff: PropTypes.bool };

// Which exact candidate the answer check tests: the saved draft or the viewed release.
// It changes only the answer-check candidate; the application test keeps the viewed release.
function CandidateSource({ source }) {
  return <FormControl>
    <FormLabel id="context-test-source" sx={{ color: 'text.primary' }}>Test which version</FormLabel>
    <RadioGroup row aria-labelledby="context-test-source" value={source.testingDraft ? 'draft' : 'version'} onChange={(event) => source.onSelect(event.target.value === 'draft')}>
      {source.draftAvailable ? <FormControlLabel value="draft" control={<Radio size="small" />} label="Saved draft" />
        : <Tooltip title="No saved draft for this context" describeChild><span><FormControlLabel value="draft" disabled control={<Radio size="small" />} label="Saved draft" /></span></Tooltip>}
      <FormControlLabel value="version" disabled={!source.releaseAvailable} control={<Radio size="small" />} label="Published version" />
    </RadioGroup>
  </FormControl>;
}
CandidateSource.propTypes = { source: PropTypes.object.isRequired };

// In the editor the draft may be unsaved, so the answer check saves it first and opens
// the exact saved revision on the context's Tests tab. Opening tests never publishes.
function SavedDraftHandoff({ onOpen, disabled }) {
  return <Stack spacing={1.5}>
    <Alert severity="info">Checks answers from your saved instructions and linked skills together. Opening the test doesn’t publish or move production.</Alert>
    <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} disabled={disabled} onClick={onOpen}>Save and open answer check</Button>
  </Stack>;
}
SavedDraftHandoff.propTypes = { onOpen: PropTypes.func.isRequired, disabled: PropTypes.bool };

// The ad hoc answer check names its one case "Reviewed context regression" in the request payload;
// on screen the result's heading carries it instead ("Answer check · v2 vs v1"). The payload is unchanged.
const AD_HOC_TITLE = 'Reviewed context regression';
// Every grade goes through the shared status vocabulary; a raw server value never reaches the page.
// A failed answer is red on every row, the baseline included, so "Answer failed" reads the same everywhere;
// a baseline failure that the tested version fixes says "expected" beside it. A baseline pass stays quiet.
const ANSWER_STATUS = { pass: ['passed', 'Answer passed'], fail: ['failed', 'Answer failed'], not_graded: ['not_graded', 'Not graded'] };
const WAITING = ['queued', 'pending', 'not_started'];
// One mark per attempt. While the run is live, an attempt with no grade yet reads as its own state
// (not started, or running), never "Not graded"; "Not graded" is said only once the run has finished.
function attemptBadge(item, running) {
  if (ANSWER_STATUS[item.answer_verdict] && item.answer_verdict !== 'not_graded') return ANSWER_STATUS[item.answer_verdict];
  if (running || isActiveSkillJob(item.status) || WAITING.includes(item.status)) return isActiveSkillJob(item.status) && !WAITING.includes(item.status) ? [item.status, undefined] : ['not_started', undefined];
  if (item.answer_verdict === 'not_graded') return ANSWER_STATUS.not_graded;
  return item.status && item.status !== 'completed' ? [item.status, undefined] : ['unknown', 'Not reported'];
}
const BASELINE_NAMES = { without_target: 'Without the context', without_context: 'Without the context', production: 'Production' };
function Results({ contextId, runId, contextName, versions = [], canWrite, onRunApplication, onReady, secondary = false, hasTarget = false, applicationAvailable = false, productionVersion, newerDraft = false }) {
  const [page, setPage] = useState(1);
  const [attemptId, setAttemptId] = useState(null);
  const run = useContextTestRun(contextId, runId);
  const results = useContextTestResults(contextId, runId, page);
  const attempt = useContextTestAttempt(contextId, runId, attemptId);
  const cancel = useCancelContextTest(contextId, runId);
  const candidate = run.data?.candidate || {};
  const rows = results.data?.results || [];
  const running = isActiveSkillJob(run.data?.status);
  const complete = Boolean(results.data) && !(results.data.total_pages > 1);
  const passed = run.data?.status === 'completed' && complete && rows.length > 0 && rows.every((row) => passedAll(row.variants.find((variant) => variant.variant === 'candidate')));
  // A passing answer check on a published version offers the next step; a draft must be published first.
  // Readiness is read against current production: a pass for the version production already serves
  // confirms that release and offers no release request (the composer would refuse it).
  const inProduction = isInProduction(candidate.version, productionVersion);
  const releasable = passed && Boolean(candidate.version) && !inProduction;
  const confirmed = passed && Boolean(candidate.version) && inProduction;
  // While this result offers its next step as the page's primary, the run forms step back.
  useEffect(() => { onReady?.(releasable); return () => onReady?.(false); }, [onReady, releasable]);
  if (run.isLoading) return <ContentSkeleton />;
  if (run.isError) return <ErrorState title="Context run unavailable" onRetry={run.refetch} />;
  if (!run.data) return null;
  const version = candidate.version ? `v${candidate.version}` : 'the saved draft';
  const versionId = versions.find((item) => Number(item.number) === Number(candidate.version))?.id;
  const reference = run.data.reference?.version;
  const tested = candidate.version ? `v${candidate.version}` : 'Saved draft';
  const referenceIsProduction = reference != null && isInProduction(reference, productionVersion);
  const armLabel = (variant) => (variant === 'candidate' ? `${tested} (testing)` : variant === 'reference' && reference ? `v${reference}${referenceIsProduction ? ' (production)' : ''}` : BASELINE_NAMES[variant] || variant.replaceAll('_', ' '));
  const heading = `Answer check · ${tested}${reference ? ` vs v${reference}` : ''}`;
  const composer = canWrite ? reviewComposerUrl({ kind: 'agent_context', subjectId: contextId, name: contextName, candidateId: versionId, number: candidate.version, digest: candidate.digest, label: 'production', evidence: [{ source: 'skills_text', sourceId: runId }] }) : undefined;
  // With an application registered, proving the repair there is the stronger next step, so it leads;
  // the release request stays one click away. Without one, the release request leads.
  const verifyFirst = hasTarget && Boolean(onRunApplication);
  const lead = secondary ? 'outlined' : 'contained';
  const request = <Button key="request" color="primary" variant={verifyFirst ? 'outlined' : lead} disabled={!canWrite} href={composer}>{requestReleaseFor(candidate.version)}</Button>;
  const verify = verifyFirst && <Button key="verify" color="primary" variant={lead} onClick={onRunApplication}>Test v{candidate.version} on the application</Button>;
  const setUp = !hasTarget && applicationAvailable && onRunApplication && <Button key="setup" variant="outlined" onClick={onRunApplication}>Set up application test</Button>;
  const hint = canWrite ? 'This run stays private until you share it in the release request. An answer check alone doesn\u2019t prove an application repair.' : 'Only a workspace author can request a release.';
  return <Stack spacing={1.5}>
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }} justifyContent="space-between" sx={{ minWidth: 0 }}>
      <Typography component="h2" variant="m3">{heading}</Typography>
      {running && <Button size="small" variant="outlined" sx={{ flexShrink: 0 }} disabled={cancel.isPending} onClick={() => cancel.mutate({})}>Cancel context test</Button>}
    </Stack>
    <ResultConclusionCard sentence={answerConclusion({ status: run.data.status, subject: version, rows, complete })} sentenceProps={{ role: 'status' }}
      meta={<Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap><StatusBadge status={run.data.status} />{run.data.created_at && <Typography variant="s3" color="text.secondary"><When value={run.data.created_at} /></Typography>}</Stack>}
      badge={confirmed ? { status: 'released', role: 'neutral', label: 'Already in production' } : undefined}
      actions={releasable ? <>{verify || request}{verify ? request : setUp}</> : confirmed ? <InProductionAction contextId={contextId} newer={newerDraft || versions.some((item) => Number(item.number) > Number(candidate.version))} /> : undefined}
      hint={releasable ? <Typography variant="s3" color="text.secondary">{hint}</Typography> : confirmed ? <Typography variant="s3" color="text.secondary">{inProductionHint(candidate.version)}</Typography> : undefined}
      decided={<Stack spacing={1}>
        <IdentityChip value={candidate.digest} label="Version" /><IdentityChip value={runId} label="Run" kind="id" />
        {run.data.reference && <IdentityChip value={run.data.reference.digest} label="Reference" />}
        <Typography variant="s3">Instructions apply to every request, so skill routing is not measured here.</Typography>
      </Stack>} />
    <AuthoringError error={cancel.error} />
    {results.isLoading ? <ContentSkeleton /> : results.isError ? <ErrorState title="Case results unavailable" onRetry={results.refetch} /> : rows.map((row) => {
      const candidateRow = row.variants.find((variant) => variant.variant === 'candidate');
      const fixing = passedAll(candidateRow);
      return <Stack spacing={1} key={row.case_id} sx={{ py: 1, borderBottom: 1, borderColor: 'divider' }}>
        {row.title !== AD_HOC_TITLE && <Typography variant="s1" sx={{ fontWeight: 500 }}>{row.title}</Typography>}
        {row.variants.map((variant) => { const baseline = variant.variant !== 'candidate'; return <Stack key={variant.variant} direction={{ xs: 'column', sm: 'row' }} spacing={1.5} alignItems={{ xs: 'flex-start', sm: 'center' }}>
          <Typography variant="s1" sx={{ minWidth: 200 }}>{armLabel(variant.variant)}</Typography>
          {variant.attempts.map((item) => { const [status, label] = attemptBadge(item, running); return <Stack key={item.id} direction="row" spacing={1} alignItems="center" flexWrap="wrap">
            <StatusBadge status={status} label={label} role={baseline && item.answer_verdict === 'pass' ? 'neutral' : undefined} />
            {baseline && fixing && item.answer_verdict === 'fail' && <Typography variant="s3" color="text.secondary">expected</Typography>}
            <Button size="small" variant="text" color="inherit" aria-label={`Inspect ${armLabel(variant.variant)} attempt ${item.repeat_index + 1}`} onClick={() => setAttemptId(item.id)}>Inspect</Button>
          </Stack>; })}
        </Stack>; })}
      </Stack>;
    })}
    {results.data?.total_pages > 1 && <Pagination page={page} count={results.data.total_pages} onChange={(_, value) => { setPage(value); setAttemptId(null); }} />}
    {attemptId && (attempt.isLoading ? <ContentSkeleton /> : attempt.isError ? <ErrorState title="Attempt unavailable" onRetry={attempt.refetch} /> : attempt.data && <Stack spacing={1}>
      <Typography variant="m3">Retained attempt evidence</Typography>
      <Typography variant="s1">All instructions delivered to the model: {attempt.data.root_fully_delivered ? 'yes' : 'no'}</Typography>
      <TechnicalDetails title="Attempt details"><Typography variant="s3">Delivery receipts identify the bytes sent, not whether the model followed them. A linked skill that was never read is not counted as delivered.</Typography><Box component="pre" tabIndex={0} aria-label="Answer check attempt evidence" sx={{ m: 0, p: 1.5, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1, typography: 's1', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(attempt.data, null, 2)}</Box></TechnicalDetails>
    </Stack>)}
  </Stack>;
}
Results.propTypes = { secondary: PropTypes.bool, contextId: PropTypes.string.isRequired, runId: PropTypes.string.isRequired, contextName: PropTypes.string, versions: PropTypes.array, canWrite: PropTypes.bool, onRunApplication: PropTypes.func, onReady: PropTypes.func, hasTarget: PropTypes.bool, applicationAvailable: PropTypes.bool, productionVersion: PropTypes.number, newerDraft: PropTypes.bool };

const isLimit = (value) => /^\d+(\.\d{1,8})?$/.test(value) && Number(value) > 0;
function WholeContextTests({ contextName, contextId, candidate, scopeKey, identity, state, source, primary, onStarted, onEngage, versions = [], productionVersion, savedCases = [], suiteRevision = null }) {
  const [last] = useState(() => readLastTest(identity));
  const [prompt, setPrompt] = useState(typeof last.prompt === 'string' ? last.prompt : '');
  const [expected, setExpected] = useState(typeof last.expected === 'string' ? last.expected : '');
  const [chosenModel, setModel] = useState(typeof last.model === 'string' ? last.model : '');
  const [limit, setLimit] = useState(typeof last.limit === 'string' ? last.limit : '');
  const [reference, setReference] = useState(typeof last.reference === 'string' ? last.reference : '');
  // UXF-03: a saved test case was approved when it was saved (`reviewed`, bound to its revision), so it
  // asks no tick here. A new request keeps its own tick, bound to the exact request and answer text.
  const [caseMode, setCaseMode] = useState(null);
  const [chosenCase, setChosenCase] = useState('');
  // A saved case is the default whenever one can run; with none, a new request is the only choice.
  const mode = (caseMode ?? 'saved') === 'saved' && savedCases.length ? 'saved' : 'new';
  const savedCase = savedCases.find((item) => item.id === chosenCase) || savedCases[0] || null;
  const caseIdentity = prompt.trim() && expected.trim() ? JSON.stringify([prompt, expected]) : null;
  const caseAck = useAcknowledgment(caseIdentity);
  const [preview, setPreview] = useState(null);
  const [configuration, setConfiguration] = useState(null);
  // What the preview was built from; a different case, suite revision or request needs a new preview.
  const [configurationKey, setConfigurationKey] = useState(null);
  const [inventory, setInventory] = useState({ review: emptyReview(), complete: false });
  // Settings last used for this same context show as one line ("gpt-4o-mini · up to $2") behind Change.
  // A first test in a context (even with values carried over from another one) shows the fields, so the
  // model and ceiling are seen where they are first needed.
  const [editSettings, setEditSettings] = useState(() => !(last.contextId === contextId && typeof last.model === 'string' && last.model && typeof last.limit === 'string' && isLimit(last.limit)));
  const options = useSkillTestingOptions();
  const runnable = (options.data?.models || []).filter((item) => item.canRun);
  // Preselect the last-used model, else the first model this workspace can run; it stays visible and changeable.
  const model = runnable.some((item) => item.modelId === chosenModel) ? chosenModel : (runnable[0]?.modelId || '');
  const prepare = usePreviewContextTest(contextId);
  const create = useCreateContextTest(contextId);
  const operation = useAuthoringOperation(scopeKey);
  const locked = operation.pending || operation.uncertain || prepare.isPending;
  const invalidate = () => { setPreview(null); setConfigurationKey(null); setInventory({ review: emptyReview(), complete: false }); };
  const change = (setter) => (event) => { setter(event.target.value); invalidate(); onEngage?.(); };
  const validLimit = isLimit(limit);
  const showSettings = editSettings || !model || !validLimit;
  // The comparison is a published version other than the one under test, chosen from the list;
  // a remembered choice that no longer applies falls back, visibly, to "Without the context".
  const referenceChoices = versions.filter((item) => candidate?.kind !== 'version' || Number(item.number) !== Number(candidate.version));
  const comparison = reference && referenceChoices.some((item) => String(item.number) === reference) ? reference : '';
  const validReference = !comparison || (/^[1-9]\d*$/.test(comparison) && Number.isSafeInteger(Number(comparison)));
  const currentCandidate = candidate && configuration && JSON.stringify(candidate) === JSON.stringify(configuration.candidate);
  const selectionKey = mode === 'saved' ? JSON.stringify(['saved', suiteRevision, savedCase?.id ?? null]) : JSON.stringify(['new', prompt, expected]);
  const currentSelection = configurationKey === selectionKey;
  // A new suite revision (someone edited the saved cases) drops the preview built from the old one.
  useEffect(() => { if (mode === 'saved') { setPreview(null); setConfigurationKey(null); setInventory({ review: emptyReview(), complete: false }); } }, [suiteRevision, mode]);
  const caseReady = mode === 'saved' ? Boolean(savedCase && suiteRevision) : Boolean(prompt.trim() && expected.trim() && caseAck.acknowledged);
  const sendReady = Boolean(preview && inventory.complete && !inventory.diagnostic && inventory.review.fingerprint === preview.fingerprint);
  const fileCount = useMemo(() => (preview?.inventory || []).reduce((total, bundle) => total + bundle.files.length, 0), [preview]);
  const launch = async () => {
    if (!sendReady || !currentSelection) return;
    const result = await operation.run((input, extra) => create.mutateAsync({ ...input, ...extra }), { configuration, expectedFingerprint: preview.fingerprint, inventoryReview: inventory.review });
    if (result) { onStarted(result.id); invalidate(); }
  };
  const preflight = async () => {
    invalidate();
    const config = {
      harness: 'contexts-text-v1.2', candidate,
      // A saved case is sent by reference, exactly { kind, suite_revision, case_ids }; the server checks its approval.
      cases: mode === 'saved' ? { kind: 'saved', suite_revision: suiteRevision, case_ids: [savedCase.id] }
        : { kind: 'ad_hoc', cases: [{ id: crypto.randomUUID(), title: AD_HOC_TITLE, source_group_id: 'manual-context-test', prompt, fixtures: [], expected_trigger: null,
          checks: [{ id: crypto.randomUUID(), kind: 'contains', critical: true, config: { text: expected, case_sensitive: false } }], reviewed: caseAck.acknowledged }] },
      baselines: comparison ? [{ kind: 'version', version: Number(comparison) }] : [{ kind: 'without_context' }], competitors: [],
      model: { model_id: model, temperature: 0, max_output_tokens: 1024 }, judge: null,
      profile: 'quick', repetitions: 1, limits: { max_total_tokens: options.data.limits.profiles.quick.maxTotalTokens, max_estimated_cost_usd: limit }, purpose: 'regression', parent_run_id: null,
    };
    setConfiguration(config);
    setConfigurationKey(selectionKey);
    writeLastTest(identity, { prompt, expected, model, limit, reference: comparison, contextId });
    try { setPreview(await prepare.mutateAsync(config)); } catch { /* The mutation error is rendered below. */ }
  };
  if (options.isLoading || state === 'loading' || source?.isLoading) return <ContentSkeleton />;
  if (options.isError || state === 'error') return <ErrorState title="Couldn't load answer-check options." onRetry={options.refetch} sx={{ alignItems: 'flex-start', textAlign: 'left', py: 1.5, px: 0 }} />;
  if (source?.isError) return <ErrorState title="Saved draft unavailable" onRetry={source.onRetry} />;
  return <Stack spacing={2}>
    {!candidate ? <Alert severity="warning">Select a published version or load your saved draft before testing.</Alert> : candidate.kind === 'draft'
      ? <Stack direction="row" spacing={0.5} alignItems="center"><Typography variant="s1">{`Testing your saved draft (revision ${candidate.revision})`}</Typography><VersionLabel compact digest={candidate.digest} label="draft" /></Stack>
      : <Typography variant="s1">Testing <VersionLabel number={candidate.version} digest={candidate.digest} sx={{ typography: 's1', fontWeight: 600 }} /></Typography>}
    <Stack spacing={1.5}>
      <FormControl>
        <FormLabel id="context-test-case-mode" sx={{ color: 'text.primary' }}>Test case</FormLabel>
        <RadioGroup row aria-labelledby="context-test-case-mode" value={mode} onChange={(event) => { setCaseMode(event.target.value); invalidate(); onEngage?.(); }}>
          {savedCases.length ? <FormControlLabel value="saved" disabled={locked} control={<Radio size="small" />} label="Use a saved test case" />
            : <Tooltip title="No approved test case with checks yet. Add one in Cases." describeChild><span><FormControlLabel value="saved" disabled control={<Radio size="small" />} label="Use a saved test case" /></span></Tooltip>}
          <FormControlLabel value="new" disabled={locked} control={<Radio size="small" />} label="Write a new request" />
        </RadioGroup>
      </FormControl>
      {mode === 'saved' && savedCase ? <Stack spacing={1}>
        <TextField select size="small" label="Saved test case" value={savedCase.id} onChange={(event) => { setChosenCase(event.target.value); invalidate(); onEngage?.(); }} disabled={locked} sx={{ maxWidth: 480 }}>
          {savedCases.map((item) => <MenuItem key={item.id} value={item.id}>{item.title}</MenuItem>)}
        </TextField>
        <Typography variant="s3" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{savedCase.prompt}</Typography>
        <Stack direction="row" spacing={1} alignItems="center"><StatusBadge status="completed" label={TEST_CASE_APPROVED} /><Typography variant="s3" color="text.secondary">Approved when it was saved, so it needs no tick here.</Typography></Stack>
      </Stack> : <>
        <TextField size="small" label="Test request" multiline minRows={3} value={prompt} onChange={change(setPrompt)} disabled={locked} />
        <TextField size="small" label="Expected answer contains" value={expected} onChange={change(setExpected)} disabled={locked} helperText="The answer must contain this text." />
        <Acknowledgment acknowledgment={caseAck} disabled={locked} label="I checked this request and its expected answer." changedNote="The request or answer changed. Check it again." />
      </>}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
        {showSettings && <>
          <TextField select size="small" label="Task model" value={model} onChange={change(setModel)} disabled={locked} sx={{ minWidth: 200 }}>
            {(options.data?.models || []).map((item) => <MenuItem key={item.modelId} value={item.modelId} disabled={!item.canRun}>{item.modelId}{!item.canRun ? ` · ${item.disabledReason}` : ''}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Maximum estimated cost (USD)" value={limit} onChange={change(setLimit)} disabled={locked} />
        </>}
        <TextField select size="small" label="Compare with" value={comparison} onChange={change(setReference)} disabled={locked} sx={{ minWidth: 200 }} InputLabelProps={{ shrink: true }} SelectProps={{ displayEmpty: true }}>
          <MenuItem value="">Without the context</MenuItem>
          {referenceChoices.map((item) => <MenuItem key={item.number} value={String(item.number)}>v{item.number}{Number(productionVersion) === Number(item.number) ? ' (production)' : ''}</MenuItem>)}
        </TextField>
      </Stack>
      {!showSettings && <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="s3" color="text.secondary">{model} · up to ${limit}</Typography>
        <Button size="small" variant="text" aria-label="Change model and cost limit" disabled={locked} onClick={() => setEditSettings(true)}>Change</Button>
      </Stack>}
      <AuthoringError error={prepare.error || operation.error} />
      <Button color="primary" sx={{ alignSelf: 'flex-start' }} variant={primary && !preview ? "contained" : "outlined"} disabled={locked || !candidate || !caseReady || !options.data?.enabled || !options.data?.models.some((item) => item.modelId === model && item.canRun) || !validLimit || !validReference} onClick={preflight}>Preview answer check</Button>
      <Typography variant="s3" color="text.secondary">{locked ? 'Wait for the current operation to finish.' : !candidate ? 'Choose a saved draft or published version.' : !options.data?.enabled || !model ? 'A runnable model must be enabled for this workspace.' : mode === 'saved' && !caseReady ? 'Choose a saved test case.' : mode === 'new' && (!prompt.trim() || !expected.trim()) ? 'Add a request and the answer text to check.' : !caseReady ? 'Tick that you checked the request and answer to continue.' : !validLimit ? 'Enter a positive maximum cost.' : !validReference ? 'Choose a published version to compare with.' : 'Preview the files before starting the test.'}</Typography>
    </Stack>
    {preview && <Stack spacing={1.5}>
      <Typography variant="m3">Up to {preview.attemptCount} runs · at most {centsUp(preview.estimate.upperBoundUsd)}</Typography>
      <Typography variant="s3">Test runs are private and don’t change production. The task provider, {providerName(preview.providerDisclosure.taskProvider)}, receives the files and request below.</Typography>
      {(!currentCandidate || !currentSelection) && <Alert severity="warning">{!currentCandidate ? 'The saved version changed. Preview and check the new compiled context before running.' : 'The test case changed. Preview again before running.'}</Alert>}
      <TestInventoryReview subjectName={contextName} subjectKind="agent_context" subjectId={contextId} preview={preview} value={inventory.review} onChange={setInventory} disabled={locked || !currentCandidate || !currentSelection} consentHeading="Before this runs"
        sendLabel={sendSentence({ fileCount, what: mode === 'saved' ? 'the test case' : 'the request', provider: providerName(preview.providerDisclosure.taskProvider), upTo: centsUp(preview.estimate.upperBoundUsd) })} />
      {inventory.review.reviewedAllFiles && inventory.diagnostic && <Alert severity="warning">A flagged file is marked as evaluation material or overlaps the test case, so this answer check cannot run. Edit the context so it is no longer sent, then preview again.</Alert>}
      <Button color="primary" sx={{ alignSelf: 'flex-start' }} variant={primary ? "contained" : "outlined"} disabled={locked || !currentCandidate || !currentSelection || !sendReady} onClick={launch}>Run answer check</Button>
      {!sendReady && <Typography variant="s3" color="text.secondary">Classify any flagged files and check any secret findings, then tick to send this test.</Typography>}
    </Stack>}
    {operation.uncertain && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const receipt = await operation.retry(); if (receipt) { onStarted(receipt.id); invalidate(); } }}>Recover original context test</Button>}

  </Stack>;
}
WholeContextTests.propTypes = { savedCases: PropTypes.array, suiteRevision: PropTypes.number, onEngage: PropTypes.func, versions: PropTypes.array, productionVersion: PropTypes.number, contextName: PropTypes.string, primary: PropTypes.bool, onStarted: PropTypes.func.isRequired, contextId: PropTypes.string.isRequired, candidate: PropTypes.object, scopeKey: PropTypes.string.isRequired, identity: PropTypes.string.isRequired, state: PropTypes.string, source: PropTypes.object };

function Tests({ contextName, contextId, version, versions, canWrite, skills, productionVersion, seed, candidate, scopeKey, identity, state, source, onOpenSavedDraft, openSavedDraftDisabled }) {
  const [answerRunId, setAnswerRunId] = useState(null);
  const [historyPage, setHistoryPage] = useState(1);
  const history = useContextTestRuns(contextId, historyPage);
  const latestAnswers = useContextTestRuns(contextId, 1);
  return <Stack spacing={3}>
    <RegressionCases draftHandoff={Boolean(source?.testingDraft)} contextId={contextId} version={version} versions={versions} canWrite={canWrite} skills={skills} productionVersion={productionVersion} seed={seed} state={state} answerHistory={history} latestAnswer={latestAnswers.data?.results[0]} answerRunId={answerRunId} renderAnswer={(id, actions) => <Results key={id} contextId={contextId} runId={id} contextName={contextName} versions={versions} canWrite={canWrite} onRunApplication={actions?.onRunApplication} onReady={actions?.onReady} secondary={Boolean(actions?.secondary)} hasTarget={Boolean(actions?.hasTarget)} applicationAvailable={Boolean(actions?.applicationAvailable)} productionVersion={productionVersion} newerDraft={Boolean(source?.draftAvailable)} />} historyPagination={history.data?.total_pages > 1 ? <Pagination page={historyPage} count={history.data.total_pages} onChange={(_, value) => setHistoryPage(value)} /> : null}>
    {(primary, onEngage, suiteInfo) => <Stack spacing={2}>
      <Section title={<Typography component="span" variant="m3">Check answers</Typography>} />
      {source && !onOpenSavedDraft && <CandidateSource source={source} />}
      {onOpenSavedDraft ? <SavedDraftHandoff onOpen={onOpenSavedDraft} disabled={openSavedDraftDisabled} />
        : <WholeContextTests contextName={contextName} contextId={contextId} candidate={candidate} scopeKey={scopeKey} identity={identity} state={state} source={source} primary={primary} onEngage={onEngage} onStarted={setAnswerRunId} versions={versions} productionVersion={productionVersion} savedCases={suiteInfo?.savedCases} suiteRevision={suiteInfo?.suiteRevision} />}
    </Stack>}
    </RegressionCases>
  </Stack>;
}
Tests.propTypes = {
  contextName: PropTypes.string,
  contextId: PropTypes.string.isRequired, version: PropTypes.object, versions: PropTypes.array.isRequired, canWrite: PropTypes.bool.isRequired, skills: PropTypes.array.isRequired,
  productionVersion: PropTypes.number, seed: PropTypes.object, candidate: PropTypes.object, scopeKey: PropTypes.string.isRequired, identity: PropTypes.string.isRequired,
  state: PropTypes.string, source: PropTypes.object, onOpenSavedDraft: PropTypes.func, openSavedDraftDisabled: PropTypes.bool,
};

// version: the viewed release (the application test and the default answer-check candidate).
// draft: the saved draft to test instead, when source.testingDraft is set (or no source is given).
// source: the saved-draft/viewed-release switch for the answer-check candidate.
// onOpenSavedDraft: editor hand-off that saves the draft and opens these tests on the context page.
export default function ContextTestsPanel({ contextName, contextId, version, versions = [], draft, source, canWrite = false, skills = [], productionVersion, seed, state, onOpenSavedDraft, openSavedDraftDisabled = false }) {
  const { identity } = useSkillV11Scope();
  const scopeKey = `${identity}:${contextId}`;
  const testingDraft = source ? source.testingDraft : Boolean(draft);
  let candidate = !testingDraft && version ? { kind: 'version', version: version.number, digest: version.digest } : null;
  if (testingDraft && draft) { try { candidate = contextTestCandidate(draft); } catch { candidate = null; } }
  return <Tests contextName={contextName} key={scopeKey} scopeKey={scopeKey} identity={identity} contextId={contextId} candidate={candidate} state={state} source={source}
    version={version} versions={versions} canWrite={canWrite} skills={skills} productionVersion={productionVersion} seed={seed}
    onOpenSavedDraft={onOpenSavedDraft} openSavedDraftDisabled={openSavedDraftDisabled} />;
}
ContextTestsPanel.propTypes = {
  contextName: PropTypes.string,
  contextId: PropTypes.string.isRequired, version: PropTypes.object, versions: PropTypes.array, draft: PropTypes.object, source: PropTypes.object,
  canWrite: PropTypes.bool, skills: PropTypes.array, productionVersion: PropTypes.number, seed: PropTypes.object, state: PropTypes.string,
  onOpenSavedDraft: PropTypes.func, openSavedDraftDisabled: PropTypes.bool,
};
