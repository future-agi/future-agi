import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Pagination, Stack, TextField, Typography } from '@mui/material';
import { useSkillTestingOptions, useSkillV11Scope, isActiveSkillJob } from 'src/api/skills/testing';
import { contextTestCandidate, usePreviewContextTest, useCreateContextTest, useContextTestRuns, useContextTestRun, useContextTestResults, useContextTestAttempt, useCancelContextTest } from 'src/api/skills/context-testing';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import TestInventoryReview from '../tests/TestInventoryReview';
import { AuthoringError, useAuthoringOperation } from '../editor/HotfixHeadReview';

// Intent: authors test the standing root plus accepted dependencies, not a leaf.
// Hierarchy/signature: exact compiled digest above the reviewed outbound tree.
// Domain: standing root, lazy skill reads, reference arm, receipts, privacy.
// Palette/surfaces: neutral paper and divider, status roles only for outcomes.
// Depth: flat sections. Typography: m3/s1/s3. Spacing: shared 12px groups.
// Reject a single green run badge as a release verdict, editable identity JSON,
// and automatically accepted provider consent. Each preview owns a fresh review.
// Last-used test settings are a per-viewer convenience: a person testing several contexts
// in a row keeps the same request, model, ceiling and reference. Nothing is pre-attested.
const LAST_TEST_KEY = 'skills.contextTest.last';
const readLastTest = () => { try { const value = JSON.parse(window.localStorage.getItem(LAST_TEST_KEY) || '{}'); return value && typeof value === 'object' ? value : {}; } catch { return {}; } };
const writeLastTest = (value) => { try { window.localStorage.setItem(LAST_TEST_KEY, JSON.stringify(value)); } catch { /* Storage is optional. */ } };
const emptyReview = () => ({ fingerprint: '', reviewedAllFiles: false, noKnownAnswerKeys: false, classifications: [], falsePositiveSecretFindings: [] });

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
    <Section title="Whole-context result" description="Execution status is not an approval to release. Routing is not applicable to standing context." />
    <StatusBadge status={run.data.status} sx={{ alignSelf: 'flex-start' }} />
    <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>Candidate {run.data.candidate.digest} · run {runId}</Typography>
    {run.data.reference && <Typography variant="s3">Historical reference v{run.data.reference.version} · {run.data.reference.digest}</Typography>}
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
      <Box component="pre" tabIndex={0} aria-label="Whole-context attempt evidence" sx={{ m: 0, p: 1.5, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1, typography: 's1', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(attempt.data, null, 2)}</Box>
    </Stack>)}
  </Stack>;
}
Results.propTypes = { contextId: PropTypes.string.isRequired, runId: PropTypes.string.isRequired };

function Tests({ contextId, candidate, scopeKey, state }) {
  const [last] = useState(readLastTest);
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
  const [runId, setRunId] = useState(null);
  const [historyPage, setHistoryPage] = useState(1);
  const options = useSkillTestingOptions();
  const runnable = (options.data?.models || []).filter((item) => item.canRun);
  // Preselect the last-used model, else the first model this workspace can run; it stays visible and changeable.
  const model = runnable.some((item) => item.modelId === chosenModel) ? chosenModel : (runnable[0]?.modelId || '');
  const history = useContextTestRuns(contextId, historyPage);
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
    if (result) { setRunId(result.id); invalidate(); }
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
    writeLastTest({ prompt, expected, model, limit, reference });
    try { setPreview(await prepare.mutateAsync(config)); } catch { /* The mutation error is rendered below. */ }
  };
  if (options.isLoading || state === 'loading') return <ContentSkeleton />;
  if (options.isError || state === 'error') return <ErrorState title="Context testing unavailable" onRetry={options.refetch} />;
  return <Stack spacing={2}>
    <Section title="Test the whole context" description="The standing root and accepted linked-skill catalog are frozen together. Text harness results do not establish native-session conformance." />
    <Alert severity="info">Runs are private. Provider processing requires a separate acknowledgment after reviewing every model-visible file. No production label is moved.</Alert>
    {!candidate ? <Alert severity="warning">Select an exact published release or load your saved draft before testing.</Alert> : <Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{candidate.kind === 'draft' ? `Saved draft revision ${candidate.revision}` : `Release v${candidate.version}`} · {candidate.digest}</Typography>}
    <Stack spacing={1.5}>
      <TextField size="small" label="Test request" multiline minRows={3} value={prompt} onChange={(event) => { change(setPrompt)(event); setReviewedCase(false); }} disabled={locked} />
      <TextField size="small" label="Expected answer contains" value={expected} onChange={(event) => { change(setExpected)(event); setReviewedCase(false); }} disabled={locked} helperText="A deterministic assertion, not a model judge or a release approval." />
      <FormControlLabel control={<Checkbox checked={reviewedCase} disabled={locked} onChange={(event) => { setReviewedCase(event.target.checked); invalidate(); }} />} label="I reviewed this request and its expected answer." />
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
        <TextField select size="small" label="Task model" value={model} onChange={change(setModel)} disabled={locked} sx={{ minWidth: 200 }}>
          {(options.data?.models || []).map((item) => <MenuItem key={item.modelId} value={item.modelId} disabled={!item.canRun}>{item.modelId}{!item.canRun ? ` · ${item.disabledReason}` : ''}</MenuItem>)}
        </TextField>
        <TextField size="small" label="Maximum estimated cost (USD)" value={limit} onChange={change(setLimit)} disabled={locked} />
        <TextField size="small" label="Historical reference version (optional)" value={reference} onChange={change(setReference)} disabled={locked} error={!validReference} />
      </Stack>
      <AuthoringError error={prepare.error || operation.error} />
      <Button variant="outlined" disabled={locked || !candidate || !prompt.trim() || !expected.trim() || !reviewedCase || !options.data?.enabled || !options.data?.models.some((item) => item.modelId === model && item.canRun) || !validLimit || !validReference} onClick={preflight}>Preview whole-context test</Button>
    </Stack>
    {preview && <Stack spacing={1.5}>
      <Typography variant="m3">{preview.attemptCount} attempts · estimated upper bound ${preview.estimate.upperBoundUsd}</Typography>
      <Typography variant="s3">Task provider: {preview.providerDisclosure.taskProvider}. Content remains private in this workspace but is sent to the selected provider.</Typography>
      {!currentCandidate && <Alert severity="warning">The saved candidate changed. Preview and review the new compiled context before running.</Alert>}
      <TestInventoryReview preview={preview} value={inventory.review} onChange={setInventory} disabled={locked || !currentCandidate} />
      <FormControlLabel control={<Checkbox checked={providerConsent} disabled={locked || !currentCandidate} onChange={(event) => setProviderConsent(event.target.checked)} />} label="I acknowledge provider processing of this reviewed context and test request." />
      <Button variant="contained" disabled={locked || !currentCandidate || !inventory.complete || !providerConsent} onClick={launch}>Run whole-context test</Button>
    </Stack>}
    {operation.uncertain && <Button variant="outlined" disabled={operation.pending} onClick={async () => { const receipt = await operation.retry(); if (receipt) { setRunId(receipt.id); invalidate(); } }}>Recover original context test</Button>}
    {runId && <Results key={runId} contextId={contextId} runId={runId} />}
    <Section title="Context test history" description="Inspect retained runs, including prior candidates. A past result is not silently relabeled as current." />
    {history.isLoading ? <ContentSkeleton /> : history.isError ? <ErrorState title="Test history unavailable" onRetry={history.refetch} /> : !history.data?.results.length || state === 'empty' ? <EmptyState title="No context test runs" description="Preview the complete context to start a private test." /> : history.data.results.map((run) => <Stack key={run.id} direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems="center">
      <StatusBadge status={run.status} /><Typography variant="s3" sx={{ overflowWrap: 'anywhere' }}>{run.candidate.digest}</Typography><Button onClick={() => setRunId(run.id)}>Inspect run {run.id}</Button>
    </Stack>)}
    {history.data?.total_pages > 1 && <Pagination page={historyPage} count={history.data.total_pages} onChange={(_, value) => setHistoryPage(value)} />}
  </Stack>;
}
Tests.propTypes = { contextId: PropTypes.string.isRequired, candidate: PropTypes.object, scopeKey: PropTypes.string.isRequired, state: PropTypes.string };
export default function ContextTestsPanel({ contextId, version, draft, state }) {
  const { identity } = useSkillV11Scope();
  const scopeKey = `${identity}:${contextId}`;
  let candidate = version ? { kind: 'version', version: version.number, digest: version.digest } : null;
  if (draft) { try { candidate = contextTestCandidate(draft); } catch { candidate = null; } }
  return <Tests key={scopeKey} scopeKey={scopeKey} contextId={contextId} candidate={candidate} state={state} />;
}
ContextTestsPanel.propTypes = { contextId: PropTypes.string.isRequired, version: PropTypes.object, draft: PropTypes.object, state: PropTypes.string };
