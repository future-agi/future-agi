import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useChangeCredentials } from "src/api/simulate-environments/versions";
import { useUploadSecretFile } from "src/api/simulate-environments/environments";
import { AGENT_VERSIONS_COPY } from "../workspace.constants";
import { refusalText } from "../evals/refusalText";
import { checkMissing, missingRequired } from "./agentVersionStatus";

const COPY = AGENT_VERSIONS_COPY.credentials;
const MONO = "ui-monospace, Menlo, monospace";
// The theme paints a focused label with the page background, which shows as a
// patch on a raised surface like a dialog; the outline's notch already clears it.
const FIELD_SX = {
  flex: 1,
  "& .MuiFormLabel-root.MuiInputLabel-shrink.Mui-focused": {
    backgroundColor: "transparent",
    padding: 0,
  },
};
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

let nextRowKey = 0;
const newRow = (name = "") => {
  nextRowKey += 1;
  return { key: nextRowKey, name, value: "" };
};
// What the check found missing first, then every other name the version has.
const rowsFor = (version) =>
  [
    ...new Set([
      ...checkMissing(version),
      ...(version?.credentialNames || []),
      ...missingRequired(version),
    ]),
  ].map((name) => newRow(name));

// Each sent row's problem with its name, if any; a row with no value is not
// sent, so its name is left alone.
const nameErrors = (rows) => {
  const sent = rows.filter((row) => row.value);
  const counts = sent.reduce((acc, row) => {
    const name = row.name.trim();
    acc[name] = (acc[name] || 0) + 1;
    return acc;
  }, {});
  return Object.fromEntries(
    sent.map((row) => {
      const name = row.name.trim();
      let problem = null;
      if (!NAME_RE.test(name)) problem = COPY.invalidName;
      else if (counts[name] > 1) problem = COPY.duplicateName;
      return [row.key, problem];
    }),
  );
};

const changeErrorText = (error) => {
  const text = COPY.errors[error?.error];
  if (typeof text === "function") return text(error);
  return text || refusalText(error, COPY.error);
};

// New values for the agent's credentials, saved as its next version, which is
// checked before it becomes the default. Values are never shown back.
export default function CredentialsForm({
  envId,
  version,
  onSaved,
  onCancel,
  extras = true,
  saveLabel = COPY.save,
  note = null,
}) {
  const change = useChangeCredentials(envId);
  // Dropped from the cache on reset, so the picked File doesn't linger.
  const upload = useUploadSecretFile({ gcTime: 0 });
  // Without extras the form asks only for what the check found missing.
  const [rows, setRows] = useState(() =>
    extras || !checkMissing(version).length
      ? rowsFor(version)
      : [...new Set(checkMissing(version))].map((name) => newRow(name)),
  );
  const [file, setFile] = useState(null);
  const [formError, setFormError] = useState(null);
  const fileInput = useRef(null);

  const names = version?.credentialNames || [];
  const errors = nameErrors(rows);
  const addsName = rows.some(
    (row) => row.value && row.name.trim() && !names.includes(row.name.trim()),
  );

  const setRow = (key, patch) =>
    setRows((current) =>
      current.map((row) => (row.key === key ? { ...row, ...patch } : row)),
    );
  const clearValues = () =>
    setRows((current) => current.map((row) => ({ ...row, value: "" })));
  const cancel = () => {
    upload.reset();
    onCancel();
  };

  const pickFile = (event) => {
    const [picked] = event.target.files || [];
    // The same file can be picked again after it is removed.
    event.target.value = "";
    if (!picked) return;
    setFormError(null);
    upload.mutate(
      { file: picked },
      {
        onSuccess: (result) => {
          setFile({ secretRef: result.secret_ref, name: result.name });
          upload.reset();
        },
      },
    );
  };

  const submit = (event) => {
    event.preventDefault();
    if (change.isPending) return;
    if (Object.values(errors).some(Boolean)) return;
    const values = Object.fromEntries(
      rows
        .filter((row) => row.name.trim() && row.value)
        .map((row) => [row.name.trim(), row.value]),
    );
    if (!Object.keys(values).length && !file) {
      setFormError(COPY.nothingToSave);
      return;
    }
    setFormError(null);
    change.mutate(
      { values, files: file ? [file.secretRef] : undefined },
      {
        onSuccess: (data) => {
          upload.reset();
          onSaved(data);
        },
        onError: (error) => setFormError(changeErrorText(error)),
        onSettled: () => {
          clearValues();
          // Drop the request, values included, from the mutation's state.
          change.reset();
        },
      },
    );
  };

  return (
    <Box component="form" noValidate onSubmit={submit}>
      {/* A note above the buttons says the same thing in fewer words. */}
      {!note && (
        <Typography sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}>
          {COPY.formHint}
        </Typography>
      )}
      {addsName && (
        <Typography
          data-testid="credentials-new-name-hint"
          sx={{ typography: "s3", color: "status.warning.fg", mb: 1.5 }}
        >
          {COPY.newNameHint}
        </Typography>
      )}
      <Stack spacing={1.25}>
        {rows.map((row, index) => (
          <Stack
            key={row.key}
            direction="row"
            spacing={1}
            alignItems="flex-start"
          >
            <TextField
              size="small"
              label={COPY.name}
              value={row.name}
              onChange={(e) => setRow(row.key, { name: e.target.value })}
              error={Boolean(errors[row.key])}
              helperText={errors[row.key] || undefined}
              InputProps={{ readOnly: !extras }}
              inputProps={{
                "aria-label": `${COPY.name} ${index + 1}`,
                spellCheck: false,
                style: { fontFamily: MONO },
              }}
              sx={FIELD_SX}
            />
            <TextField
              size="small"
              type="password"
              label={COPY.value}
              value={row.value}
              onChange={(e) => setRow(row.key, { value: e.target.value })}
              autoComplete="new-password"
              inputProps={{
                "aria-label": `${COPY.value} ${index + 1}`,
                autoComplete: "new-password",
              }}
              sx={FIELD_SX}
            />
            {extras && (
              <IconButton
                aria-label={COPY.removeVariable(row.name.trim())}
                onClick={() =>
                  setRows((current) =>
                    current.filter((one) => one.key !== row.key),
                  )
                }
                size="small"
                sx={{ mt: 0.5 }}
              >
                <Iconify icon="solar:close-linear" width={16} />
              </IconButton>
            )}
          </Stack>
        ))}
      </Stack>
      {extras && (
        <>
          <Button
            size="small"
            color="inherit"
            onClick={() => setRows((current) => [...current, newRow()])}
            startIcon={<Iconify icon="solar:add-linear" width={16} />}
            sx={{ mt: 1 }}
          >
            {COPY.addVariable}
          </Button>

          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ mt: 1.5 }}
          >
            <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
              {COPY.fileLabel}
            </Typography>
            {file ? (
              <>
                <Typography sx={{ typography: "s3", fontFamily: MONO }}>
                  {file.name}
                </Typography>
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => setFile(null)}
                >
                  {COPY.removeFile}
                </Button>
              </>
            ) : (
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                disabled={upload.isPending}
                onClick={() => fileInput.current?.click()}
              >
                {upload.isPending ? COPY.uploading : COPY.chooseFile}
              </Button>
            )}
            <input
              ref={fileInput}
              type="file"
              accept=".json,application/json"
              hidden
              data-testid="credential-file-input"
              onChange={pickFile}
            />
          </Stack>
          {upload.isError && (
            <Typography
              role="alert"
              sx={{ typography: "s3", color: "status.danger.fg", mt: 0.5 }}
            >
              {refusalText(upload.error, COPY.uploadError)}
            </Typography>
          )}
        </>
      )}

      {formError && (
        <Typography
          role="alert"
          sx={{ typography: "s2", color: "status.danger.fg", mt: 1.5 }}
        >
          {formError}
        </Typography>
      )}
      {note && (
        <Typography sx={{ typography: "s3", color: "text.secondary", mt: 2 }}>
          {note}
        </Typography>
      )}
      <Stack
        direction="row"
        spacing={1}
        justifyContent="flex-end"
        sx={{ mt: note ? 1 : 2 }}
      >
        <Button
          size="small"
          color="inherit"
          onClick={cancel}
          disabled={change.isPending}
        >
          {COPY.cancel}
        </Button>
        <Button
          size="small"
          variant="contained"
          type="submit"
          disabled={change.isPending || upload.isPending}
        >
          {change.isPending ? COPY.saving : saveLabel}
        </Button>
      </Stack>
    </Box>
  );
}

CredentialsForm.propTypes = {
  envId: PropTypes.string.isRequired,
  // The version whose names the form starts from (versionFromApi).
  version: PropTypes.object,
  // The save's answer: the new agent version, checked next.
  onSaved: PropTypes.func.isRequired,
  onCancel: PropTypes.func.isRequired,
  // Off where only the missing values are asked for: those names alone, fixed,
  // with no add, remove or file upload.
  extras: PropTypes.bool,
  saveLabel: PropTypes.string,
  // A line above the buttons, e.g. what saving does next.
  note: PropTypes.string,
};
