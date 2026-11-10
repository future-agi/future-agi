import { useCallback, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { Alert, Button, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { paths } from 'src/routes/paths';
import { useMigrationDecision, useMigrationPreview } from 'src/api/skills/migration';
import { useSkillV11Scope } from 'src/api/skills/testing';
import ContentSkeleton from 'src/components/content-skeleton';
import MigrationPlanPanel from './MigrationPlanPanel';

// Intent: trial a private library as retained drafts, never move production labels.
// Hierarchy: bounded source/identity input, exact review, explicit publication handoff.
export default function RetainedMigrationPanel({ onBusyChange, onClose }) {
  const navigate = useNavigate();
  const { identity } = useSkillV11Scope();
  const preview = useMigrationPreview();
  const decision = useMigrationDecision();
  const folder = useRef(null);
  const upload = useRef(null);
  const [kind, setKind] = useState('langfuse');
  const [files, setFiles] = useState([]);
  const [identities, setIdentities] = useState('');
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState('');
  const [locked, setLocked] = useState(false);
  const [receipts, setReceipts] = useState([]);
  const busy = locked || preview.isPending;
  const selectFiles = (next) => { setFiles(Array.from(next)); setPlan(null); setError(''); setReceipts([]); };
  const prepare = async () => {
    setError('');
    setPlan(null);
    onBusyChange(true);
    try {
      let input;
      if (kind === 'langfuse') {
        if (files.length !== 1 || files[0].size > 20 * 1024 * 1024) throw new Error('Choose one versioned exchange JSON file, at most 20 MiB.');
        input = { exportData: JSON.parse(await files[0].text()) };
      } else {
        const form = new FormData();
        files.forEach((file) => form.append('files', file, file.webkitRelativePath || file.name));
        if (identities.trim()) form.append('source_identities', JSON.stringify(JSON.parse(identities)));
        input = { form };
      }
      setPlan(await preview.mutateAsync(input));
      setReceipts([]);
    } catch (failure) { setError(failure.message || 'Could not retain this source. No content was imported.'); }
    finally { onBusyChange(false); }
  };
  const openDraft = (resourceId) => { onClose(); navigate(paths.dashboard.skills.edit(resourceId)); };
  const setOperationBusy = useCallback((value) => { setLocked(value); onBusyChange(value); }, [onBusyChange]);
  return <Stack spacing={1.5} sx={{ minWidth: 0, overflowY: 'auto' }}>
    <Typography variant="s1">Run the local compatibility inventory first. This review does not issue runtime credentials, change the source, move production labels or certify native delivery.</Typography>
    <TextField select size="small" label="Retained source format" value={kind} disabled={busy} onChange={(event) => { setKind(event.target.value); selectFiles([]); }}>
      <MenuItem value="langfuse">Versioned Langfuse exchange JSON</MenuItem><MenuItem value="folder">Private folder or ZIP</MenuItem>
    </TextField>
    <Alert severity="info">Langfuse intake supports the explicit langfuse-skills-v1 exchange schema, not arbitrary native exports or remote URLs. Local-folder identities must come from the local inventory; without them a later import cannot be matched by immutable source identity.</Alert>
    <Stack direction="row" spacing={1}>
      <Button variant="outlined" disabled={busy} onClick={() => upload.current?.click()}>Choose source files</Button>
      {kind === 'folder' && <Button variant="outlined" disabled={busy} onClick={() => folder.current?.click()}>Choose private folder</Button>}
    </Stack>
    <input ref={upload} type="file" hidden multiple={kind === 'folder'} aria-label="Retained source files" disabled={busy} onChange={(event) => { selectFiles(event.target.files); event.target.value = ''; }} />
    <input ref={folder} type="file" hidden multiple webkitdirectory="" aria-label="Retained private folder" disabled={busy} onChange={(event) => { selectFiles(event.target.files); event.target.value = ''; }} />
    <Typography variant="s3">{files.length} source files selected</Typography>
    {kind === 'folder' && <TextField label="Immutable source identities JSON" multiline minRows={2} size="small" value={identities} disabled={busy} onChange={(event) => { setIdentities(event.target.value); setPlan(null); }} helperText="Inventory package key → origin, project, resource, revision and digest. Names are never used as migration identities." />}
    <Button variant={plan ? 'outlined' : 'contained'} disabled={busy || !files.length} onClick={prepare}>Preview retained migration</Button>
    {error && <Alert severity="error">{error}</Alert>}
    {preview.isPending && <ContentSkeleton variant="rows" rows={3} label="Retaining source and loading exact comparisons" />}
    {plan && <MigrationPlanPanel scopeKey={identity} plan={plan}
      onAccept={(input, options) => decision.mutateAsync({ ...input, ...options })}
      onKeep={(input, options) => decision.mutateAsync({ ...input, ...options, decision: 'keep' })}
      onPreserveDraft={(item) => openDraft(item.destinationId)} onBusyChange={setOperationBusy}
      onAccepted={(receipt) => setReceipts((previous) => [...previous, receipt])} />}
    {receipts.map((receipt) => <Alert severity="success" key={receipt.receiptId} action={<Button disabled={busy} onClick={() => openDraft(receipt.resourceId)}>Open destination</Button>}>Decision {receipt.decision} retained. {receipt.published ? 'Published' : 'Not published; review and publish deliberately in the editor.'}</Alert>)}
  </Stack>;
}
RetainedMigrationPanel.propTypes = { onBusyChange: PropTypes.func.isRequired, onClose: PropTypes.func.isRequired };
