import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import {
  checkRunning,
  useChangeCredentials,
} from "src/api/simulate-environments/versions";
import { useUploadSecretFile } from "src/api/simulate-environments/environments";
import { VersionBadge } from "../EnvVersionPin";
import { AGENT_VERSIONS_COPY } from "../workspace.constants";
import { refusalText } from "../evals/refusalText";
import { PreflightBadge, PreflightReport } from "./PreflightStatus";
import { activationText, named } from "./agentVersionStatus";

const COPY = AGENT_VERSIONS_COPY.credentials;
const MONO = "ui-monospace, Menlo, monospace";
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/;

// Names the default version's source reads but its credentials lack.
const missingRequired = (version) => {
  const required = version?.requiredVariableNames;
  if (!Array.isArray(required)) return [];
  const have = new Set(version.credentialNames || []);
  return required.filter((name) => !have.has(name));
};

let nextRowKey = 0;
const newRow = (name = "") => {
  nextRowKey += 1;
  return { key: nextRowKey, name, value: "" };
};
const rowsFor = (version) =>
  [...(version?.credentialNames || []), ...missingRequired(version)].map(
    (name) => newRow(name),
  );

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

// The default version's credential names (values are never shown) and a form
// that saves new values as the agent's next version, then follows its check.
export default function AgentCredentials({ envId, defaultVersion, versions }) {
  const change = useChangeCredentials(envId);
  // Dropped from the cache on reset, so the picked File doesn't linger.
  const upload = useUploadSecretFile({ gcTime: 0 });
  const [rows, setRows] = useState(null);
  const [file, setFile] = useState(null);
  const [formError, setFormError] = useState(null);
  const [savedId, setSavedId] = useState(null);
  const fileInput = useRef(null);

  const open = rows !== null;
  const names = defaultVersion?.credentialNames || [];
  const requiredMissing = missingRequired(defaultVersion);
  const saved = savedId && versions.find((one) => one.id === savedId);
  // Only this environment's own changes hold it back.
  const checking = versions.some(checkRunning);
  const errors = open ? nameErrors(rows) : {};
  const addsName =
    open &&
    rows.some(
      (row) => row.value && row.name.trim() && !names.includes(row.name.trim()),
    );

  const setRow = (key, patch) =>
    setRows((current) =>
      current.map((row) => (row.key === key ? { ...row, ...patch } : row)),
    );
  const clearValues = () =>
    setRows((current) =>
      current ? current.map((row) => ({ ...row, value: "" })) : current,
    );
  const close = () => {
    setRows(null);
    setFile(null);
    setFormError(null);
    upload.reset();
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
          setSavedId(data?.agent_version?.id || null);
          close();
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
    <Box
      data-testid="agent-credentials"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1.5,
        p: 2,
      }}
    >
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <Box flex={1} minWidth={0}>
          <Typography sx={{ typography: "s1", fontWeight: "fontWeightBold" }}>
            {COPY.title}
          </Typography>
          {defaultVersion && (
            <Typography
              sx={{ typography: "s3", color: "text.subtitle", mt: 0.25 }}
            >
              {COPY.subtitle(named(defaultVersion))}
            </Typography>
          )}
        </Box>
        {defaultVersion && !open && (
          <Tooltip arrow title={checking ? COPY.checkRunning : ""}>
            <Box
              component="span"
              sx={{ display: "inline-flex", flexShrink: 0 }}
            >
              <Button
                size="small"
                variant="outlined"
                color="inherit"
                disabled={checking}
                onClick={() => {
                  setFormError(null);
                  setRows(rowsFor(defaultVersion));
                }}
                sx={{ textTransform: "none" }}
              >
                {COPY.change}
              </Button>
            </Box>
          </Tooltip>
        )}
      </Stack>

      {!defaultVersion && (
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1 }}>
          {COPY.noDefault}
        </Typography>
      )}
      {defaultVersion && !names.length && !requiredMissing.length && (
        <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1 }}>
          {COPY.none}
        </Typography>
      )}
      {(names.length > 0 || requiredMissing.length > 0) && (
        <Stack direction="row" flexWrap="wrap" gap={0.75} sx={{ mt: 1 }}>
          {names.map((name) => (
            <Typography
              key={name}
              data-testid="credential-name"
              sx={{ typography: "s3", fontFamily: MONO }}
            >
              {name}
            </Typography>
          ))}
          {requiredMissing.map((name) => (
            <Stack key={name} direction="row" spacing={0.5} alignItems="center">
              <Typography
                data-testid="credential-name"
                sx={{ typography: "s3", fontFamily: MONO, color: "error.main" }}
              >
                {name}
              </Typography>
              <VersionBadge label={COPY.requiredMissing} />
            </Stack>
          ))}
        </Stack>
      )}

      {saved && !open && (
        <Box sx={{ mt: 1.5 }} data-testid="credentials-saved">
          <Stack direction="row" spacing={0.75} alignItems="center">
            <Typography sx={{ typography: "s2", color: "text.secondary" }}>
              {COPY.saved(named(saved))}
            </Typography>
            <PreflightBadge status={saved.preflightStatus} />
          </Stack>
          {saved.preflightStatus === "failed" && (
            <PreflightReport report={saved.preflightReport} />
          )}
          {activationText(saved) && (
            <Typography
              sx={{ typography: "s3", color: "warning.main", mt: 0.5 }}
            >
              {activationText(saved)}
            </Typography>
          )}
        </Box>
      )}

      {open && (
        <Box component="form" noValidate onSubmit={submit} sx={{ mt: 2 }}>
          <Typography
            sx={{ typography: "s3", color: "text.subtitle", mb: 1.5 }}
          >
            {COPY.formHint}
          </Typography>
          {addsName && (
            <Typography
              data-testid="credentials-new-name-hint"
              sx={{ typography: "s3", color: "warning.main", mb: 1.5 }}
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
                  inputProps={{
                    "aria-label": `${COPY.name} ${index + 1}`,
                    spellCheck: false,
                    style: { fontFamily: MONO },
                  }}
                  sx={{ flex: 1 }}
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
                  sx={{ flex: 1 }}
                />
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
                  <Iconify icon="mingcute:close-line" width={16} />
                </IconButton>
              </Stack>
            ))}
          </Stack>
          <Button
            size="small"
            color="inherit"
            onClick={() => setRows((current) => [...current, newRow()])}
            startIcon={<Iconify icon="mingcute:add-line" width={16} />}
            sx={{ textTransform: "none", mt: 1 }}
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
                  sx={{ textTransform: "none" }}
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
                sx={{ textTransform: "none" }}
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
              sx={{ typography: "s3", color: "error.main", mt: 0.5 }}
            >
              {refusalText(upload.error, COPY.uploadError)}
            </Typography>
          )}

          {formError && (
            <Typography
              role="alert"
              sx={{ typography: "s2", color: "error.main", mt: 1.5 }}
            >
              {formError}
            </Typography>
          )}
          <Stack
            direction="row"
            spacing={1}
            justifyContent="flex-end"
            sx={{ mt: 2 }}
          >
            <Button
              size="small"
              color="inherit"
              onClick={close}
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
              {change.isPending ? COPY.saving : COPY.save}
            </Button>
          </Stack>
        </Box>
      )}
    </Box>
  );
}

AgentCredentials.propTypes = {
  envId: PropTypes.string.isRequired,
  // The agent's default version (versionFromApi); none before the first.
  defaultVersion: PropTypes.object,
  // Every agent version, to follow the one a save made.
  versions: PropTypes.arrayOf(PropTypes.object).isRequired,
};
