import { useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink } from 'react-router-dom';
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography } from '@mui/material';
import { useCreateGitHubPreview, useGitHubPreview, useStageGitHubImport } from 'src/api/skills/github';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import Section from 'src/components/section';
import StatusBadge from 'src/components/status-badge';
import ChecksList from 'src/sections/skills/components/ChecksList';
import TextDiff from 'src/sections/skills/components/TextDiff';
import { usePreviewState } from 'src/sections/skills/components/SkillsStatePreview';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import { paths } from 'src/routes/paths';

const ACTIVE = ['queued', 'running', 'cancelling'];
const OUTCOMES = {
  ready: 'Ready to review', changed: 'Changes available', unchanged_commit: 'Already at this commit',
  unchanged_content: 'Upstream changed, skill files unchanged', pinned: 'Source is pinned',
  source_missing: 'Source unavailable', blocked: 'Import blocked', error: 'Could not check', needs_path: 'Choose one skill folder',
};

// The reviewed immutable SHA leads; files, rights and replacement follow it.
export function GitHubPreviewReview({ preview, onStage, busy, hasDraft = false, onContinue }) {
  const [rights, setRights] = useState(false);
  const [reviewNote, setReviewNote] = useState('');
  const [replacement, setReplacement] = useState(false);
  const [chosenRoot, setChosenRoot] = useState('');
  const [selectedPath, setSelectedPath] = useState('');
  const changedFiles = preview.diff ? preview.diff.files.filter((file) => file.status !== 'unchanged') : [];
  const selectedFile = changedFiles.find((file) => file.path === selectedPath) || changedFiles[0];
  const active = ACTIVE.includes(preview.status);
  const expired = preview.expiresAt && new Date(preview.expiresAt).getTime() <= Date.now();
  const mayStage = preview.canStage && !expired && !hasDraft && rights &&
    (!preview.license.requiresAdminReview || reviewNote.trim()) && (!preview.localDiverged || replacement);

  return (
    <Stack spacing={2} sx={{ minWidth: 0 }}>
      <Box role="status" aria-live="polite">
        <StatusBadge status={preview.status === 'completed' ? 'draft' : preview.status} label={active ? 'Reading the public source' : OUTCOMES[preview.outcome] || 'Source check finished'} />
      </Box>
      {active && <ContentSkeleton variant="rows" rows={3} label="Reading GitHub source" />}
      {preview.error && <Alert severity="error">{preview.error.code === 'github_archive_too_large'
        ? 'This repository exceeds the archive limit. A narrower path does not reduce the download. Upload a ZIP of the folder instead.'
        : preview.error.message}</Alert>}
      {preview.retryAt && <Typography variant="s3">Try again after {new Date(preview.retryAt).toLocaleString()}.</Typography>}
      {expired && <Alert severity="warning">This preview expired. Preview the source again before importing.</Alert>}
      {preview.source.commitSha && <Section title="Reviewed source" description="The draft will use this exact commit, not a moving branch.">
        <Typography variant="s1" component="div" sx={{ overflowWrap: 'anywhere' }}>{preview.source.repo} · {preview.source.path || 'Repository root'}</Typography>
        <Typography variant="s3" component="div" color="text.secondary">Tracked ref: {preview.source.ref} ({preview.source.refKind})</Typography>
        <Typography variant="s1" component="code" sx={{ display: 'block', overflowWrap: 'anywhere', userSelect: 'all', mt: 1 }}>{preview.source.commitSha}</Typography>
      </Section>}
      {preview.outcome === 'needs_path' && onContinue && <Stack spacing={1}>
        <TextField select size="small" label="Skill folder" value={chosenRoot} onChange={(event) => setChosenRoot(event.target.value)} disabled={busy}>
          {preview.roots.map((root) => <MenuItem key={root} value={root}>{root || 'Repository root'}</MenuItem>)}
        </TextField>
        <Button variant="outlined" disabled={busy || !chosenRoot || expired} onClick={() => onContinue(chosenRoot)}>Preview this folder at the same commit</Button>
      </Stack>}
      {preview.bundle && <>
        <Section title={`Files in ${preview.bundle.name}`} description="Scripts are stored as text and are never run during import.">
          <Table size="small" aria-label="Reviewed GitHub files" sx={{ tableLayout: 'fixed', width: 1 }}>
            <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>File</TableCell><TableCell align="right">Bytes</TableCell></TableRow></TableHead>
            <TableBody>{preview.bundle.files.map((file) => <TableRow key={file.path} hover sx={{ height: ROW_HEIGHT.default }}>
              <TableCell sx={{ overflowWrap: 'anywhere' }} title={`SHA-256 ${file.sha256}`}>{file.path}</TableCell>
              <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>{file.sizeBytes.toLocaleString()}</TableCell>
            </TableRow>)}</TableBody>
          </Table>
          <ChecksList checks={preview.bundle.checks} dense />
        </Section>
        <Section title="License and notices" description="Public availability does not grant permission to reuse these files.">
          <Typography variant="s1">{preview.license.declared || 'No declared license'} · {preview.license.status}</Typography>
          {preview.license.files.map((file) => <Box key={file.path} component="details" sx={{ mt: 1 }}>
            <Typography component="summary" variant="s1" sx={{ cursor: 'pointer', overflowWrap: 'anywhere' }}>{file.path}</Typography>
            <Typography component="pre" variant="s3" sx={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: 240, overflow: 'auto', p: 1.5, bgcolor: 'action.hover' }}>{file.text}</Typography>
          </Box>)}
          {preview.license.requiresAdminReview && <Alert severity="warning" sx={{ mt: 1 }}>A workspace or organization admin must record a rights-review note before staging.</Alert>}
          {preview.license.status === 'restricted' && <Alert severity="error" sx={{ mt: 1 }}>These terms block import. Rights confirmation does not override restricted terms.</Alert>}
        </Section>
      </>}
      {changedFiles.length > 0 && <Section title="Replacement diff" description="These files replace the local latest version; changes are not merged.">
        <TextField select fullWidth size="small" label="Changed file" value={selectedFile.path} onChange={(event) => setSelectedPath(event.target.value)}>
          {changedFiles.map((file) => <MenuItem value={file.path} key={file.path}>{file.path} · {file.status}</MenuItem>)}
        </TextField>
        <TextDiff fromText={selectedFile.fromText} toText={selectedFile.toText} fromTitle={`Local v${preview.expectedLatestVersion}`} toTitle="Reviewed upstream" />
      </Section>}
      {hasDraft && <Alert severity="warning">Your draft already exists. Open or discard it before staging an update.</Alert>}
      {preview.canStage && !preview.consumed && <Stack spacing={1}>
        {preview.localDiverged && <FormControlLabel control={<Checkbox checked={replacement} onChange={(event) => setReplacement(event.target.checked)} disabled={busy} />} label="Replace my reviewed local changes. They will not be merged." />}
        <FormControlLabel control={<Checkbox checked={rights} onChange={(event) => setRights(event.target.checked)} disabled={busy} />} label="I confirm that I have the rights to import these files." />
        <TextField size="small" multiline minRows={2} label={preview.license.requiresAdminReview ? 'Rights-review note (required for admins)' : 'Rights-review note'} value={reviewNote} inputProps={{ maxLength: 2000 }} onChange={(event) => setReviewNote(event.target.value)} disabled={busy} />
        <Button variant="contained" disabled={busy || !mayStage} onClick={() => onStage({ rights: { confirmed: true, reviewNote }, acceptReplacement: replacement })}>
          {busy ? 'Staging draft…' : preview.operation === 'update' ? 'Stage replacement draft' : 'Import as draft'}
        </Button>
        <Typography variant="s3" color="text.secondary">No version is published and no label moves. Review and test the draft before publishing.</Typography>
      </Stack>}
      {preview.consumed && <Stack spacing={1}>
        <Alert severity="info">Already staged as draft r{preview.consumed.draftRevision}. This preview will not overwrite later edits.</Alert>
        <Button variant="outlined" component={RouterLink} to={paths.dashboard.skills.edit(preview.consumed.skillId)}>Open staged draft</Button>
      </Stack>}
    </Stack>
  );
}

GitHubPreviewReview.propTypes = {
  preview: PropTypes.object.isRequired, onStage: PropTypes.func.isRequired, busy: PropTypes.bool,
  hasDraft: PropTypes.bool, onContinue: PropTypes.func,
};

export default function GitHubImportPanel({ onStaged }) {
  const [url, setUrl] = useState('');
  const [path, setPath] = useState('');
  const [ref, setRef] = useState('');
  const [previewId, setPreviewId] = useState(null);
  const [error, setError] = useState('');
  const key = useRef(null);
  const create = useCreateGitHubPreview();
  const stage = useStageGitHubImport();
  const preview = useGitHubPreview(previewId);
  const state = usePreviewState();
  const busy = create.isPending || stage.isPending || ACTIVE.includes(preview.data?.status);

  const changeUrl = (value) => {
    setUrl(value); setPreviewId(null); setError('');
    try {
      const parsed = new URL(value);
      const parts = parsed.pathname.split('/').filter(Boolean);
      if (parsed.origin === 'https://github.com' && ['tree', 'blob'].includes(parts[2]) && parts[3]) {
        setRef(decodeURIComponent(parts[3]));
        setPath(parts.slice(4, parts[2] === 'blob' ? -1 : undefined).map(decodeURIComponent).join('/'));
      }
    } catch { /* Incomplete pasted input is validated when previewing. */ }
  };
  const start = async (input) => {
    setError('');
    const signature = JSON.stringify(input);
    if (!key.current || key.current.signature !== signature) key.current = { signature, id: crypto.randomUUID() };
    try {
      const ack = await create.mutateAsync({ ...input, idempotencyKey: key.current.id });
      setPreviewId(ack.id); key.current = null;
    } catch (failure) { setError(failure.response?.data?.message || 'Could not preview this source. Check the URL, folder and ref, then retry.'); }
  };
  const accept = async ({ rights }) => {
    setError('');
    try { onStaged(await stage.mutateAsync({ previewId, rights })); }
    catch (failure) {
      setError(failure.response?.data?.message || 'Could not confirm staging. Reloading the preview receipt; your inputs are preserved.');
      preview.refetch();
    }
  };

  if (state === 'loading') return <ContentSkeleton variant="rows" rows={4} label="Loading GitHub import" />;
  if (state === 'error') return <ErrorState title="Could not preview GitHub" detail="Check the public repository URL and try again." />;
  return <Stack spacing={2} sx={{ minWidth: 0 }}>
    <Section title="Import a public GitHub skill" description="Review a pinned commit and its license, then create an unpublished draft." />
    {state === 'empty' && <EmptyState title="Choose a public skill repository" description="Paste a repository URL to review its files and license." />}
    <TextField fullWidth size="small" label="Public repository URL" value={url} onChange={(event) => changeUrl(event.target.value)} disabled={busy} placeholder="https://github.com/owner/repository" />
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
      <TextField fullWidth size="small" label="Folder path (optional)" value={path} disabled={busy} onChange={(event) => { setPath(event.target.value); setPreviewId(null); }} />
      <TextField fullWidth size="small" label="Branch, tag or full commit (optional)" value={ref} disabled={busy} onChange={(event) => { setRef(event.target.value); setPreviewId(null); }} />
    </Stack>
    <Typography variant="s3" color="text.secondary">For tree links, confirm the fields above. A branch containing “/” needs its full name; use refs/tags/name for a tag.</Typography>
    {error && <Alert severity="error">{error}</Alert>}
    <Button variant="outlined" disabled={busy || !url.trim()} onClick={() => start({ url: url.trim(), path, ref: ref || null })}>{create.isPending ? 'Starting preview…' : 'Preview source'}</Button>
    {previewId && preview.isPending && <ContentSkeleton variant="rows" rows={3} label="Loading source preview" />}
    {preview.isError && <ErrorState title="Could not load the preview" detail="Your inputs are preserved. Retry or preview the source again." onRetry={preview.refetch} />}
    {preview.data && <GitHubPreviewReview key={preview.data.id} preview={preview.data} busy={busy} onStage={accept} onContinue={(root) => start({ parentPreviewId: preview.data.id, path: root })} />}
  </Stack>;
}

GitHubImportPanel.propTypes = { onStaged: PropTypes.func.isRequired };
