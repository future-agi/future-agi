import { useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, Stack, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { useSkillDraft } from 'src/api/skills';
import { useCheckSkillSource, useGitHubPreview, useSkillSource, useStageSkillSourceUpdate } from 'src/api/skills/github';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
import { paths } from 'src/routes/paths';
import { usePreviewState } from 'src/sections/skills/components/SkillsStatePreview';
import { GitHubPreviewReview } from './GitHubImportPanel';

// Source evidence stays quiet; the actionable replacement diff receives the space.
export default function GitHubSourcePanel({ skill }) {
  const source = useSkillSource(skill.id);
  const draft = useSkillDraft(skill.id);
  const check = useCheckSkillSource(skill.id);
  const stage = useStageSkillSourceUpdate(skill.id);
  const [previewId, setPreviewId] = useState(null);
  const [error, setError] = useState('');
  const [staged, setStaged] = useState(false);
  const key = useRef(null);
  const preview = useGitHubPreview(previewId, skill.id);
  const state = usePreviewState();
  const navigate = useNavigate();
  const busy = check.isPending || stage.isPending || ['queued', 'running', 'cancelling'].includes(preview.data?.status);
  const openDraft = () => navigate(paths.dashboard.skills.edit(skill.id));
  const start = async () => {
    setError('');
    const revision = source.data.revision;
    if (!key.current || key.current.revision !== revision) key.current = { revision, id: crypto.randomUUID() };
    try {
      const ack = await check.mutateAsync({ expectedSourceRevision: revision, idempotencyKey: key.current.id });
      setPreviewId(ack.id); setStaged(false); key.current = null;
    } catch (failure) { setError(failure.response?.data?.message || 'Could not check this source. Retry without changing your draft.'); }
  };
  const accept = async ({ rights, acceptReplacement }) => {
    setError('');
    try {
      const value = preview.data;
      await stage.mutateAsync({ previewId, revision: 0, expectedSourceRevision: value.sourceRevision,
        expectedLatestVersion: value.expectedLatestVersion, expectedLatestDigest: value.expectedLatestDigest,
        acceptReplacement, rights });
      setStaged(true);
    } catch (failure) {
      setError(failure.response?.data?.message || 'Could not stage the replacement. Reload the source and draft before retrying.');
      draft.refetch(); source.refetch();
    }
  };

  if (state === 'loading' || source.isPending) return <ContentSkeleton variant="rows" rows={3} label="Loading GitHub source" />;
  if (state === 'empty' || source.error?.response?.data?.code === 'source_not_found') {
    return <EmptyState title="No adopted GitHub source" description="A source is adopted only after its reviewed draft is published." />;
  }
  if (state === 'error' || source.isError) return <ErrorState title="Could not load the source" detail="Check workspace access and try again." onRetry={source.refetch} />;
  const value = source.data;
  return <Stack spacing={2} sx={{ minWidth: 0 }}>
    <Section title="GitHub source" description="Updates become reviewed replacement drafts, never automatic releases." actions={
      <Button variant="outlined" size="small" onClick={start} disabled={busy || value.refKind === 'commit'}>Check for updates</Button>
    }>
      <Typography variant="s1" component="div" sx={{ overflowWrap: 'anywhere' }}>{value.repo} · {value.path || 'Repository root'}</Typography>
      <Typography variant="s3" component="div" color="text.secondary">{value.refKind === 'commit' ? 'Source is pinned' : `Tracked ${value.refKind}: ${value.ref}`}</Typography>
      <Typography variant="s1" component="code" sx={{ display: 'block', overflowWrap: 'anywhere', userSelect: 'all', mt: 1 }}>{value.importedSha}</Typography>
      <Typography variant="s3" component="div" color="text.secondary" sx={{ mt: 1 }}>
        {value.checkedAt ? `Last checked ${new Date(value.checkedAt).toLocaleString()}` : 'Not checked for updates yet'}
      </Typography>
    </Section>
    {error && <Alert severity="error">{error}</Alert>}
    {draft.isError && <ErrorState title="Could not check your draft" detail="Reload before staging so an existing draft is not replaced." onRetry={draft.refetch} />}
    {draft.data && <Button variant="outlined" size="small" onClick={openDraft}>Open your draft</Button>}
    {previewId && preview.isPending && <ContentSkeleton variant="rows" rows={3} label="Checking source updates" />}
    {preview.isError && <ErrorState title="Could not load the update preview" onRetry={preview.refetch} />}
    {staged ? <Stack spacing={1}>
      <Alert severity="info">Replacement draft staged. Nothing was published or promoted.</Alert>
      <Button variant="contained" onClick={openDraft}>Review and test changes</Button>
    </Stack> : preview.data && <GitHubPreviewReview key={preview.data.id} preview={preview.data} onStage={accept}
      busy={busy || draft.isPending || draft.isError} hasDraft={Boolean(draft.data)} />}
  </Stack>;
}

GitHubSourcePanel.propTypes = { skill: PropTypes.object.isRequired };
