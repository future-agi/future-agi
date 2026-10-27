import PropTypes from "prop-types";
import { useRef } from "react";
import {
  Box,
  Button,
  IconButton,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { useUploadSecretFile } from "src/api/simulate-environments/environments";
import { errorMessage } from "src/pages/dashboard/harness/harnessShared";
import { INPUT_SX, ROW_BOX_SX, T12 } from "../drawerStyles";
import { Field, HintLine, MONO } from "./formBits";

// `secret_ref` is an object and cannot key a list; alias and name cover older drafts.
const secretFileKey = (f) =>
  f?.secret_ref?.key || f?.environment_name || f?.name;

const shortcutSx = {
  ...T12,
  fontWeight: 600,
  color: "text.secondary",
  px: 0.75,
  minWidth: 0,
  height: 24,
};

/**
 * Vel's Environment values block, on the build's own fields: pasted .env
 * values, credential files (uploaded, only their reference kept) and extra
 * egress domains. Never folded away.
 */
export default function DrawerEnvValues({
  envText,
  onEnvText,
  egress,
  onEgress,
  secretFiles = [],
  onSecretFiles,
}) {
  const fileRef = useRef(null);
  const envFileRef = useRef(null);
  const upload = useUploadSecretFile();

  // The raw file goes to the upload endpoint; only its reference is kept.
  const onCredentialFile = (file) => {
    if (!file) return;
    upload.mutate(
      { file },
      {
        onSuccess: ({ secret_ref, environment_name, name, size }) =>
          onSecretFiles((prev) => [
            ...(prev ?? []),
            { name, size, secret_ref, environment_name },
          ]),
      },
    );
  };
  const onEnvFile = async (file) => {
    if (!file) return;
    const text = await file.text();
    onEnvText(envText ? `${envText.trimEnd()}\n${text}` : text);
  };

  return (
    <Box>
      <Stack direction="row" alignItems="center" spacing={0.5}>
        <Typography sx={{ ...T12, fontWeight: 600, flex: 1 }}>
          Environment values
        </Typography>
        <Button
          onClick={() => envFileRef.current?.click()}
          startIcon={<Iconify icon="solar:upload-linear" width={12} />}
          sx={shortcutSx}
        >
          Upload .env
        </Button>
        <input
          ref={envFileRef}
          type="file"
          hidden
          accept=".env,text/plain"
          onChange={(e) => {
            onEnvFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </Stack>
      <Typography
        component="div"
        sx={{ ...T12, color: "text.secondary", mb: 1 }}
      >
        <HintLine
          text="Test credentials and settings our sandbox runs your code with. Never a production key."
          hint="Values are sent only for preflight and runs, and never written to jobs, logs or artifacts."
        />
      </Typography>
      <TextField
        fullWidth
        multiline
        minRows={3}
        value={envText}
        onChange={(e) => onEnvText(e.target.value)}
        placeholder={"OPENAI_API_KEY=…\nLOG_LEVEL=info"}
        inputProps={{ "aria-label": "Environment values", spellCheck: false }}
        sx={{
          ...INPUT_SX,
          "& .MuiInputBase-inputMultiline": { fontFamily: MONO, ...T12 },
        }}
      />

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 1 }}>
        <Typography
          sx={{
            ...T12,
            color: upload.error ? "error.main" : "text.secondary",
            flex: 1,
          }}
        >
          {upload.error
            ? errorMessage(upload.error)
            : "Credential file: Google service-account JSON only, up to 5 MiB."}
        </Typography>
        <Button
          disabled={upload.isPending}
          onClick={() => fileRef.current?.click()}
          startIcon={<Iconify icon="solar:upload-linear" width={12} />}
          sx={shortcutSx}
        >
          Upload credential file
        </Button>
        <input
          ref={fileRef}
          type="file"
          hidden
          accept="application/json,.json"
          onChange={(e) => {
            onCredentialFile(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </Stack>
      {secretFiles.length > 0 && (
        <Stack spacing={0.5} sx={{ mt: 0.75 }}>
          {secretFiles.map((f) => (
            <Stack
              key={secretFileKey(f)}
              direction="row"
              alignItems="center"
              spacing={1}
              sx={{ ...ROW_BOX_SX, px: 1.25, py: 0.75 }}
            >
              <Iconify
                icon="solar:file-check-linear"
                width={16}
                sx={{ color: "text.secondary" }}
              />
              <Typography noWrap sx={{ ...T12, fontFamily: MONO }}>
                {f.name}
              </Typography>
              <Typography sx={{ ...T12, color: "text.secondary", flex: 1 }}>
                Mounted per run, never written to a run
              </Typography>
              <IconButton
                size="small"
                aria-label={`Remove ${f.name}`}
                onClick={() =>
                  onSecretFiles((prev) =>
                    (prev ?? []).filter(
                      (x) => secretFileKey(x) !== secretFileKey(f),
                    ),
                  )
                }
              >
                <Iconify icon="solar:close-linear" width={16} />
              </IconButton>
            </Stack>
          ))}
        </Stack>
      )}

      <Field
        label="Additional egress domains"
        mono
        value={egress}
        onChange={onEgress}
        placeholder="api.example.com, turn.example.com"
        helper="Optional. Public hostnames for hardcoded APIs or TURN endpoints. Everything else is firewalled while the agent runs."
        sx={{ mt: 1.5 }}
      />
    </Box>
  );
}
DrawerEnvValues.propTypes = {
  envText: PropTypes.string,
  onEnvText: PropTypes.func.isRequired,
  egress: PropTypes.string,
  onEgress: PropTypes.func.isRequired,
  secretFiles: PropTypes.array,
  onSecretFiles: PropTypes.func.isRequired,
};
