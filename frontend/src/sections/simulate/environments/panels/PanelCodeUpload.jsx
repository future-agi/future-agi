import PropTypes from "prop-types";
import { useReducer } from "react";
import { alpha } from "@mui/material/styles";
import { Box, Stack, Typography, Button, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import { fData } from "src/utils/format-number";
import Label from "../components/Label";
import Field from "../components/Field";
import ContinueRow from "../components/ContinueRow";
import EnvironmentValues from "./EnvironmentValues";
import { CODE_UPLOAD_COPY } from "../codeUpload.constants";
import { prepareSourceFolder } from "src/pages/dashboard/harness/sourceUpload";

const initial = {
  files: [],
  entry: "",
  envText: "",
  egress: "",
  secretFiles: [],
  excludedCount: 0,
  error: "",
};

function reducer(s, a) {
  if (a.type === "reset") return initial;
  const value = typeof a.value === "function" ? a.value(s[a.field]) : a.value;
  return { ...s, [a.field]: value };
}

export default function PanelCodeUpload({ onBuild }) {
  const [form, dispatch] = useReducer(reducer, initial);
  const set = (field) => (value) => dispatch({ field, value });
  const { files, entry, envText, egress, secretFiles, excludedCount, error } = form;
  const canGo = files.length > 0;
  const summary = files.length
    ? `${files.length} file${files.length === 1 ? "" : "s"}${
        excludedCount > 0 ? ` · ${excludedCount} excluded` : ""
      }`
    : "";

  const onDrop = (list) => {
    let prepared;
    try {
      prepared = prepareSourceFolder(list);
    } catch (e) {
      set("error")(e.message);
      return;
    }
    const added = prepared.files.map((f, i) => ({
      name: prepared.paths[i],
      size: f.size,
    }));
    set("error")("");
    set("files")(added);
    set("excludedCount")(prepared.excludedCount);
    if (!entry && added[0]) set("entry")(added[0].name);
  };

  return (
    <Stack spacing={1.75} sx={{ p: 2.5 }}>
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
          {/* Folder picker — Box component="input" spreads the non-standard
              directory/webkitdirectory attrs the way the product's HarnessCreate
              does (a raw <input> trips react/no-unknown-property). */}
          <Box
            component="input"
            hidden
            multiple
            directory=""
            webkitdirectory=""
            type="file"
            onChange={(e) => e.target.files && onDrop(e.target.files)}
          />
        </Button>
      </Box>
      {error && (
        <Typography sx={{ typography: "s3" }} color="error.main">
          {error}
        </Typography>
      )}
      {files.length > 0 && (
        <Box>
          <Label>{CODE_UPLOAD_COPY.uploaded} ({files.length})</Label>
          <Typography sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}>
            {summary}
          </Typography>
          <Stack spacing={0.5} sx={{ mt: 0.75 }}>
            {files.map((f, i) => (
              <Stack
                key={`${f.name}-${i}`}
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ px: 1, py: 0.75, borderRadius: 1, bgcolor: (th) => alpha(th.palette.text.primary, 0.04) }}
              >
                <Iconify icon="solar:document-linear" width={13} sx={{ color: "text.subtitle" }} />
                <Typography sx={{ typography: "s2", fontFamily: "ui-monospace, Menlo, monospace", flex: 1, minWidth: 0 }} noWrap>
                  {f.name}
                </Typography>
                <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
                  {fData(f.size)}
                </Typography>
                <IconButton size="small" onClick={() => set("files")((prev) => prev.filter((_, x) => x !== i))}>
                  <Iconify icon="solar:trash-bin-minimalistic-linear" width={13} />
                </IconButton>
              </Stack>
            ))}
          </Stack>
        </Box>
      )}
      {files.length > 0 && (
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
      <ContinueRow
        disabled={!canGo}
        hint={CODE_UPLOAD_COPY.emptyHint}
        onClick={() => onBuild?.({
          kind: "upload",
          entry: entry.trim(),
          files: files.map((f) => ({ name: f.name, size: f.size })),
          envText: envText.trim() || null,
          egress: egress.trim() || null,
          secretFiles,
        })}
      />
    </Stack>
  );
}
PanelCodeUpload.propTypes = { onBuild: PropTypes.func };
