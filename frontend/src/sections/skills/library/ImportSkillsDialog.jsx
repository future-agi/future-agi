import { useState } from "react";
import PropTypes from "prop-types";
import { Alert, Box, Button, Checkbox, FormControlLabel, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { useImportPreview, useImportSkills } from "src/api/skills";
import ModalWrapper from "src/components/ModalWrapper/ModalWrapper";
import ContentSkeleton from "src/components/content-skeleton";
import { Upload } from "src/components/upload";
import ChecksList from "src/sections/skills/components/ChecksList";
import { LIMITS } from "src/sections/skills/constants";
import ImportPreviewTable from "./ImportPreviewTable";

function uploadForm(files) {
  const form = new FormData();
  files.forEach((file) => {
    const relativePath = file.webkitRelativePath || file.path || file.name;
    form.append("files", file, relativePath.replace(/^\.\//, ""));
  });
  return form;
}

export default function ImportSkillsDialog({ open, onClose }) {
  const previewMutation = useImportPreview();
  const importMutation = useImportSkills();
  const [files, setFiles] = useState([]);
  const [preview, setPreview] = useState(null);
  const [selected, setSelected] = useState([]);
  const [label, setLabel] = useState("");
  const [acknowledged, setAcknowledged] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const busy = previewMutation.isPending || importMutation.isPending;
  const selectedBundles = preview ? preview.bundles.filter((bundle) => selected.includes(bundle.key)) : [];
  const hasWarnings = selectedBundles.some((bundle) => bundle.checks.summary.warnings > 0);
  const canImport = selectedBundles.length > 0 && selectedBundles.every((bundle) => bundle.status !== "invalid" && bundle.checks.summary.errors === 0) && (!hasWarnings || acknowledged);

  const chooseFiles = (nextFiles) => {
    if (busy) return;
    setFiles(Array.from(nextFiles));
    setPreview(null);
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
      form.append("labels", JSON.stringify(label ? [label] : []));
      form.append("acknowledge_warnings", String(acknowledged));
      setResult(await importMutation.mutateAsync(form));
    } catch (_failure) {
      setError(preview ? "Couldn't finish importing. Review the preview and try again." : "Couldn't preview these files. Check the upload limits and file paths, then try again.");
    }
  };

  return (
    <ModalWrapper open={open} onClose={onClose} title="Import skills" modalWidth="880px" paperSx={{ maxWidth: 880 }}
      subTitle="Review files and checks before publishing versions to your workspace."
      isLoading={busy} isValid={Boolean(result) || (preview ? canImport : files.length > 0)}
      onSubmit={submit} actionBtnTitle={result ? "Done" : preview ? "Import" : "Preview import"}>
      <Stack spacing={2} sx={{ minWidth: 0, overflowY: "auto" }}>
        {error && <Alert severity="error">{error}</Alert>}
        {!result && <>
          <Upload multiple files={files} disabled={busy} showIllustration={false} hidePreview maxSize={LIMITS.MAX_ZIP_BYTES}
            onDropRejected={() => setError("Some files couldn't be selected. Each ZIP must be 10 MiB or smaller; each text file must be 512 KiB or smaller.")}
            heading="Drop a ZIP or skill files" description="ZIPs up to 10 MiB, or up to 200 loose text files. Include a SKILL.md in each skill folder."
            onDrop={(accepted) => chooseFiles(accepted)} />
          <Stack direction="row" spacing={1} alignItems="center">
            <Button component="label" variant="outlined" size="small" disabled={busy}>
              Choose folder
              <input type="file" hidden multiple webkitdirectory="" aria-label="Choose skill folder"
                disabled={busy} onChange={(event) => { chooseFiles(event.target.files); event.target.value = ""; }} />
            </Button>
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
              onChange={(event) => setLabel(event.target.value)}>
              <MenuItem value="">None</MenuItem><MenuItem value="staging">Staging</MenuItem><MenuItem value="production">Production</MenuItem>
            </TextField>
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
      </Stack>
    </ModalWrapper>
  );
}

ImportSkillsDialog.propTypes = { open: PropTypes.bool.isRequired, onClose: PropTypes.func.isRequired };
