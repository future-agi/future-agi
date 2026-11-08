import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Button, Checkbox, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { ClosableDialog } from "src/components/custom-dialog";
import CompatibilityInventory from "../library/CompatibilityInventory";
import { AuthoringError, useAuthoringOperation, useExpired } from "../editor/HotfixHeadReview";

// Intent: safely create one standing-context draft from reviewed retained bytes.
// Hierarchy: source choice, complete inventory, then explicit rights/identity confirmation.
// Palette/depth/surfaces: shared dialog and neutral fields, warning for unsupported input.
// Typography/spacing: m3/s1/s3 with compact 12px groups; no extra onboarding layer.
function ImportContext({ scopeKey, open, onClose, onPreview, onImport, onCreate, onCreated }) {
  const [mode, setMode] = useState("upload");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [body, setBody] = useState("");
  const [preview, setPreview] = useState(null);
  const [rootKey, setRootKey] = useState("");
  const [rights, setRights] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const operation = useAuthoringOperation(scopeKey);
  const expired = useExpired(preview?.expiresAt);
  const roots = (preview?.roots || []).map((root) => ({ ...root, blocked: Boolean(root.blocked || root.checks?.summary?.errors || root.kind !== "agent_context") }));
  const root = roots.find((entry) => entry.key === rootKey);
  const nameValid = /^[a-z0-9]+(-[a-z0-9]+)*$/.test(name) && name.length <= 64;
  const descriptionValid = Boolean(description.trim()) && description.length <= 1024;
  const upload = async (files) => {
    setPreview(null); setRootKey(""); setRights(false); setUploadError(null);
    const list = Array.from(files || []);
    if (!list.length) return;
    if (list.length > 200 || list.some((file) => file.size > 10 * 1024 * 1024)) { setUploadError("Upload at most 200 files; each ZIP must be at most 10 MiB. The server checks complete archive and compiled limits."); return; }
    const form = new FormData();
    list.forEach((file) => form.append("files", file, file.webkitRelativePath || file.name));
    const result = await operation.run(() => onPreview(form), { action: "preview", selection: crypto.randomUUID() });
    if (result) setPreview(result);
  };
  const input = mode === "upload" ? { previewId: preview?.previewId, rootKey, name, description, tags: [], rightsConfirmed: true } : { name, description, tags: [], files: [{ path: "AGENTS.md", content: body }] };
  const canSubmit = nameValid && descriptionValid && (mode === "upload" ? root && !root.blocked && !preview.closure?.blocked && (!expired || operation.canRetry(input)) && rights && Boolean(onImport) : Boolean(body.trim()) && new TextEncoder().encode(body).length <= 512 * 1024 && Boolean(onCreate));
  const submit = async () => {
    if (!canSubmit) return;
    setSubmitted(true);
    const result = await operation.run(mode === "upload" ? onImport : onCreate, input);
    if (result) onCreated?.(result);
  };
  const locked = operation.pending || submitted;
  return <ClosableDialog open={open} onClose={(...args) => { if (!operation.pending) onClose(...args); }} fullWidth maxWidth="md" aria-labelledby="context-import-title">
    <DialogTitle id="context-import-title">Create agent context</DialogTitle>
    <DialogContent><Stack spacing={1.5} sx={{ pt: 1, minWidth: 0 }}>
      <Typography variant="s1">AGENTS.md is standing context. Skills load when needed. Import creates a private draft; production and source applications stay unchanged.</Typography>
      <AuthoringError error={operation.error} />
      {uploadError && <Alert severity="error">{uploadError}</Alert>}
      <TextField select size="small" label="Start from" value={mode} disabled={locked} onChange={(event) => { setMode(event.target.value); setRights(false); }}><MenuItem value="upload">Folder or ZIP</MenuItem><MenuItem value="write">Write or paste AGENTS.md</MenuItem></TextField>
      {mode === "upload" ? <>
        <Stack direction={{ xs: "column", sm: "row" }} spacing={1}>
          <Button component="label" variant="outlined" disabled={locked || !onPreview}>Choose ZIP or files<input type="file" hidden multiple onChange={(event) => upload(event.target.files)} /></Button>
          <Button component="label" variant="outlined" disabled={locked || !onPreview}>Choose folder<input type="file" hidden multiple webkitdirectory="" onChange={(event) => upload(event.target.files)} /></Button>
        </Stack>
        {preview && <>
          <Typography variant="s3">Retained preview {preview.previewId} · expires {preview.expiresAt}</Typography>
          {expired && <Alert severity="warning">The retained upload expired. Choose the source again to review fresh bytes.</Alert>}
          <CompatibilityInventory items={roots} selectedKeys={rootKey ? [rootKey] : []} onSelectionChange={(keys) => { setRootKey(keys.at(-1) || ""); setRights(false); }} readOnly={locked} rejected={preview.rejected} skipped={preview.skipped} closure={preview.closure} />
          <FormControlLabel control={<Checkbox checked={rights} disabled={locked} onChange={(event) => setRights(event.target.checked)} />} label="I have the rights to import this exact selected tree and its notices." />
        </>}
      </> : <TextField label="AGENTS.md" size="small" fullWidth multiline minRows={10} value={body} disabled={locked} onChange={(event) => setBody(event.target.value)} helperText="One root instruction file; no YAML skill frontmatter is required." />}
      <TextField label="Immutable name" size="small" value={name} disabled={locked} onChange={(event) => setName(event.target.value)} inputProps={{ maxLength: 64 }} error={Boolean(name) && !nameValid} helperText="Lowercase letters, digits and single hyphens. Names are shared with Skills in this workspace." />
      <TextField label="Purpose" size="small" fullWidth multiline minRows={2} value={description} disabled={locked} onChange={(event) => setDescription(event.target.value)} inputProps={{ maxLength: 1024 }} helperText="Versioned with the context; not a tool authorization policy." />
      {submitted && operation.error && <Alert severity="info">The submitted identity and bytes are locked for a same-operation retry. Do not create another draft to work around an unknown response.</Alert>}
    </Stack></DialogContent>
    <DialogActions><Button disabled={operation.pending} onClick={onClose}>Cancel</Button><Button variant="contained" onClick={submit} disabled={!canSubmit || operation.pending || Boolean(operation.result)}>{submitted && operation.error ? "Retry same operation" : mode === "upload" ? "Import as draft" : "Create draft"}</Button></DialogActions>
  </ClosableDialog>;
}
ImportContext.propTypes = { scopeKey: PropTypes.string.isRequired, open: PropTypes.bool.isRequired, onClose: PropTypes.func.isRequired, onPreview: PropTypes.func, onImport: PropTypes.func, onCreate: PropTypes.func, onCreated: PropTypes.func };
export default function ContextImportDialog(props) { return <ImportContext key={`${props.scopeKey}:${props.open}`} {...props} />; }
ContextImportDialog.propTypes = ImportContext.propTypes;
