import PropTypes from "prop-types";
import { useRef, useState } from "react";
import {
  Box,
  Stack,
  Typography,
  Chip,
  IconButton,
  Tooltip,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useUpdateEnvironmentConfiguration } from "src/api/simulate-environments/environments";
import { uploadHarnessSecretFile } from "src/api/harness/harness";
import {
  CREDENTIAL_FILE_ALIAS,
  CREDENTIAL_FILE_ENV,
} from "./configurationEdit";

const MONO = "ui-monospace, Menlo, monospace";

const CHECK_LABEL = {
  accepted: "Accepted",
  rejected: "Rejected",
  not_checked: "Not checked",
};
const CHECK_COLOR = {
  accepted: "success",
  rejected: "error",
  not_checked: "warning",
};

const chipSx = {
  height: 19,
  borderRadius: 0.5,
  color: "text.secondary",
  border: "1px solid",
  borderColor: "divider",
  bgcolor: "transparent",
  "& .MuiChip-label": { px: 0.75, typography: "s3", fontWeight: 600 },
};

export default function CredentialFilesEditor({ envId, credentialFiles }) {
  const update = useUpdateEnvironmentConfiguration();
  const [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState([]);
  const [error, setError] = useState(null);
  const fileInput = useRef(null);

  const files = credentialFiles.length
    ? credentialFiles.map((file) => ({
        name: file.environment_name,
        uploaded: true,
      }))
    : [{ name: CREDENTIAL_FILE_ALIAS, uploaded: false }];

  const uploadAndSave = async (file) => {
    if (!file) return;
    setError(null);
    setChecks([]);
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("environment_name", CREDENTIAL_FILE_ENV);
      const result = await uploadHarnessSecretFile(form);
      update.mutate(
        {
          id: envId,
          body: {
            credential_files: { [CREDENTIAL_FILE_ALIAS]: result?.secret_ref },
          },
        },
        {
          onSuccess: (data) => setChecks(data?.checks || []),
          onError: (e) => {
            setChecks(e?.checks || []);
            setError(e?.detail || e?.message || "Couldn't save the file.");
          },
          onSettled: () => setBusy(false),
        },
      );
    } catch (e) {
      setError(e?.detail || e?.message || "Couldn't upload the file.");
      setBusy(false);
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  return (
    <Box>
      <input
        ref={fileInput}
        type="file"
        accept="application/json,.json"
        hidden
        data-testid="credential-file-input"
        onChange={(e) => uploadAndSave(e.target.files?.[0])}
      />
      <Stack
        divider={
          <Box sx={{ borderBottom: "1px solid", borderColor: "divider" }} />
        }
      >
        {files.map((file) => {
          const label = file.uploaded
            ? `Replace ${file.name}`
            : `Upload ${file.name}`;
          return (
            <Stack
              key={file.name}
              direction="row"
              alignItems="center"
              spacing={1.5}
              sx={{ px: 2.5, py: 1.375 }}
            >
              <Typography
                sx={{
                  flex: 1,
                  minWidth: 0,
                  typography: "s2",
                  fontWeight: 600,
                  fontFamily: MONO,
                  overflowWrap: "anywhere",
                }}
              >
                {file.name}
              </Typography>
              <Typography sx={{ typography: "s2", color: "text.subtitle" }}>
                {busy
                  ? "Uploading…"
                  : file.uploaded
                    ? "Uploaded"
                    : "Not uploaded"}
              </Typography>
              <Chip size="small" label="File" sx={chipSx} />
              <Tooltip title={label}>
                <span>
                  <IconButton
                    size="small"
                    aria-label={label}
                    disabled={busy}
                    onClick={() => fileInput.current?.click()}
                  >
                    <Iconify
                      icon={
                        file.uploaded
                          ? "solar:pen-2-linear"
                          : "solar:upload-linear"
                      }
                      width={15}
                      sx={{ color: "text.subtitle" }}
                    />
                  </IconButton>
                </span>
              </Tooltip>
            </Stack>
          );
        })}
      </Stack>
      {(error || checks.length > 0) && (
        <Stack
          spacing={1}
          sx={{
            px: 2.5,
            py: 1.5,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          {error && (
            <Typography
              role="alert"
              sx={{ typography: "s2", color: "error.main" }}
            >
              {error}
            </Typography>
          )}
          {checks.map((check) => (
            <Stack
              key={`${check.label}:${check.aliases.join(",")}`}
              direction="row"
              alignItems="center"
              spacing={1}
            >
              <Chip
                size="small"
                color={CHECK_COLOR[check.status] || "default"}
                label={CHECK_LABEL[check.status] || check.status}
              />
              <Typography sx={{ typography: "s2" }}>{check.message}</Typography>
            </Stack>
          ))}
        </Stack>
      )}
    </Box>
  );
}

CredentialFilesEditor.propTypes = {
  envId: PropTypes.string.isRequired,
  credentialFiles: PropTypes.array.isRequired,
};
