import { useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import PageHeader from 'src/components/page-header';
import Section from 'src/components/section';
import { paths } from 'src/routes/paths';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useReviewSubjects, useReviewCandidates, useReviewOptions, usePrepareReleaseReview, useSubmitReleaseReview, useShareReleaseEvidence } from 'src/api/skills/review-authoring';
import ReleaseRequestPanel from './ReleaseRequestPanel';
import { ReviewState, TypedEvidenceList } from './ReleaseReviewBody';

// Intent: choose immutable records by name, inspect a frozen packet, then assign
// an independent reviewer. Existing page/section/form tokens; no identity entry.
function PageButtons({ query, page, setPage, label }) {
  return <Stack direction="row" spacing={1}>
    <Button disabled={page <= 1 || query.isFetching} onClick={() => setPage(page - 1)}>Previous {label}</Button>
    <Button disabled={!query.data || page >= query.data.total_pages || query.isFetching} onClick={() => setPage(page + 1)}>Next {label}</Button>
  </Stack>;
}
PageButtons.propTypes = { query: PropTypes.object.isRequired, page: PropTypes.number.isRequired, setPage: PropTypes.func.isRequired, label: PropTypes.string.isRequired };

export default function ReleaseRequestComposer({ scopeKey, renewal }) {
  const navigate = useNavigate();
  const access = useSkillV11Scope();
  const [kind, setKind] = useState(renewal?.kind || 'skill');
  const [resourceId, setResourceId] = useState(renewal?.subjectId || '');
  const [candidateId, setCandidateId] = useState('');
  const [subjectPage, setSubjectPage] = useState(1);
  const [versionPage, setVersionPage] = useState(1);
  const [label, setLabel] = useState('production');
  const [targetId, setTargetId] = useState('');
  const [selected, setSelected] = useState([]);
  const [pipelineUrl, setPipelineUrl] = useState('');
  const [pipelineConfig, setPipelineConfig] = useState('');
  const [pipelineDecision, setPipelineDecision] = useState('');
  const [prepared, setPrepared] = useState(null);
  const [error, setError] = useState('');
  const subjects = useReviewSubjects(kind, subjectPage);
  const versions = useReviewCandidates(kind, resourceId, versionPage);
  const options = useReviewOptions(kind, resourceId, candidateId);
  const prepare = usePrepareReleaseReview();
  const submit = useSubmitReleaseReview();
  const share = useShareReleaseEvidence(resourceId, kind);
  const candidate = versions.data?.results.find((item) => item.id === candidateId);
  const target = options.data?.targets.find((item) => item.target.bindingId === targetId)?.target || null;
  const changeResource = (value) => { setResourceId(value); setCandidateId(''); setVersionPage(1); setSelected([]); setTargetId(''); setError(''); };
  const changeCandidate = (value) => { setCandidateId(value); setSelected([]); setTargetId(''); setError(''); };
  const busy = prepare.isPending;
  const failed = subjects.isError || versions.isError || options.isError;
  const packet = { subjectId: resourceId, subjectKind: kind, candidateDigest: candidate?.digest, target };
  const pipelineComplete = !pipelineUrl && !pipelineConfig && !pipelineDecision || /^https:\/\//.test(pipelineUrl) && pipelineConfig.trim() && pipelineDecision.trim();
  const refresh = () => { subjects.refetch(); if (resourceId) versions.refetch(); if (candidateId) options.refetch(); };
  const preparePacket = async () => {
    setError('');
    try {
      setPrepared(await prepare.mutateAsync({ resourceId, kind, candidateId, label, target, evidence: selected,
        externalPipeline: pipelineUrl ? { trust: 'operator_recorded_unimported', url: pipelineUrl, configuration_identity: pipelineConfig, decision: pipelineDecision, recorded_by: access.scope.userId } : null }));
    } catch { setError('Could not prepare the exact review. Reload the available records and inspect any changed selections. Nothing was submitted.'); }
  };
  return <Stack spacing={3} sx={{ px: { xs: 2, sm: 3 }, py: 2, minWidth: 0 }}>
    <PageHeader title={renewal ? 'Renew release review' : 'Request release review'} description="Production stays unchanged. Sharing, submitting, approving and releasing are separate decisions." />
    {renewal && <Alert severity="info">Renew request revision {renewal.revision}. Select and inspect current evidence explicitly; previous approval will not carry forward.</Alert>}
    {prepared ? <ReleaseRequestPanel scopeKey={scopeKey} state="ready" {...prepared}
      recipients={options.data?.recipients || []} permissions={{ canSubmit: true }}
      onRefresh={() => { setPrepared(null); refresh(); }}
      onSubmit={async (input) => {
        const result = await submit.mutateAsync({ ...input, prepared, ...(renewal ? { requestId: renewal.id, revision: renewal.revision } : {}) });
        navigate(`${paths.dashboard.skills.reviews}?mine=1`);
        return result;
      }} /> : <>
      {failed && <ReviewState state="error" onRetry={refresh} />}
      {error && <Alert severity="error">{error}</Alert>}
      <Section title="Candidate and intended transition"><Stack spacing={1.5}>
        <TextField select size="small" label="Resource kind" value={kind} disabled={busy || Boolean(renewal)} onChange={(event) => { setKind(event.target.value); changeResource(''); setSubjectPage(1); }}>
          <MenuItem value="skill">Skill</MenuItem><MenuItem value="agent_context">Agent context</MenuItem>
        </TextField>
        {renewal ? <Typography variant="s1">Renewing the same resource. Choose its immutable candidate below.</Typography> : <>
          <TextField select size="small" label="Release resource" value={resourceId} disabled={busy || subjects.isFetching} onChange={(event) => changeResource(event.target.value)}>
            <MenuItem value="">Choose a resource</MenuItem>
            {subjects.data?.results.map((item) => <MenuItem key={item.id} value={item.id}>{item.name}</MenuItem>)}
          </TextField>
          <PageButtons query={subjects} page={subjectPage} setPage={(value) => { setSubjectPage(value); changeResource(''); }} label="resources" />
        </>}
        {resourceId && <>
          <TextField select size="small" label="Published candidate" value={candidateId} disabled={busy || versions.isFetching} onChange={(event) => changeCandidate(event.target.value)}>
            <MenuItem value="">Choose an immutable version</MenuItem>
            {versions.data?.results.map((item) => <MenuItem key={item.id} value={item.id}>v{item.number}{item.note ? ` — ${item.note}` : ''}</MenuItem>)}
          </TextField>
          <PageButtons query={versions} page={versionPage} setPage={(value) => { setVersionPage(value); changeCandidate(''); }} label="versions" />
        </>}
        <TextField size="small" label="Release label" value={label} disabled={busy} onChange={(event) => setLabel(event.target.value)} inputProps={{ maxLength: 40 }} />
        {options.isFetching && <ReviewState state="loading" />}
        {options.data && <TextField select size="small" label="Application target" value={targetId} disabled={busy} onChange={(event) => { setTargetId(event.target.value); setSelected([]); }}>
          <MenuItem value="">No application target — text-only decision</MenuItem>
          {options.data.targets.map((item) => <MenuItem key={item.target.bindingId} value={item.target.bindingId}>{item.name} · configuration r{item.target.revision}</MenuItem>)}
        </TextField>}
      </Stack></Section>
      {candidate && options.data && <Section title="Evidence to share and attach">
        <TypedEvidenceList packet={packet} evidence={options.data.evidence} selected={selected} onSelectionChange={setSelected} onShare={(input) => share.mutateAsync(input)} />
      </Section>}
      <Section title="Retained external pipeline (optional)"><Stack spacing={1.5}>
        <Typography variant="s3">Operator-recorded, not a verified Hub verdict. Leave all three fields empty if no external pipeline applies.</Typography>
        <TextField size="small" label="Pipeline result URL" value={pipelineUrl} onChange={(event) => setPipelineUrl(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
        <TextField size="small" label="Pipeline configuration identity" value={pipelineConfig} onChange={(event) => setPipelineConfig(event.target.value)} disabled={busy} inputProps={{ maxLength: 255 }} />
        <TextField size="small" label="Recorded pipeline decision" value={pipelineDecision} onChange={(event) => setPipelineDecision(event.target.value)} disabled={busy} inputProps={{ maxLength: 2000 }} />
      </Stack></Section>
      <Stack direction="row" spacing={1}>
        <Button variant="outlined" disabled={busy} onClick={() => navigate(paths.dashboard.skills.reviews)}>Cancel</Button>
        <Button variant="contained" disabled={busy || failed || options.isFetching || !candidate || !options.data || !pipelineComplete || !/^[a-z0-9][a-z0-9-]{0,39}$/.test(label)} onClick={preparePacket}>Prepare exact review</Button>
      </Stack>
    </>}
  </Stack>;
}
ReleaseRequestComposer.propTypes = { scopeKey: PropTypes.string.isRequired, renewal: PropTypes.object };
