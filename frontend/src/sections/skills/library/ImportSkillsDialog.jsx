import { useCallback, useEffect, useRef, useState } from "react";
import { useSkillV11Scope } from "src/api/skills/testing";
import PropTypes from "prop-types";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, Tab, TextField, Typography } from "@mui/material";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { CustomTabs } from "src/components/tabs/tabs";
import GitHubImportPanel from "../sources/GitHubImportPanel";
import { useImportPreview, useImportSkills, useSkill } from "src/api/skills";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import { Upload } from "src/components/upload";
import ChecksList from "src/sections/skills/components/ChecksList";
import { LIMITS } from "src/sections/skills/constants";
import ImportPreviewTable from "./ImportPreviewTable";
import RetainedMigrationPanel from "./RetainedMigrationPanel";

function uploadForm(files) {
  const form = new FormData();
  files.forEach((file) => {
    const relativePath = file.webkitRelativePath || file.path || file.name;
    form.append("files", file, relativePath.replace(/^\.\//, ""));
  });
  return form;
}

function ImportReleaseReview({ bundle, label, snapshot, onReview }) {
  const detail = useSkill(bundle.skillId);
  useEffect(() => {
    if (snapshot || !detail.data || detail.isError) return;
    const pointer = detail.data.labels.find((item) => item.name === label);
    const protectedName = detail.data.protectedLabelNames.includes(label);
    onReview(bundle.key, { label, revision: detail.data.releaseRevision, version: pointer?.version ?? null, canMove: pointer ? pointer.canMove : !protectedName || detail.data.permissions.canManageLabelProtection });
  }, [bundle.key, label, snapshot, detail.data, detail.isError, onReview]);
  if (detail.isError) return <Alert severity="error" action={<Button onClick={detail.refetch}>Retry</Button>}>Could not load release permissions for {bundle.name}.</Alert>;
  if (!snapshot) return <ContentSkeleton variant="rows" rows={1} label={`Loading release state for ${bundle.name}`} />;
  return <Typography variant="s3" color="text.secondary">{bundle.name}: {snapshot.canMove ? `Move ${label} from ${snapshot.version === null ? 'unassigned' : `v${snapshot.version}`} to the imported version (release r${snapshot.revision}).` : `An admin must move protected ${label}. Import without a label to publish an ordinary version.`}</Typography>;
}
ImportReleaseReview.propTypes = { bundle: PropTypes.object.isRequired, label: PropTypes.string.isRequired, snapshot: PropTypes.object, onReview: PropTypes.func.isRequired };

function ImportSkillsDialogContent({ open, onClose }) {
  const navigate = useNavigate();
  const [tab, setTab] = useState("upload");
  const previewMutation = useImportPreview();
  const importMutation = useImportSkills();
  const folderInput = useRef(null);
  const [files, setFiles] = useState([]);
  const [releaseSnapshots, setReleaseSnapshots] = useState({});
  const reviewRelease = useCallback((key, value) => setReleaseSnapshots((current) => ({ ...current, [key]: value })), []);
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState([]);
  const [label, setLabel] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [migrationBusy, setMigrationBusy] = useState(false);
  const busy = migrationBusy || previewMutation.isPending || importMutation.isPending;
  const selectedBundles = preview ? preview.bundles.filter((bundle) => selected.includes(bundle.key)) : [];
  const hasWarnings = selectedBundles.some((bundle) => bundle.checks.summary.warnings > 0);
  const canImport = selectedBundles.length > 0 && selectedBundles.every((bundle) => bundle.status !== "invalid" && bundle.checks.summary.errors === 0) && (!hasWarnings || acknowledged) && (!label || selectedBundles.every((bundle) => !bundle.skillId || (releaseSnapshots[bundle.key]?.label === label && releaseSnapshots[bundle.key]?.canMove)));

  const chooseFiles = (nextFiles) => {
    if (busy) return;
    setFiles(Array.from(nextFiles));
    setPreview(null);
    setReleaseSnapshots({});
    setSelected([]);
    setAcknowledged(false);
    setResult(null);
    setError("");
  };

  const submit = async () => {
    if (busy) return;
    if (result) { onClose(); return; }
    setError("");
    try {
      if (!preview) {
        if (!files.length) return;
        const next = await previewMutation.mutateAsync(uploadForm(files));
        setPreview(next);
        setSelected(next.bundles.filter((bundle) => bundle.status !== "invalid" && bundle.checks.summary.errors === 0).map((bundle) => bundle.key));
        return;
      }
      if (!canImport) return;
      const form = uploadForm(files);
      form.append("selected", JSON.stringify(selected));
      if (label) form.append("expected_release_revisions", JSON.stringify(Object.fromEntries(selectedBundles.map((bundle) => [bundle.key, bundle.skillId ? releaseSnapshots[bundle.key].revision : 0]))));
      form.append("labels", JSON.stringify(label ? [label] : []));
      form.append("acknowledge_warnings", String(acknowledged));
      setResult(await importMutation.mutateAsync(form));
    } catch (_failure) {
      setError(preview ? "Couldn't finish importing. Review the preview and try again." : "Couldn't preview these files. Check the upload limits and file paths, then try again.");
    }
  };

  return (
    <ModalWrapper open={open} onClose={onClose} title="Import skills" modalWidth="880px" paperSx={{ maxWidth: 880 }}
      subTitle={tab !== "upload" ? "Review the source, then import as an unpublished draft." : "Review files and checks before publishing versions to your workspace."}
      isLoading={busy} isValid={Boolean(result) || (preview ? canImport : files.length > 0)}
      onSubmit={submit} actionBtnSx={tab !== "upload" ? { display: "none" } : {}} actionBtnTitle={result ? "Done" : preview ? "Import" : "Preview import"}>
      <CustomTabs value={tab} onChange={(_, value) => setTab(value)} aria-label="Import method"><Tab value="upload" label="Upload" disabled={busy} /><Tab value="github" label="GitHub" disabled={busy} /><Tab value="migration" label="Retained migration" disabled={busy} /></CustomTabs>
      {tab === 'migration' ? <RetainedMigrationPanel onBusyChange={setMigrationBusy} onClose={onClose} /> : tab === 'github' ? <Box sx={{ minWidth: 0, overflowY: 'auto' }}><GitHubImportPanel onStaged={(receipt) => {
        const id = receipt.consumed?.skillId ?? receipt.skill?.id;
        if (id) { onClose(); navigate(`${paths.dashboard.skills.edit(id)}?tab=tests`); }
      }} /></Box> : <Stack spacing={2} sx={{ minWidth: 0, overflowY: "auto" }}>
        {error && <Alert severity="error">{error}</Alert>}
        {!result && <>
          <Upload multiple files={files} disabled={busy} showIllustration={false} hidePreview maxSize={LIMITS.MAX_ZIP_BYTES}
            onDropRejected={() => setError("Some files couldn't be selected. Each ZIP must be 10 MiB or smaller; each text file must be 512 KiB or smaller.")}
            heading="Drop a ZIP or skill files" description="ZIPs up to 10 MiB, or up to 200 loose text files. Include a SKILL.md in each skill folder."
            onDrop={(accepted) => chooseFiles(accepted)} />
          <Stack direction="row" spacing={1} alignItems="center">
            <Button variant="outlined" size="small" disabled={busy} onClick={() => folderInput.current?.click()}>
              Choose folder
            </Button>
            <input ref={folderInput} type="file" hidden multiple webkitdirectory="" aria-label="Choose skill folder"
              disabled={busy} onChange={(event) => { chooseFiles(event.target.files); event.target.value = ""; }} />
            <Typography variant="s3" color="text.secondary">{files.length ? `${files.length} ${files.length === 1 ? "file" : "files"} selected` : "Folder paths are preserved."}</Typography>
          </Stack>
          {previewMutation.isPending && <ContentSkeleton variant="rows" rows={3} label="Checking uploaded skills" />}
          {preview && <>
            {preview.bundles.length > 0 ? <ImportPreviewTable bundles={preview.bundles} selected={selected} disabled={busy} onToggle={(key) => {
              const bundle = preview.bundles.find((item) => item.key === key);
              if (busy || !bundle || bundle.status === "invalid" || bundle.checks.summary.errors > 0) return;
              setSelected((current) => current.includes(key) ? current.filter((value) => value !== key) : [...current, key]);
              setAcknowledged(false);
            }} /> : <Alert severity="warning">No skill folders were found. Include a root SKILL.md in each folder.</Alert>}
            {preview.rejected.length > 0 && <Alert severity="warning">
              {preview.rejected.map((entry, index) => <Typography variant="s3" component="div" key={`${entry.path}-${index}`}>{entry.path}: {entry.reason}</Typography>)}
            </Alert>}
            {preview.skipped.length > 0 && <Typography variant="s3" color="text.secondary">Skipped: {preview.skipped.join(", ")}</Typography>}
            {preview.bundles.filter((bundle) => bundle.checks.items.length > 0).map((bundle) => (
              <Box key={bundle.key}>
                <Typography variant="m3" component="h3">Checks for {bundle.name}</Typography>
                <ChecksList checks={bundle.checks} dense />
              </Box>
            ))}
            <TextField select label="Also label as" size="small" SelectProps={{ displayEmpty: true }} value={label} disabled={busy}
              helperText={label ? `This moves ${label} to each imported version. Agents fetching that label will receive it.` : "Import publishes versions without moving a label."}
              onChange={(event) => { setLabel(event.target.value); setReleaseSnapshots({}); }}>
              <MenuItem value="">None</MenuItem><MenuItem value="staging">Staging</MenuItem><MenuItem value="production">Production</MenuItem>
            </TextField>
            {label && selectedBundles.filter((bundle) => bundle.skillId).map((bundle) => <ImportReleaseReview key={`${bundle.key}:${label}`} bundle={bundle} label={label} snapshot={releaseSnapshots[bundle.key]} onReview={reviewRelease} />)}
            {label === 'production' && selectedBundles.some((bundle) => !bundle.skillId) && <Typography variant="s3" color="text.secondary">New skills protect production by default. An admin must assign it; members can import without a label.</Typography>}
            {hasWarnings && <FormControlLabel control={<Checkbox checked={acknowledged} disabled={busy} onChange={(event) => setAcknowledged(event.target.checked)} />}
              label={<Typography variant="s1">I reviewed the warnings for the selected skills.</Typography>} />}
          </>}
        </>}
        {result && <>
          <Alert severity={result.failed.length > 0 ? "warning" : "success"}>
            {result.created.length} created · {result.updated.length} updated · {result.unchanged.length} up to date · {result.failed.length} failed
          </Alert>
          {result.failed.map((failure) => <Typography key={failure.key} variant="s1">{failure.name}: {failure.message}</Typography>)}
          {result.failed.length > 0 && <Typography variant="s3" color="text.secondary">Successful imports are saved. Fix the failed skills and import them again.</Typography>}
        </>}
      </Stack>}
    </ModalWrapper>
  );
}

ImportSkillsDialog.propTypes = { open: PropTypes.bool.isRequired, onClose: PropTypes.func.isRequired };

export default function ImportSkillsDialog(props) {
  const { identity } = useSkillV11Scope();
  return props.open ? <ImportSkillsDialogContent key={identity} {...props} /> : null;
}
ImportSkillsDialogContent.propTypes = ImportSkillsDialog.propTypes;
