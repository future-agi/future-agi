import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { AuthoringError, FileChanges, useAuthoringOperation, useExpired } from "./HotfixHeadReview";

// Intent: preserve an author's work before switching or restoring; compare rather than merge.
// Hierarchy: local/server identity, export, then explicit checkpoint choice.
// Palette/depth/surfaces: neutral paper/divider, warning only for replacement.
// Typography/spacing: shared m3/s1/s3 and 4px rhythm with 12px groups.
function difference(before, after) {
  const old = new Map(before.map((file) => [file.path, file.content]));
  const next = new Map(after.map((file) => [file.path, file.content]));
  return [...new Set([...old.keys(), ...next.keys()])].map((path) => ({
    path, fromText: old.get(path), toText: next.get(path),
    status: !old.has(path) ? "added" : !next.has(path) ? "removed" : old.get(path) === next.get(path) ? "unchanged" : "modified",
  }));
}

function Recovery({ scopeKey, session, baseFiles = [], serverDraft, checkpoints = [], onSuspend, onRestore, onPreserved, onReload, hasLiveDraft = true, targetVersion }) {
  const [comparison, setComparison] = useState("base");
  const [confirmed, setConfirmed] = useState(false);
  const [selected, setSelected] = useState("");
  const [exportedSequence, setExportedSequence] = useState(null);
  const exported = exportedSequence !== null && exportedSequence === session.editSequence;
  const operation = useAuthoringOperation(scopeKey);
  const checkpoint = checkpoints.find((item) => item.id === selected);
  const expired = useExpired(checkpoint?.expiresAt);
  const restoreInput = { checkpointId: checkpoint?.id, revision: session.revision, expectedDigest: checkpoint?.digest };
  const restoreExpired = expired && !operation.canRetry(restoreInput);
  const exportLocal = () => {
    const data = { schema: 1, baseVersion: session.baseVersion, revision: session.revision, files: session.files, note: session.note, description: session.description, contextSpec: session.contextSpec, resolvedLock: session.resolvedLock };
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url; link.download = "unsent-draft.json";
    document.body.appendChild(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    setExportedSequence(session.editSequence);
  };
  const preserve = async () => {
    if (!confirmed) return;
    const result = await operation.run(async (_, options) => {
      const saved = await session.flush();
      if (!saved) throw Object.assign(new Error("Save conflict"), { code: "draft_conflict" });
      // Server checkpoints and suspends atomically. Starting the selected origin is
      // a separate explicit action in the caller; no overwrite fallback is allowed.
      return onSuspend({ revision: saved.revision, expectedDigest: saved.digest, reason: "switch" }, options);
    }, { action: "suspend", revision: session.revision, editSequence: session.editSequence });
    if (result) onPreserved?.(result);
  };
  const restore = async () => {
    if (!checkpoint || restoreExpired || hasLiveDraft || !confirmed) return;
    const result = await operation.run(onRestore, restoreInput);
    if (result) { session.reload(result); onPreserved?.(result); }
  };
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <Typography variant="m3" component="h2">Preserve your draft</Typography>
    <Typography variant="s1">Your local r{session.revision} · server {serverDraft ? `r${serverDraft.revision}` : "not loaded"}. No automatic merge or overwrite.</Typography>
    <AuthoringError error={operation.error} />
    <Button variant="outlined" onClick={exportLocal}>Download unsent edits</Button>
    {exported && <Typography variant="s3" role="status">Download requested. Keep the private export before leaving this draft.</Typography>}
    <TextField select size="small" label="Compare local changes with" value={comparison} onChange={(event) => setComparison(event.target.value)}>
      <MenuItem value="base">Content origin</MenuItem>
      <MenuItem value="server" disabled={!serverDraft}>Current server draft</MenuItem>
    </TextField>
    <FileChanges files={difference(comparison === "base" ? baseFiles : (serverDraft?.files || []), session.files)} beforeTitle={comparison === "base" ? "Origin" : "Server"} afterTitle="Local" />
    {serverDraft && <Button variant="outlined" disabled={!exported || operation.pending || !onReload} onClick={() => onReload(serverDraft)}>Use server draft after export</Button>}
    {hasLiveDraft ? <>
      <Alert severity="warning">{targetVersion ? `Editing viewed v${targetVersion} requires suspending this draft first. ` : ""}A private checkpoint retains the saved files and accepted links until its expiry. Nothing is published.</Alert>
      <FormControlLabel control={<Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={operation.pending} />} label="Save a recovery checkpoint and suspend my current draft." />
      <Button variant="contained" onClick={preserve} disabled={!confirmed || !onSuspend || operation.pending}>Preserve and suspend draft</Button>
    </> : <>
      <TextField select size="small" label="Recovery checkpoint" value={selected} onChange={(event) => { setSelected(event.target.value); setConfirmed(false); }}>
        <MenuItem value="">Select a checkpoint</MenuItem>
        {checkpoints.map((item) => <MenuItem key={item.id} value={item.id}>r{item.draftRevision} · {item.reason}</MenuItem>)}
      </TextField>
      {!checkpoints.length && <Typography variant="s3">No retained private checkpoints.</Typography>}
      {checkpoint && <Box>
        <Typography variant="s3">Expires {checkpoint.expiresAt}</Typography>
        {expired && <Alert severity="warning">This checkpoint expired. It cannot replace your current state.</Alert>}
      </Box>}
      <FormControlLabel control={<Checkbox checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} disabled={restoreExpired || operation.pending} />} label="Restore this checkpoint as a new draft revision, without changing production." />
      <Button variant="contained" onClick={restore} disabled={!checkpoint || restoreExpired || !confirmed || !onRestore || operation.pending}>Restore private checkpoint</Button>
    </>}
  </Stack>;
}
Recovery.propTypes = { scopeKey: PropTypes.string.isRequired, session: PropTypes.object.isRequired, baseFiles: PropTypes.array, serverDraft: PropTypes.object, checkpoints: PropTypes.array, onSuspend: PropTypes.func, onRestore: PropTypes.func, onPreserved: PropTypes.func, onReload: PropTypes.func, hasLiveDraft: PropTypes.bool, targetVersion: PropTypes.number };
export default function RecoverableDraftChanges(props) { return <Recovery key={props.scopeKey} {...props} />; }
RecoverableDraftChanges.propTypes = Recovery.propTypes;
