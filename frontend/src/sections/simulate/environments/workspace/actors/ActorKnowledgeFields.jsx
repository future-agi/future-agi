import PropTypes from "prop-types";
import { useId, useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  InputLabel,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { refusalText } from "../evals/refusalText";
import { ACTORS_COPY } from "./actors.constants";

const F = ACTORS_COPY.worldActors.form;

export default function ActorKnowledgeFields({
  files,
  sources,
  upload,
  uploadRules,
  problems,
  onChange,
}) {
  const inputId = useId();
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  // Evaluator-only files are listed, locked, so it's clear why an actor can't be given them.
  const evaluatorOnly = Object.entries(sources.roles ?? {})
    .filter(([, role]) => role === "evaluator_only")
    .map(([path]) => path);
  const uploadButton = upload && (
    <Button
      size="small"
      variant="outlined"
      component="label"
      disabled={uploading}
      startIcon={<Iconify icon="solar:cloud-upload-linear" width={16} />}
      sx={{ flexShrink: 0, color: "text.primary", borderColor: "divider" }}
    >
      {uploading ? F.uploading : F.upload}
      <input
        hidden
        type="file"
        accept={uploadRules.formats.join(",")}
        onChange={async (event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (!file) return;
          setUploading(true);
          setUploadError("");
          try {
            const path = await upload(file);
            onChange("files", [...new Set([...files, path])]);
          } catch (failure) {
            setUploadError(refusalText(failure, F.uploadFailed));
          } finally {
            setUploading(false);
          }
        }}
      />
    </Button>
  );
  return (
    <Box>
      {/* Upload sits on the label's row, so the file it adds appears just below in the field. */}
      <Stack
        direction="row"
        alignItems="center"
        justifyContent="space-between"
        spacing={1}
        sx={{ mb: 0.75 }}
      >
        <InputLabel
          variant="outlined"
          htmlFor={inputId}
          error={Boolean(problems.files)}
          sx={{ "&&&": { mb: 0 } }}
        >
          {F.files}
        </InputLabel>
        {uploadButton}
      </Stack>
      <Autocomplete
        id={inputId}
        multiple
        size="small"
        options={[...sources.files, ...evaluatorOnly]}
        getOptionDisabled={(path) => evaluatorOnly.includes(path)}
        renderOption={({ key, ...props }, path) => (
          <li key={key} {...props}>
            {evaluatorOnly.includes(path)
              ? `${path} · ${F.evaluatorOnly}`
              : path}
          </li>
        )}
        value={files}
        onChange={(_, next) => onChange("files", next)}
        renderInput={(params) => (
          <TextField
            {...params}
            error={Boolean(problems.files)}
            helperText={
              problems.files || (sources.files.length ? F.filesHelp : F.noFiles)
            }
          />
        )}
      />
      {upload && (
        <Typography
          sx={{
            mt: 0.5,
            typography: "s3",
            color: uploadError ? "error.main" : "text.secondary",
          }}
        >
          {uploadError ||
            F.uploadHelp(uploadRules.formats, uploadRules.max_label)}
        </Typography>
      )}
    </Box>
  );
}

ActorKnowledgeFields.propTypes = {
  files: PropTypes.arrayOf(PropTypes.string).isRequired,
  sources: PropTypes.shape({
    files: PropTypes.arrayOf(PropTypes.string).isRequired,
    // Every attached file's role by path; evaluator-only files are never in `files`.
    roles: PropTypes.objectOf(PropTypes.string),
  }).isRequired,
  // Resolves to the stored path; absent when the environment is locked.
  upload: PropTypes.func,
  uploadRules: PropTypes.shape({
    formats: PropTypes.arrayOf(PropTypes.string).isRequired,
    max_label: PropTypes.string.isRequired,
  }).isRequired,
  problems: PropTypes.objectOf(PropTypes.string).isRequired,
  // (field, next value)
  onChange: PropTypes.func.isRequired,
};
