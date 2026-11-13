import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Checkbox, FormControl, FormControlLabel, FormLabel, Radio, RadioGroup, MenuItem, Pagination, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography } from '@mui/material';
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
import TechnicalDetails from '../components/TechnicalDetails';
import TestCaseEditor from '../tests/TestCaseEditor';
import TestInventoryReview from '../tests/TestInventoryReview';
import { AuthoringError, useAuthoringOperation } from '../editor/HotfixHeadReview';
import ContextNativePanel from './ContextNativePanel';

// The one Tests panel for an agent context. One scroll, two sections, each with its own
// loading, error and empty states so a failure in one never hides the other:
//   - Regression cases and application verification: retained, reviewed examples captured from
//     Observe, plus the pinned application target, matched native run, per-cell grades,
//     ship/hold decision and human disagreement (ContextNativePanel).
//   - Whole-context tests: the frozen standing root plus accepted linked skills, run privately
//     against an exact saved draft or published release.
// Intent: incident responders inspect retained cases without mistaking a text answer for a
// repaired application; authors test the standing root plus accepted dependencies, not a leaf.
// Hierarchy: viewed release and native limitation, reviewed requests, then the whole-context run.
// Palette/depth/surfaces: neutral, flat sections and tables; status roles only for outcomes.
// Typography/spacing: m3/s1/s3, 12px groups and standard dense table rows.
// Reject a single green run badge as a release verdict, editable identity JSON,
// and automatically accepted provider consent. Each preview owns a fresh review.
// Last-used test settings are a per-viewer convenience: a person testing several contexts
// in a row keeps the same request, model, ceiling and reference. Nothing is pre-attested.
// Keyed by the organization/workspace/user identity so one login never prefills another's request text.
const lastTestKey = (identity) => `skills.contextTest.last:${identity}`;
const readLastTest = (identity) => { try { const value = JSON.parse(window.localStorage.getItem(lastTestKey(identity)) || '{}'); return value && typeof value === 'object' ? value : {}; } catch { return {}; } };
const writeLastTest = (identity, value) => { try { window.localStorage.setItem(lastTestKey(identity), JSON.stringify(value)); } catch { /* Storage is optional. */ } };
const emptyReview = () => ({ fingerprint: '', reviewedAllFiles: false, noKnownAnswerKeys: false, classifications: [], falsePositiveSecretFindings: [] });


function RegressionCases({ contextId, version, versions, canWrite, skills, productionVersion, seed, children, state, answerHistory, renderAnswer, answerRunId, historyPagination, latestAnswer }) {
  const suite = useContextTestSuite(contextId);
  const save = useSaveContextTestSuite(contextId);
  const [editor, setEditor] = useState(null);
  const [error, setError] = useState('');
  if (suite.isLoading) return <Stack spacing={3}><ContentSkeleton variant="panel" label="Loading context regression cases" />{typeof children === 'function' ? children(false) : children}</Stack>;
  if (suite.isError) return <Stack spacing={3}><ErrorState title="Could not load context regression cases" onRetry={suite.refetch} />{typeof children === 'function' ? children(false) : children}</Stack>;
  if (!suite.data) return <EmptyState title="Select an authenticated workspace" description="Regression cases remain scoped to their context and workspace." />;
  const changed = editor && editor.revision !== suite.data.revision;
  return <Stack spacing={1.5}>
    <ContextNativePanel contextId={contextId} versions={versions} viewed={version} cases={suite.data.cases} canWrite={canWrite} productionVersion={productionVersion} seed={seed} state={state} answerHistory={answerHistory} renderAnswer={renderAnswer} answerRunId={answerRunId} historyPagination={historyPagination} latestAnswer={latestAnswer} reviewedCases={<Stack spacing={1.5}>
    <Section title={<Typography component="span" variant="m3">Reviewed cases</Typography>} description={<Typography component="span" variant="s3">To turn a failure into a case, open it in Observe and choose Create context regression case.</Typography>}>
      {!suite.data.cases.length ? <EmptyState title="No regression cases yet" description="Capture a retained trace, then review its redaction and expected behavior. Working neighboring cases belong in the same suite." /> : <TableContainer>
        <Table size="small" aria-label="Context regression cases"><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Example</TableCell><TableCell>Review</TableCell><TableCell>Checks</TableCell><TableCell>Action</TableCell></TableRow></TableHead>
          <TableBody>{suite.data.cases.map((item) => <TableRow key={item.id} hover sx={{ height: ROW_HEIGHT.default }}>
            <TableCell><Stack spacing={0.5}><Typography component="div" variant="s1">{item.title}</Typography><Typography component="div" variant="s3" color="text.secondary" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.prompt}</Typography></Stack></TableCell>
            <TableCell>{item.reviewed ? 'Reviewed' : 'Needs review'}</TableCell><TableCell>{item.checks.length ? item.checks.length : <StatusBadge status="warning" label="No checks yet" />}</TableCell>
            <TableCell><Button size="small" variant="outlined" disabled={!canWrite} onClick={() => { setError(''); setEditor({ value: item, revision: suite.data.revision, cases: suite.data.cases }); }}>{!item.checks.length ? 'Add checks' : item.reviewed ? 'Open' : 'Review example'}</Button></TableCell>
          </TableRow>)}</TableBody></Table>
      </TableContainer>}
    </Section></Stack>}>{children}</ContextNativePanel>
    {editor && <TestCaseEditor open subjectKind="agent_context" initialCase={editor.value} applicationBehavior={{ skills }} canSave={canWrite && !changed} isSaving={save.isPending}
      error={changed ? 'The suite changed. Close and reopen the example after reviewing the latest cases; your edits were not submitted.' : error}
      onClose={() => { if (!save.isPending) setEditor(null); }}
      onSave={async (value) => {
        if (changed) return;
        try {
          await save.mutateAsync({ revision: editor.revision, cases: editor.cases.map((item) => item.id === value.id ? value : item) });
          setEditor(null);
        } catch { setError('Could not save this reviewed example. Your edits are preserved; retry after checking access and the suite revision.'); }
      }} />}
  </Stack>;
}
RegressionCases.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object, versions: PropTypes.array.isRequired, skills: PropTypes.array.isRequired, canWrite: PropTypes.bool.isRequired, productionVersion: PropTypes.number, seed: PropTypes.object, children: PropTypes.oneOfType([PropTypes.node, PropTypes.func]), state: PropTypes.string, answerHistory: PropTypes.object, renderAnswer: PropTypes.func, answerRunId: PropTypes.string, historyPagination: PropTypes.node, latestAnswer: PropTypes.object };

// Which exact candidate the whole-context section tests: the saved draft or the viewed release.
// It changes only the whole-context candidate; application verification keeps the viewed release.
function CandidateSource({ source }) {
  return <FormControl>
    <FormLabel id="context-test-source">Test which version</FormLabel>
    <RadioGroup row aria-labelledby="context-test-source" value={source.testingDraft ? 'draft' : 'version'} onChange={(event) => source.onSelect(event.target.value === 'draft')}>
      <FormControlLabel value="draft" disabled={!source.draftAvailable} control={<Radio size="small" />} label="Saved draft" />
      <FormControlLabel value="version" disabled={!source.releaseAvailable} control={<Radio size="small" />} label="Published version" />
    </RadioGroup>
  </FormControl>;
}
CandidateSource.propTypes = { source: PropTypes.object.isRequired };

// In the editor the draft may be unsaved, so the whole-context section saves it first and opens
// the exact saved revision on the context's Tests tab. Opening tests never publishes.
function SavedDraftHandoff({ onOpen, disabled }) {
  return <Stack spacing={1.5}>
    <Alert severity="info">Test the saved standing root and accepted linked skills together. Opening tests does not publish or move production.</Alert>
    <Button variant="outlined" sx={{ alignSelf: 'flex-start' }} disabled={disabled} onClick={onOpen}>Save and open whole-context tests</Button>
  </Stack>;
}
SavedDraftHandoff.propTypes = { onOpen: PropTypes.func.isRequired, disabled: PropTypes.bool };

function Results({ contextId, runId }) {
  const [page, setPage] = useState(1);
  const [attemptId, setAttemptId] = useState(null);
  const run = useContextTestRun(contextId, runId);
  const results = useContextTestResults(contextId, runId, page);
  const attempt = useContextTestAttempt(contextId, runId, attemptId);
  const cancel = useCancelContextTest(contextId, runId);
  if (run.isLoading) return <ContentSkeleton />;
  if (run.isError) return <ErrorState title="Context run unavailable" onRetry={run.refetch} />;
  if (!run.data) return null;
  return <Stack spacing={1.5}>
    <Section title={<Typography component="span" variant="m3">Whole-context result</Typography>} description="Execution status is not an approval to release. Routing is not applicable to standing context." />
    <StatusBadge status={run.data.status} sx={{ alignSelf: 'flex-start' }} />
    <IdentityChip value={run.data.candidate.digest} label="Candidate" /><IdentityChip value={runId} label="Run" kind="id" />
    {run.data.reference && <Stack spacing={0.5}><Typography variant="s3">Historical reference v{run.data.reference.version}</Typography><IdentityChip value={run.data.reference.digest} label="Reference" /></Stack>}
    {isActiveSkillJob(run.data.status) && <Button variant="outlined" disabled={cancel.isPending} onClick={() => cancel.mutate({})}>Cancel context test</Button>}
    <AuthoringError error={cancel.error} />
    {results.isLoading ? <ContentSkeleton /> : results.isError ? <ErrorState title="Case results unavailable" onRetry={results.refetch} /> : results.data?.results.map((row) => <Stack spacing={1} key={row.case_id} sx={{ py: 1, borderBottom: 1, borderColor: 'divider' }}>
      <Typography variant="m3">{row.title}</Typography>
      {row.variants.map((variant) => <Stack key={variant.variant} spacing={1}>
        <Typography variant="s1">{variant.variant}</Typography>
        {variant.attempts.map((item) => <Stack key={item.id} direction="row" spacing={1} alignItems="center" flexWrap="wrap">
          <StatusBadge status={item.status} /><StatusBadge status={item.answer_verdict} label={`Answer: ${item.answer_verdict}`} />
          <Button variant="text" onClick={() => setAttemptId(item.id)}>Inspect attempt {item.repeat_index + 1}</Button>
        </Stack>)}
      </Stack>)}
    </Stack>)}
    {results.data?.total_pages > 1 && <Pagination page={page} count={results.data.total_pages} onChange={(_, value) => { setPage(value); setAttemptId(null); }} />}
    {attemptId && (attempt.isLoading ? <ContentSkeleton /> : attempt.isError ? <ErrorState title="Attempt unavailable" onRetry={attempt.refetch} /> : attempt.data && <Stack spacing={1}>
      <Typography variant="m3">Retained attempt evidence</Typography>
      <Typography variant="s1">Standing root fully delivered: {attempt.data.root_fully_delivered ? 'yes' : 'no'}</Typography>
      <Typography variant="s3">Receipts identify delivered bytes, not instruction adherence. An unread linked skill is not silently counted as delivered.</Typography>
      <TechnicalDetails title="Attempt details"><Box component="pre" tabIndex={0} aria-label="Whole-context attempt evidence" sx={{ m: 0, p: 1.5, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1, typography: 's1', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(attempt.data, null, 2)}</Box></TechnicalDetails>
    </Stack>)}
  </Stack>;
}
Results.propTypes = { contextId: PropTypes.string.isRequired, runId: PropTypes.string.isRequired };

function WholeContextTests({ contextName, contextId, candidate, scopeKey, identity, state, source, primary, onStarted }) {
  const [last] = useState(() => readLastTest(identity));
  const [prompt, setPrompt] = useState(typeof last.prompt === 'string' ? last.prompt : '');
  const [expected, setExpected] = useState(typeof last.expected === 'string' ? last.expected : '');
  const [chosenModel, setModel] = useState(typeof last.model === 'string' ? last.model : '');
  const [limit, setLimit] = useState(typeof last.limit === 'string' ? last.limit : '');
  const [reference, setReference] = useState(typeof last.reference === 'string' ? last.reference : '');
  const [reviewedCase, setReviewedCase] = useState(false);
  const [preview, setPreview] = useState(null);
  const [configuration, setConfiguration] = useState(null);
  const [inventory, setInventory] = useState({ review: emptyReview(), complete: false });
  const [providerConsent, setProviderConsent] = useState(false);
  const options = useSkillTestingOptions();
  const runnable = (options.data?.models || []).filter((item) => item.canRun);
  // Preselect the last-used model, else the first model this workspace can run; it stays visible and changeable.
  const model = runnable.some((item) => item.modelId === chosenModel) ? chosenModel : (runnable[0]?.modelId || '');
  const prepare = usePreviewContextTest(contextId);
  const create = useCreateContextTest(contextId);
  const operation = useAuthoringOperation(scopeKey);
  const locked = operation.pending || operation.uncertain || prepare.isPending;
  const invalidate = () => { setPreview(null); setInventory({ review: emptyReview(), complete: false }); setProviderConsent(false); };
  const change = (setter) => (event) => { setter(event.target.value); invalidate(); };
  const validLimit = /^\d+(\.\d{1,8})?$/.test(limit) && Number(limit) > 0;
  const validReference = !reference || (/^[1-9]\d*$/.test(reference) && Number.isSafeInteger(Number(reference)));
  const currentCandidate = candidate && configuration && JSON.stringify(candidate) === JSON.stringify(configuration.candidate);
  const launch = async () => {
    const result = await operation.run((input, extra) => create.mutateAsync({ ...input, ...extra }), { configuration, expectedFingerprint: preview.fingerprint, inventoryReview: inventory.review });
    if (result) { onStarted(result.id); invalidate(); }
  };
  const preflight = async () => {
    invalidate();
    const config = {
      harness: 'contexts-text-v1.2', candidate,
      cases: { kind: 'ad_hoc', cases: [{ id: crypto.randomUUID(), title: 'Reviewed context regression', source_group_id: 'manual-context-test', prompt, fixtures: [], expected_trigger: null,
        checks: [{ id: crypto.randomUUID(), kind: 'contains', critical: true, config: { text: expected, case_sensitive: false } }], reviewed: reviewedCase }] },
      baselines: reference ? [{ kind: 'version', version: Number(reference) }] : [{ kind: 'without_context' }], competitors: [],
      model: { model_id: model, temperature: 0, max_output_tokens: 1024 }, judge: null,
      profile: 'quick', repetitions: 1, limits: { max_total_tokens: options.data.limits.profiles.quick.maxTotalTokens, max_estimated_cost_usd: limit }, purpose: 'regression', parent_run_id: null,
    };
    setConfiguration(config);
    writeLastTest(identity, { prompt, expected, model, limit, reference });
    try { setPreview(await prepare.mutateAsync(config)); } catch { /* The mutation error is rendered below. */ }
  };
  if (options.isLoading || state === 'loading' || source?.isLoading) return <ContentSkeleton />;
  if (options.isError || state === 'error') return <ErrorState title="Couldn't load answer-check options." onRetry={options.refetch} sx={{ alignItems: 'flex-start', textAlign: 'left', py: 1.5, px: 0 }} />;
  if (source?.isError) return <ErrorState title="Saved draft unavailable" onRetry={source.onRetry} />;
  return <Stack spacing={2}>
    <Typography variant="s3" color="text.secondary">Test runs are private and don’t change production. You’ll confirm the files sent to the model before anything runs.</Typography>
    {!candidate ? <Alert severity="warning">Select an exact published release or load your saved draft before testing.</Alert> : <Stack direction="row" spacing={1} alignItems="center"><Typography variant="s3">{candidate.kind === 'draft' ? `Saved draft revision ${candidate.revision}` : `Release v${candidate.version}`}</Typography><IdentityChip value={candidate.digest} label="Candidate" /></Stack>}
    <Stack spacing={1.5}>
      <TextField size="small" label="Test request" multiline minRows={3} value={prompt} onChange={(event) => { change(setPrompt)(event); setReviewedCase(false); }} disabled={locked} />
      <TextField size="small" label="Expected answer contains" value={expected} onChange={(event) => { change(setExpected)(event); setReviewedCase(false); }} disabled={locked} helperText="The answer must contain this text." />
      <FormControlLabel control={<Checkbox checked={reviewedCase} disabled={locked} onChange={(event) => { setReviewedCase(event.target.checked); invalidate(); }} />} label="I reviewed this request and its expected answer." />
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
        <TextField select size="small" label="Task model" value={model} onChange={change(setModel)} disabled={locked} sx={{ minWidth: 200 }}>
          {(options.data?.models || []).map((item) => <MenuItem key={item.modelId} value={item.modelId} disabled={!item.canRun}>{item.modelId}{!item.canRun ? ` · ${item.disabledReason}` : ''}</MenuItem>)}
        </TextField>
        <TextField size="small" label="Maximum estimated cost (USD)" value={limit} onChange={change(setLimit)} disabled={locked} />
        <TextField size="small" label="Historical reference version (optional)" value={reference} onChange={change(setReference)} disabled={locked} error={!validReference} />
      </Stack>
      <AuthoringError error={prepare.error || operation.error} />
      <Button variant={primary && !preview ? "contained" : "outlined"} disabled={locked || !candidate || !prompt.trim() || !expected.trim() || !reviewedCase || !options.data?.enabled || !options.data?.models.some((item) => item.modelId === model && item.canRun) || !validLimit || !validReference} onClick={preflight}>Preview whole-context test</Button>
      <Typography variant="s3" color="text.secondary">{locked ? 'Wait for the current operation to finish.' : !candidate ? 'Choose a saved draft or published version.' : !options.data?.enabled || !model ? 'A runnable model must be enabled for this workspace.' : !prompt.trim() || !expected.trim() ? 'Add a request and the answer text to check.' : !reviewedCase ? 'Tick the reviewed-case acknowledgment to continue.' : !validLimit ? 'Enter a positive maximum cost.' : !validReference ? 'Enter a valid published reference version.' : 'Preview the files before starting the test.'}</Typography>
    </Stack>
    {preview && <Stack spacing={1.5}>
      <Typography variant="m3">Up to {preview.attemptCount} runs · at most ${(Math.ceil(Number(preview.estimate.upperBoundUsd) * 100) / 100).toFixed(2)}</Typography>
      <Typography variant="s3">Task provider: {preview.providerDisclosure.taskProvider}. Content remains private in this workspace but is sent to the selected provider.</Typography>
      {!currentCandidate && <Alert severity="warning">The saved candidate changed. Preview and review the new compiled context before running.</Alert>}
      <TestInventoryReview subjectName={contextName} subjectKind="agent_context" subjectId={contextId} preview={preview} value={inventory.review} onChange={setInventory} disabled={locked || !currentCandidate} />
      <FormControlLabel control={<Checkbox checked={providerConsent} disabled={locked || !currentCandidate} onChange={(event) => setProviderConsent(event.target.checked)} />} label="I acknowledge provider processing of this reviewed context and test request." />
      <Button variant={primary ? "contained" : "outlined"} disabled={locked || !currentCandidate || !inventory.complete || !providerConsent} onClick={launch}>Run whole-context test</Button>
      {(!inventory.complete || !providerConsent) && <Typography variant="s3" color="text.secondary">Review all files and findings, then confirm provider processing to run this test.</Typography>}
    </Stack>}
    {operation.uncertain && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const receipt = await operation.retry(); if (receipt) { onStarted(receipt.id); invalidate(); } }}>Recover original context test</Button>}

  </Stack>;
}
WholeContextTests.propTypes = { contextName: PropTypes.string, primary: PropTypes.bool, onStarted: PropTypes.func.isRequired, contextId: PropTypes.string.isRequired, candidate: PropTypes.object, scopeKey: PropTypes.string.isRequired, identity: PropTypes.string.isRequired, state: PropTypes.string, source: PropTypes.object };

function Tests({ contextName, contextId, version, versions, canWrite, skills, productionVersion, seed, candidate, scopeKey, identity, state, source, onOpenSavedDraft, openSavedDraftDisabled }) {
  const [answerRunId, setAnswerRunId] = useState(null);
  const [historyPage, setHistoryPage] = useState(1);
  const history = useContextTestRuns(contextId, historyPage);
  const latestAnswers = useContextTestRuns(contextId, 1);
  return <Stack spacing={3}>
    <RegressionCases contextId={contextId} version={version} versions={versions} canWrite={canWrite} skills={skills} productionVersion={productionVersion} seed={seed} state={state} answerHistory={history} latestAnswer={latestAnswers.data?.results[0]} answerRunId={answerRunId} renderAnswer={(id) => <Results key={id} contextId={contextId} runId={id} />} historyPagination={history.data?.total_pages > 1 ? <Pagination page={historyPage} count={history.data.total_pages} onChange={(_, value) => setHistoryPage(value)} /> : null}>
    {(primary) => <Stack spacing={2}>
      <Section title={<Typography component="span" variant="m3">Check answers with this context</Typography>} description="Tests your instructions and locked linked skills together. To verify an application repair, choose the application test." />
      {source && !onOpenSavedDraft && <CandidateSource source={source} />}
      {onOpenSavedDraft ? <SavedDraftHandoff onOpen={onOpenSavedDraft} disabled={openSavedDraftDisabled} />
        : <WholeContextTests contextName={contextName} contextId={contextId} candidate={candidate} scopeKey={scopeKey} identity={identity} state={state} source={source} primary={primary} onStarted={setAnswerRunId} />}
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

// version: the viewed release (application verification and the default whole-context candidate).
// draft: the saved draft to test instead, when source.testingDraft is set (or no source is given).
// source: the saved-draft/viewed-release switch for the whole-context candidate.
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
