import { useCallback, useEffect, useId, useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { createPortal } from 'react-dom';
import { Alert, Box, Button, Link, MenuItem, Stack, TextField, Typography } from '@mui/material';
import { useNavigate } from 'react-router-dom';
import { paths } from 'src/routes/paths';
import { useMigrationDecision, useMigrationPreview, useMigrationPublish, useMigrationPlanReload } from 'src/api/skills/migration';
import { useSkillV11Scope } from 'src/api/skills/testing';
import ImportFooter from "./ImportFooter";
import TechnicalDetails from "../components/TechnicalDetails";
import ContentSkeleton from 'src/components/content-skeleton';
import MigrationPlanPanel from './MigrationPlanPanel';
import MigrationBatchReview from './MigrationBatchReview';
import { retainPlan } from './migration-batch';
import { packetSources } from './migration-packet';


const kindOfSource = (input) => (input.exportData ? 'langfuse' : 'folder');
const countOf = (count, one, many) => (count === 1 ? one : `${count} ${many}`);
// "2 sources: a folder and a Langfuse file". When a source came back from an earlier session its kind is unknown, so only the count is said.
function sourcesSummary(plans, planKinds) {
  const kinds = plans.map((plan) => planKinds[plan.id]);
  const total = `${plans.length} ${plans.length === 1 ? 'source' : 'sources'}`;
  if (kinds.some((kind) => !kind)) return total;
  const folders = kinds.filter((kind) => kind === 'folder').length;
  const files = kinds.length - folders;
  return `${total}: ${[folders && countOf(folders, 'a folder', 'folders'), files && countOf(files, 'a Langfuse file', 'Langfuse files')].filter(Boolean).join(' and ')}`;
}

// Intent: trial a private library as retained drafts, never move production labels.
// Hierarchy: bounded source/identity input, exact review, explicit publication handoff.
export default function RetainedMigrationPanel({ onBusyChange, onClose, initialPlanIds, footerTarget, onConsequenceChange }) {
  const navigate = useNavigate();
  const formatId = useId();
  const { identity } = useSkillV11Scope();
  const preview = useMigrationPreview();
  const decision = useMigrationDecision();
  const publish = useMigrationPublish();
  const reload = useMigrationPlanReload();
  const folder = useRef(null);
  const upload = useRef(null);
  const packetInput = useRef(null);
  const [proposal, setProposal] = useState(null);
  const [kind, setKind] = useState('langfuse');
  const [files, setFiles] = useState([]);
  const [identities, setIdentities] = useState('');
  const [plans, setPlans] = useState([]);
  // Which kind of source each retained plan came from, so the summary can name them ("a folder and a Langfuse file").
  const [planKinds, setPlanKinds] = useState({});
  const [sourceSetupOpen, setSourceSetupOpen] = useState(false);
  const plan = plans.at(-1) || null;
  const [error, setError] = useState('');
  const [locked, setLocked] = useState(false);
  // The review's own footer renders into this slot (kept mounted, hidden while sources are being changed).
  const [batchSlot, setBatchSlot] = useState(null);
  const [receipts, setReceipts] = useState([]);
  const busy = locked || preview.isPending || reload.isPending;
  const setupVisible = !plans.length || sourceSetupOpen;
  const reloaded = useRef(false);
  // Reload retained plans after draft preservation: same immutable plan and
  // fingerprint, with live destination and personal-draft state re-read.
  useEffect(() => {
    if (reloaded.current || !initialPlanIds?.length) return;
    reloaded.current = true;
    reload.mutateAsync({ planIds: initialPlanIds }).then((loaded) => setPlans(loaded.reduce(retainPlan, [])))
      .catch((failure) => setError(failure.message || 'Could not reload the retained migration. Preview the source again.'));
  });
  const selectFiles = (next) => { setSourceSetupOpen(true); setFiles(Array.from(next)); setError(''); };
  const prepare = async () => {
    setError('');
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
      const next = await preview.mutateAsync(input);
      setPlanKinds((current) => ({ ...current, [next.id]: kindOfSource(input) }));
      setPlans((current) => retainPlan(current, next));
      setReceipts([]);
      setSourceSetupOpen(false);
    } catch (failure) { setError(failure.message || 'Could not retain this source. No content was imported.'); }
    finally { onBusyChange(false); }
  };
  // One packet from the local inventory replaces choosing each source again
  // and copying identity JSON by hand. Every source is previewed into the same
  // reviewed set; the server re-derives every identity and digest.
  const previewPacket = async (file) => {
    setError('');
    onBusyChange(true);
    try {
      const { sources, proposal: next } = await packetSources(file);
      let retained = [];
      const kinds = {};
      for (const source of sources) {
        const next = await preview.mutateAsync(source);
        kinds[next.id] = kindOfSource(source);
        retained = retainPlan(retained, next);
      }
      setPlanKinds((current) => ({ ...current, ...kinds }));
      setPlans((current) => retained.reduce(retainPlan, current));
      setProposal(next);
      setReceipts([]);
      setSourceSetupOpen(false);
    } catch (failure) { setError(failure.message || 'Could not retain this packet. No content was imported.'); }
    finally { onBusyChange(false); }
  };
  const openDraft = (resourceId) => { onClose(); navigate(paths.dashboard.skills.edit(resourceId)); };
  const setOperationBusy = useCallback((value) => { setLocked(value); onBusyChange(value); }, [onBusyChange]);
  // One footer for every Import tab: the reason, Cancel, then Preview import (LIB2). While a reviewed
  // set is open the review's own footer takes this slot instead; it stays mounted so nothing resets.
  const footer = <>
    <Box ref={setBatchSlot} hidden={setupVisible} />
    {setupVisible && <ImportFooter enabled={!busy && files.length > 0} reason={files.length ? '' : 'Choose a file or folder first'} busy={busy} onPrimary={prepare} onCancel={onClose} />}
  </>;
  return <>
  <Stack spacing={1.5} sx={{ minWidth: 0, minHeight: 0, overflowY: 'auto', pb: 2, flexShrink: 1 }}>
    <Stack direction={{ xs: "column", sm: "row" }} alignItems={{ sm: "center" }} justifyContent="space-between" spacing={1}>
    {plans.length > 0 && !sourceSetupOpen && <Stack direction="row" alignItems="center" spacing={1}><Typography variant="s1">{sourcesSummary(plans, planKinds)}</Typography><Button size="small" disabled={busy} onClick={() => setSourceSetupOpen(true)}>Change sources</Button>
    </Stack>}
    {plans.length > 0 && sourceSetupOpen && <Button size="small" disabled={busy} onClick={() => setSourceSetupOpen(false)} sx={{ alignSelf: 'flex-start', ml: -1 }}>Back to review</Button>}
    {(!plans.length || sourceSetupOpen) && <TextField id={formatId} SelectProps={{ SelectDisplayProps: { 'aria-labelledby': `${formatId}-label` } }} select size="small" label="Source" sx={{ minWidth: { sm: 300 } }} value={kind} disabled={busy} onChange={(event) => { setKind(event.target.value); selectFiles([]); }}>
      <MenuItem value="langfuse">Langfuse export file (JSON)</MenuItem><MenuItem value="folder">Folder</MenuItem>
    </TextField>}
    </Stack>
    <Box hidden={plans.length > 0 && !sourceSetupOpen}><Stack spacing={1.5}>
    <Typography variant="s1">Bring skills from Langfuse or a local folder. Nothing is published until you review each one.</Typography>
    {kind === 'langfuse' && <Typography variant="s3" color="text.secondary">Use a Langfuse skills export file.{' '}<Link href="https://docs.futureagi.com/docs/skills/migration#before-migration" target="_blank" rel="noopener noreferrer">How to prepare the file</Link></Typography>}
    <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
      <Button variant="outlined" disabled={busy} onClick={() => upload.current?.click()}>Choose export file</Button>
      <Button variant="outlined" disabled={busy} onClick={() => folder.current?.click()}>Choose folder</Button>
    </Stack>
    <TechnicalDetails title="Supported formats">
      <Stack spacing={1}>
        <Typography variant="s3">Langfuse files use the langfuse-skills-v1 format. Native Langfuse exports need converting first, and remote URLs aren&apos;t read.</Typography>
        <Typography variant="s3">A local folder needs the list made with the command line; without it, a later import can&apos;t be matched to the same source.</Typography>
        <Typography variant="s3">A list made with the skills-inventory command (--packet) holds every source it found, where each came from, and the proposed context. Choosing it previews them all.</Typography>
        <Box><Button variant="outlined" disabled={busy} onClick={() => packetInput.current?.click()}>Choose list file</Button></Box>
      </Stack>
    </TechnicalDetails>
    <input ref={packetInput} type="file" hidden accept="application/json,.json" aria-label="Inventory migration packet" disabled={busy} onChange={(event) => { const [file] = event.target.files; event.target.value = ''; if (file) previewPacket(file); }} />
    <input ref={upload} type="file" hidden multiple={kind === 'folder'} aria-label="Retained source files" disabled={busy} onChange={(event) => { selectFiles(event.target.files); event.target.value = ''; }} />
    {/* Choosing a folder is itself the format choice; no separate selection is needed. */}
    <input ref={folder} type="file" hidden multiple webkitdirectory="" aria-label="Retained private folder" disabled={busy} onChange={(event) => { setKind('folder'); selectFiles(event.target.files); event.target.value = ''; }} />
    <Typography variant="s3">{files.length} source files selected</Typography>
    {kind === 'folder' && <TechnicalDetails title="Source identity"><TextField label="Immutable source identities JSON" multiline minRows={2} size="small" value={identities} disabled={busy} onChange={(event) => setIdentities(event.target.value)} helperText="Inventory package key → origin, project, resource, revision and digest. Names are never used as migration identities." /></TechnicalDetails>}
    </Stack></Box>
    {error && <Alert severity="error">{error}</Alert>}
    {reload.isPending && <ContentSkeleton variant="rows" rows={3} label="Reloading the retained migration review" />}
    {preview.isPending && <ContentSkeleton variant="rows" rows={3} label="Retaining source and loading exact comparisons" />}
    {plans.length > 0 && <Box hidden={sourceSetupOpen}><MigrationBatchReview onClose={onClose} footerTarget={batchSlot} onConsequenceChange={onConsequenceChange} plans={plans} contextProposal={proposal}
      onAccept={(input, options) => decision.mutateAsync({ ...input, ...options })}
      onKeep={(input, options) => decision.mutateAsync({ ...input, ...options, decision: 'keep' })}
      onPublish={(input) => publish.mutateAsync(input)}
      onPreserveDraft={(item) => { onClose(); navigate(`${paths.dashboard.skills.edit(item.destinationId)}?preserve=migration`, { state: { reopenMigration: { planIds: plans.map((entry) => entry.id) } } }); }}
      onBusyChange={setOperationBusy}
      onCreateContext={(links) => { onClose(); navigate(paths.dashboard.skills.contexts, { state: { proposedLinks: links } }); }} /></Box>}
    {plan && <Box hidden={sourceSetupOpen}><TechnicalDetails title="Decide one item at a time" keepMounted><MigrationPlanPanel scopeKey={identity} plan={plan}
      onAccept={(input, options) => decision.mutateAsync({ ...input, ...options })}
      onKeep={(input, options) => decision.mutateAsync({ ...input, ...options, decision: 'keep' })}
      onPreserveDraft={(item) => { onClose(); navigate(`${paths.dashboard.skills.edit(item.destinationId)}?preserve=migration`); }} onBusyChange={setOperationBusy}
      onAccepted={(receipt) => setReceipts((previous) => [...previous, receipt])} /></TechnicalDetails></Box>}
    {receipts.map((receipt) => <Alert severity="success" key={receipt.receiptId} action={<Button disabled={busy} onClick={() => openDraft(receipt.resourceId)}>Open destination</Button>}>Decision {receipt.decision} retained. {receipt.published ? 'Published' : 'Not published; review and publish deliberately in the editor.'}</Alert>)}
  </Stack>
  {footerTarget ? createPortal(footer, footerTarget) : footer}
  </>;
}
RetainedMigrationPanel.propTypes = { footerTarget: PropTypes.any, onConsequenceChange: PropTypes.func, onBusyChange: PropTypes.func.isRequired, onClose: PropTypes.func.isRequired, initialPlanIds: PropTypes.arrayOf(PropTypes.string) };
