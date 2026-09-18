import PropTypes from "prop-types";
import { useReducer, useRef } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, LinearProgress } from "@mui/material";
import Iconify from "src/components/iconify";
import { fData } from "src/utils/format-number";
import { uploadHarnessSource } from "src/api/harness/harness";
import { prepareSourceFolder } from "src/pages/dashboard/harness/sourceUpload";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import Field from "../components/Field";
import ContinueRow from "../components/ContinueRow";
import EnvironmentValues from "./EnvironmentValues";
import RuntimePreflight from "./RuntimePreflight";
import usePanelBuild from "../hooks/usePanelBuild";
import { CODE_UPLOAD_COPY } from "../codeUpload.constants";

const initial = {
  folderName: "",
  fileNames: [],
  entry: "",
  envText: "",
  egress: "",
  secretFiles: [],
  summary: null, // { fileCount, totalBytes, excluded } from the upload response
  archiveArtifactId: null,
  uploading: false,
  progress: 0,
  error: "",
};

function reducer(s, a) {
  switch (a.type) {
    case "reset":
      return initial;
    // A folder is uploaded as one archive, so a new selection replaces the old
    // one wholesale and re-gates the CTA until this upload resolves.
    case "selected":
      return {
        ...s,
        folderName: a.folderName,
        fileNames: a.fileNames,
        entry: s.entry || a.fileNames[0] || "",
        summary: null,
        archiveArtifactId: null,
        uploading: true,
        progress: 0,
        error: "",
      };
    case "progress":
      return { ...s, progress: a.value };
    case "prepareError":
      return { ...s, error: a.message };
    case "uploadSuccess":
      return { ...s, uploading: false, progress: 100, archiveArtifactId: a.id, summary: a.summary, error: "" };
    case "uploadFail":
      return { ...s, uploading: false, archiveArtifactId: null, error: a.message };
    case "set": {
      const value = typeof a.value === "function" ? a.value(s[a.field]) : a.value;
      return { ...s, [a.field]: value };
    }
    default:
      return s;
  }
}

// Hidden folder picker reused by both the empty drop zone and the Replace
// button. webkitdirectory/directory are spread via Box (a raw <input> trips
// react/no-unknown-property), matching the product's HarnessCreate.
function FolderInput({ onPick }) {
  return (
    <Box
      component="input"
      hidden
      multiple
      directory=""
      webkitdirectory=""
      type="file"
      onChange={(e) => {
        // Copy the FileList before resetting value="" — the reset empties
        // e.target.files, and a live reference would empty with it.
        const list = Array.from(e.target.files || []);
        e.target.value = "";
        if (list.length) onPick(list);
      }}
    />
  );
}
FolderInput.propTypes = { onPick: PropTypes.func.isRequired };

export default function PanelCodeUpload() {
  const [form, dispatch] = useReducer(reducer, initial);
  const build = usePanelBuild();
  // Any edit invalidates a prior preflight result, so re-disable Build.
  const set = (field) => (value) => {
    dispatch({ type: "set", field, value });
    build.resetPreflight();
  };
  // Each new folder selection bumps this; an in-flight upload only applies its
  // result if it is still the latest, so switching folders mid-upload can't let
  // a stale upload clobber the current selection.
  const uploadSeq = useRef(0);
  const {
    folderName,
    fileNames,
    entry,
    envText,
    egress,
    secretFiles,
    summary,
    archiveArtifactId,
    uploading,
    progress,
    error,
  } = form;

  const chosen = uploading || !!summary;
  const canGo = !!archiveArtifactId && !uploading;

  const summaryLine = () => {
    if (uploading) return `${CODE_UPLOAD_COPY.uploadingHint} ${progress}%`;
    if (!summary) return "";
    const parts = [`${summary.fileCount} file${summary.fileCount === 1 ? "" : "s"}`];
    if (summary.totalBytes) parts.push(fData(summary.totalBytes));
    if (summary.excluded) parts.push(`${summary.excluded} excluded`);
    return parts.join(" · ");
  };

  const buildSource = () => ({
    kind: "upload",
    entry: entry.trim(),
    files: fileNames.map((name) => ({ name })),
    archive_artifact_id: archiveArtifactId,
    envText: envText.trim() || null,
    egress: egress.trim() || null,
    secretFiles,
  });

  const onDrop = async (list) => {
    // A new folder supersedes both any upload in flight and any prior preflight.
    build.resetPreflight();
    const seq = (uploadSeq.current += 1);
    let prepared;
    try {
      prepared = prepareSourceFolder(list);
    } catch (e) {
      dispatch({ type: "prepareError", message: e.message });
      return;
    }
    dispatch({
      type: "selected",
      folderName: prepared.name,
      fileNames: prepared.paths,
    });

    const formData = new FormData();
    prepared.files.forEach((file, i) => {
      formData.append("files", file, file.name);
      formData.append("paths", prepared.paths[i]);
    });
    formData.append("name", prepared.name);
    try {
      const result = await uploadHarnessSource(formData, (evt) => {
        if (seq === uploadSeq.current && evt?.total) {
          dispatch({ type: "progress", value: Math.round((evt.loaded / evt.total) * 100) });
        }
      });
      if (seq !== uploadSeq.current) return;
      dispatch({
        type: "uploadSuccess",
        id: result?.source_id,
        summary: {
          name: result?.name ?? prepared.name,
          fileCount: result?.file_count ?? prepared.files.length,
          totalBytes: result?.total_bytes ?? prepared.totalBytes,
          excluded: prepared.excludedCount,
        },
      });
    } catch (e) {
      if (seq !== uploadSeq.current) return;
      // A 400 here is almost always the runner's per-request field-count limit
      // (each file sends two fields), so name that cause instead of a generic error.
      const message = e?.response?.status === 400 ? CODE_UPLOAD_COPY.tooManyFiles : errorMessage(e);
      dispatch({ type: "uploadFail", message });
    }
  };

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
      {!chosen ? (
        <Box
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => { e.preventDefault(); if (e.dataTransfer?.files) onDrop(e.dataTransfer.files); }}
          sx={{
            border: "1px dashed",
            borderColor: (th) => th.palette.mode === "dark" ? alpha(th.palette.text.primary, 0.25) : th.palette.divider,
            borderRadius: 1.5,
            bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
            p: 3,
            textAlign: "center",
          }}
        >
          <Iconify icon="solar:cloud-upload-linear" width={28} sx={{ color: "text.subtitle" }} />
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold", mt: 1 }}>
            {CODE_UPLOAD_COPY.title}
          </Typography>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {CODE_UPLOAD_COPY.hint}
          </Typography>
          <Button
            component="label"
            size="small"
            variant="outlined"
            sx={{ mt: 1.5, typography: "s2", fontWeight: "fontWeightBold" }}
          >
            {CODE_UPLOAD_COPY.choose}
            <FolderInput onPick={onDrop} />
          </Button>
        </Box>
      ) : (
        // Uploaded/uploading summary — the archive is all-or-nothing, so we show a
        // single summary + Replace rather than an editable per-file list.
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1.5,
            bgcolor: (th) => alpha(th.palette.text.primary, 0.02),
            p: 1.75,
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.5}>
            <Iconify icon="solar:folder-with-files-bold" width={24} sx={{ color: "text.subtitle", flexShrink: 0 }} />
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography sx={{ typography: "s1", fontWeight: "fontWeightSemiBold" }} noWrap>
                {summary?.name || folderName || CODE_UPLOAD_COPY.title}
              </Typography>
              <Typography sx={{ typography: "s3", color: "text.subtitle" }} noWrap>
                {summaryLine()}
              </Typography>
            </Box>
            <Button
              component="label"
              size="small"
              color="inherit"
              disabled={uploading}
              sx={{ flexShrink: 0, typography: "s2", fontWeight: "fontWeightBold" }}
            >
              {CODE_UPLOAD_COPY.replace}
              <FolderInput onPick={onDrop} />
            </Button>
          </Stack>
          {uploading && (
            <LinearProgress
              variant={progress > 0 ? "determinate" : "indeterminate"}
              value={progress}
              sx={{ mt: 1.25, borderRadius: 1 }}
            />
          )}
        </Box>
      )}

      {error && (
        <Typography sx={{ typography: "s3" }} color="error.main">
          {error}
        </Typography>
      )}

      {chosen && (
        <Field
          label="Entry file"
          value={entry} onChange={set("entry")}
          mono
          helper={CODE_UPLOAD_COPY.entryHelper}
        />
      )}

      <EnvironmentValues
        envText={envText} onEnvText={set("envText")}
        egress={egress} onEgress={set("egress")}
        secretFiles={secretFiles} onSecretFiles={set("secretFiles")}
      />
      <RuntimePreflight
        status={build.status}
        canRun={canGo}
        onRun={() => build.runPreflight(buildSource())}
        checks={build.checks}
        state={build.state}
        error={build.error}
      />
      <ContinueRow
        disabled={!build.readyToSubmit}
        hint={build.status === "done" ? "Resolve the checks above" : "Run preflight to continue"}
        onClick={build.commitBuild}
      />
    </Stack>
  );
}
