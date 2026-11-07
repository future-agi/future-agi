import { useEffect, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { useSearchParams } from 'react-router-dom';
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from '@mui/material';

import { useApplySkillAssist, useCancelSkillAssist, useCreateSkillAssist, usePreviewSkillAssist, useSkillAssist } from 'src/api/skills/assist';
import { isActiveSkillJob, useSkillTestingOptions, useSkillTestAttempt, useSkillTestResults, useSkillV11Scope } from 'src/api/skills/testing';
import Section from 'src/components/section';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import StatusBadge from 'src/components/status-badge';
import AssistProposal from './AssistProposal';

const titles = { draft: 'Draft with AI', description: 'Improve when this skill is used', summary: 'Summarize changes' };
const stamp = (draft) => JSON.stringify([draft?.revision, draft?.files]);
const fileShape = PropTypes.shape({ path: PropTypes.string.isRequired, content: PropTypes.string.isRequired });
const panelProps = {
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired }).isRequired,
  draft: PropTypes.shape({ revision: PropTypes.number.isRequired, files: PropTypes.arrayOf(fileShape).isRequired, baseVersion: PropTypes.number }),
  kind: PropTypes.oneOf(['draft', 'description', 'summary']).isRequired,
  sourceRunId: PropTypes.string,
  onApplied: PropTypes.func,
  onUseSummary: PropTypes.func,
  isDirty: PropTypes.bool,
  onSave: PropTypes.func,
};

async function draftDigest(files) {
  const hash = async (text) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))), (byte) => byte.toString(16).padStart(2, '0')).join('');
  const manifest = await Promise.all(files.map(async (file) => [file.path, await hash(file.content)]));
  // Python's canonical manifest sorts Unicode code points and escapes non-ASCII.
  manifest.sort(([left], [right]) => {
    const a = Array.from(left, (char) => char.codePointAt(0));
    const b = Array.from(right, (char) => char.codePointAt(0));
    for (let index = 0; index < Math.min(a.length, b.length); index += 1) {
      if (a[index] !== b[index]) return a[index] - b[index];
    }
    return a.length - b.length;
  });
  const canonical = JSON.stringify(manifest).replace(/[\u007f-￿]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`);
  return `sha256:${await hash(canonical)}`;
}

function RoutingExample({ skillId, runId, row }) {
  const attemptId = row.variants.find((variant) => variant.variant === 'candidate')?.attempts[0]?.id;
  const query = useSkillTestAttempt(skillId, runId, attemptId);
  if (query.isLoading) return <ContentSkeleton rows={2} />;
  if (query.isError) return <ErrorState title="Could not load this example" onRetry={query.refetch} />;
  if (!query.data) return null;
  return <Stack spacing={0.5} sx={{ pl: 2 }}>
    <Typography variant="s1" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{query.data.case.prompt}</Typography>
    {query.data.case.fixtures.map((fixture) => <Typography variant="s3" key={fixture.id} sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{fixture.name}: {fixture.text}</Typography>)}
    {!query.data.case.reviewed && <Alert severity="warning">This example is not reviewed and cannot be used for a description proposal.</Alert>}
  </Stack>;
}
RoutingExample.propTypes = { skillId: PropTypes.string.isRequired, runId: PropTypes.string.isRequired, row: PropTypes.object.isRequired };

function AssistForm({ skill, draft, kind, sourceRunId, onApplied, onUseSummary, isDirty = false, onSave }) {
  const options = useSkillTestingOptions();
  const examples = useSkillTestResults(skill.id, kind === 'description' ? sourceRunId : null, { pageSize: 20 });
  const previewMutation = usePreviewSkillAssist(skill.id);
  const create = useCreateSkillAssist(skill.id);
  const [proposalId, setProposalId] = useState(null);
  const proposalQuery = useSkillAssist(skill.id, proposalId);
  const apply = useApplySkillAssist(skill.id, proposalId);
  const cancel = useCancelSkillAssist(skill.id, proposalId);
  const [instruction, setInstruction] = useState('');
  const [contextText, setContextText] = useState('');
  const [modelId, setModelId] = useState('');
  const [spend, setSpend] = useState('');
  const [caseIds, setCaseIds] = useState([]);
  const [review, setReview] = useState(null);
  const [reasons, setReasons] = useState({});
  const [acknowledged, setAcknowledged] = useState(false);
  const [submission, setSubmission] = useState(null);
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState(null);
  const [preparing, setPreparing] = useState(false);
  const [applied, setApplied] = useState(false);
  const current = useRef({ draft, isDirty });
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  current.current = { draft, isDirty };
  const [params] = useSearchParams();
  const previewState = params.get('state');
  const active = proposalQuery.data && isActiveSkillJob(proposalQuery.data.status);
  const locked = preparing || create.isPending || active || uncertain;
  const stale = Boolean(review && (review.stamp !== stamp(draft) || isDirty));
  const selectedRows = (examples.data?.results ?? []).filter((row) => caseIds.includes(row.caseId));
  const routingReady = selectedRows.some((row) => row.expectedTrigger === true) && selectedRows.some((row) => row.expectedTrigger === false);
  const selectedModel = options.data?.models.find((model) => model.modelId === modelId);

  const invalidatePreview = () => {
    setReview(null);
    setReasons({});
    setAcknowledged(false);
    setSubmission(null);
    setError(null);
  };
  const preview = async () => {
    setPreparing(true);
    setError(null);
    try {
      const saved = onSave ? await onSave() : current.current.draft;
      if (!saved || (!onSave && current.current.isDirty) || saved.revision < 1) throw new Error('Save the draft and resolve any conflict before previewing assistance.');
      const digest = await draftDigest(saved.files);
      const input = { kind, model: { modelId, temperature: 0, maxOutputTokens: 4096 }, maxEstimatedCostUsd: spend, ...(kind === 'summary' ? { fromVersion: saved.baseVersion, to: { kind: 'draft', revision: saved.revision, digest } } : { revision: saved.revision, expectedDigest: digest }), ...(kind === 'draft' ? { instruction, contextText } : {}), ...(kind === 'description' ? { sourceRunId, caseIds } : {}) };
      if (!mounted.current) return;
      const plan = await previewMutation.mutateAsync(input);
      if (!mounted.current) return;
      setReview({ plan, input, stamp: stamp(saved), beforeFiles: saved.files });
      setReasons({});
      setAcknowledged(false);
      setSubmission(null);
      setProposalId(null);
      setApplied(false);
    } catch (failure) {
      setError(failure.message || 'Could not preview assistance. Your inputs are still here.');
    } finally {
      setPreparing(false);
    }
  };
  const start = async () => {
    if (!review || (stale && !submission)) return;
    const request = submission ?? { ...review.input, idempotencyKey: crypto.randomUUID(), expectedInputFingerprint: review.plan.inputFingerprint, acknowledgeProviderProcessing: true, outboundReview: { fingerprint: review.plan.inputFingerprint, falsePositiveSecretFindings: review.plan.outboundFindings.map((finding) => ({ findingId: finding.findingId, reason: reasons[finding.findingId] })) } };
    setSubmission(request);
    setError(null);
    try {
      const receipt = await create.mutateAsync(request);
      if (!mounted.current) return;
      setProposalId(receipt.id);
      setUncertain(false);
    } catch (failure) {
      setUncertain(!failure.response || failure.response.status >= 500);
      setError('Could not confirm the request. Retry uses the same request key; no automatic second generation is sent.');
    }
  };
  const applyProposal = async () => {
    if (!review || stale || current.current.isDirty) return;
    setError(null);
    const beforeApply = stamp(current.current.draft);
    try {
      const saved = await apply.mutateAsync({ revision: proposalQuery.data.baseRevision, expectedDigest: proposalQuery.data.baseDigest });
      if (!mounted.current) return;
      setApplied(true);
      if (current.current.isDirty || beforeApply !== stamp(current.current.draft)) {
        setError('The proposal was saved, but you made later local edits. They are preserved; resolve the editor conflict before saving again.');
      } else {
        onApplied?.(saved);
      }
    } catch {
      setError('Could not apply this proposal. Your current draft is preserved. Reload or resolve the draft conflict before reviewing another proposal.');
    }
  };
  const stop = async () => {
    try { await cancel.mutateAsync({}); } catch { setError('Could not confirm stopping. Keep this page open to check the job status.'); }
  };

  if (previewState === 'loading' || options.isLoading) return <ContentSkeleton rows={4} />;
  if (previewState === 'error' || options.isError) return <ErrorState title="Could not load assistance options" onRetry={options.refetch} />;
  if (!draft) return <EmptyState variant="no-results" title="Save a draft first" description="Assistance belongs to your private saved draft. Create or open a draft to continue." />;
  if (previewState === 'forbidden') return <ErrorState title="Write access is required" detail="A current workspace member can request and apply a private proposal." />;
  if (!options.data?.enabled || previewState === 'missing-key' || !options.data.models.some((model) => model.canRun)) return <EmptyState variant="no-results" title="Connect a supported workspace model" description="Assistance uses your workspace provider connection. No managed model or organization-global key is substituted." />;

  return (
    <Section title={titles[kind]} description="Private proposal → review exact changes → apply to draft">
      <Stack spacing={2} sx={{ minWidth: 0 }}>
        <Typography variant="s1" color="text.secondary">{kind === 'draft' ? 'Provide your policy. Missing facts will be questions, not invented rules.' : kind === 'description' ? 'Choose reviewed positive and near-miss examples from the source run. Only the description will change.' : 'Summarize the exact change from the draft’s base version. The note remains editable.'}</Typography>
        {kind === 'draft' && <>
          <TextField label="Task and use / non-use boundary" value={instruction} multiline minRows={3} fullWidth disabled={locked} onChange={(event) => { setInstruction(event.target.value); invalidatePreview(); }} />
          <TextField label="Actual policy, examples and constraints" value={contextText} multiline minRows={4} fullWidth disabled={locked} helperText="Text only. Do not include credentials or facts that are not approved." onChange={(event) => { setContextText(event.target.value); invalidatePreview(); }} />
        </>}
        {kind === 'description' && <Section title="Routing examples" description="Both a positive and a near-miss negative are required.">
          {!sourceRunId && <Alert severity="info">Open this action from a test run to select its frozen examples.</Alert>}
          {examples.isLoading && <ContentSkeleton rows={3} />}
          {examples.isError && <ErrorState title="Could not load the source run" onRetry={examples.refetch} />}
          {(examples.data?.results ?? []).map((row) => <Box key={row.caseId} sx={{ mb: 1 }}>
            <FormControlLabel control={<Checkbox disabled={locked || row.expectedTrigger === null} checked={caseIds.includes(row.caseId)} onChange={(_, checked) => { setCaseIds((ids) => checked ? [...ids, row.caseId] : ids.filter((id) => id !== row.caseId)); invalidatePreview(); }} />} label={`${row.title} · ${row.expectedTrigger === true ? 'Use this skill' : row.expectedTrigger === false ? 'Do not use this skill' : 'Either'}`} />
            {caseIds.includes(row.caseId) && <RoutingExample skillId={skill.id} runId={sourceRunId} row={row} />}
          </Box>)}
        </Section>}
        <Stack spacing={1.5} direction={{ xs: 'column', sm: 'row' }}>
          <TextField select fullWidth size="small" label="Workspace model" value={modelId} disabled={locked} onChange={(event) => { setModelId(event.target.value); invalidatePreview(); }}>
            {options.data.models.map((model) => <MenuItem key={model.modelId} value={model.modelId} disabled={!model.canRun}>{model.modelId}{!model.canRun ? ` — ${model.disabledReason}` : ''}</MenuItem>)}
          </TextField>
          <TextField fullWidth size="small" label="Estimated-cost stop (USD)" value={spend} disabled={locked} inputProps={{ inputMode: 'decimal' }} onChange={(event) => { setSpend(event.target.value); invalidatePreview(); }} />
        </Stack>
        <Typography variant="s3" color="text.secondary">One completion · up to 4096 output tokens · no scripts, external actions or automatic publication.</Typography>
        <Box><Button variant="outlined" disabled={locked || !selectedModel?.canRun || !/^(?:\d+)(?:\.\d{1,8})?$/.test(spend) || Number(spend) <= 0 || (kind === 'draft' && !instruction.trim()) || (kind === 'description' && !routingReady)} onClick={preview}>{preparing ? 'Saving and previewing…' : 'Preview data and spend'}</Button></Box>
        {error && <Alert severity="error">{error}</Alert>}
        {review && !proposalId && <Section title="Confirm outbound processing" description={`Estimated upper bound: USD ${review.plan.estimate.upperBoundUsd}`}>
          <Stack spacing={1.5}>
            <Typography variant="s1">Provider: {review.plan.providerDisclosure.provider}. Uses your workspace model connection. Provider billing may differ from this estimate.</Typography>
            <Typography variant="s3" color="text.secondary" sx={{ fontVariantNumeric: 'tabular-nums' }}>{review.plan.allowance.inputBytes} input bytes · up to {review.plan.allowance.maxOutputTokens} output tokens · {review.plan.allowance.deadlineSeconds}-second stop ceiling, not a completion promise.</Typography>
            <Typography variant="s3" color="text.secondary">Complete saved files and the supplied context are reviewed together; no hidden truncation.</Typography>
            {stale && <Alert severity="warning">For earlier content. Save your changes and preview again.</Alert>}
            {review.plan.outboundFindings.map((finding) => <Stack key={finding.findingId} spacing={0.5}>
              <Typography variant="s1" sx={{ overflowWrap: 'anywhere' }}>{finding.location.kind}{finding.location.pointer ? ` · ${finding.location.pointer}` : ''}{finding.location.path ? ` · ${finding.location.path}` : ''}{finding.location.caseId ? ` · example ${finding.location.caseId}` : ''}{finding.location.fixtureId ? ` · fixture ${finding.location.fixtureId}` : ''}{finding.location.line ? ` · line ${finding.location.line}` : ''}: {finding.explanation}</Typography>
              <TextField size="small" label="Why this is not a real credential" fullWidth value={reasons[finding.findingId] ?? ''} disabled={create.isPending || uncertain} onChange={(event) => { setReasons((values) => ({ ...values, [finding.findingId]: event.target.value })); setSubmission(null); }} />
            </Stack>)}
            <FormControlLabel control={<Checkbox checked={acknowledged} disabled={create.isPending || uncertain} onChange={(_, checked) => setAcknowledged(checked)} />} label="I reviewed the outbound data and authorize this provider processing and estimated-cost stop." />
            <Box><Button variant="outlined" disabled={create.isPending || (stale && !submission) || !acknowledged || review.plan.outboundFindings.some((finding) => !reasons[finding.findingId]?.trim())} onClick={start}>{create.isPending ? 'Submitting…' : submission ? 'Retry same request' : 'Generate proposal'}</Button></Box>
          </Stack>
        </Section>}
        {proposalId && proposalQuery.isLoading && <ContentSkeleton rows={3} />}
        {proposalQuery.isError && <ErrorState title="Could not load the proposal" detail="Your job may still be running. Refreshing does not create another generation." onRetry={proposalQuery.refetch} />}
        {proposalQuery.data && <>
          <Stack direction="row" alignItems="center" spacing={1} role="status" aria-live="polite"><StatusBadge status={proposalQuery.data.status} /><Typography variant="s3" color="text.secondary">Closing this panel does not cancel the job.</Typography></Stack>
          {active && <Stack spacing={1}><Typography variant="s3">Stop prevents new calls. An in-flight request may finish and incur provider charges.</Typography><Box><Button variant="outlined" disabled={cancel.isPending || proposalQuery.data.status === 'cancelling'} onClick={stop}>{proposalQuery.data.status === 'cancelling' ? 'Stopping…' : 'Stop generation'}</Button></Box></Stack>}
          {proposalQuery.data.error && <Alert severity="error">{proposalQuery.data.error.message}</Alert>}
          {applied ? <Alert severity="success">Applied to your draft. Review and test it before publishing.</Alert> : <AssistProposal proposal={proposalQuery.data} beforeFiles={review?.beforeFiles ?? []} isStale={stale || previewState === 'stale'} applying={apply.isPending} onApply={applyProposal} onUseSummary={onUseSummary} />}
        </>}
        {previewState === 'empty' && !proposalId && <Typography variant="s3" color="text.secondary">No suggestion yet. Previewing does not call the model.</Typography>}
      </Stack>
    </Section>
  );
}
AssistForm.propTypes = panelProps;

export default function AssistPanel(props) {
  const access = useSkillV11Scope();
  return <AssistForm key={`${access.identity}:${props.skill.id}:${props.kind}:${props.sourceRunId ?? ''}`} {...props} />;
}
AssistPanel.propTypes = panelProps;
