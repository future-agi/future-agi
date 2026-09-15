import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Stack, Typography, Button, TextField, Alert } from "@mui/material";
import Iconify from "src/components/iconify";
import { useUploadSecretFile } from "src/api/simulate-environments/environments";
import Label from "../components/Label";
import Field from "../components/Field";

export default function EnvironmentValues({
  envText,
  onEnvText,
  egress,
  onEgress,
  secretFiles = [],
  onSecretFiles,
}) {
  const [open, setOpen] = useState(false);
  const fileRef = useRef(null);
  const upload = useUploadSecretFile();

  // Never read the file's bytes into the browser. We hand the raw File to the
  // (mocked) upload endpoint and keep only the returned reference — contents
  // are mounted per run, never written into .env or the draft.
  const onFile = (file) => {
    if (!file) return;
    upload.mutate(
      { file },
      {
        onSuccess: ({ secret_ref, name, size }) =>
          onSecretFiles?.((prev) => [
            ...(prev ?? []),
            { name, size, secret_ref },
          ]),
      },
    );
  };

  const removeSecretFile = (ref) =>
    onSecretFiles?.((prev) => (prev ?? []).filter((f) => f.secret_ref !== ref));

  return (
    <Box>
      <Button
        size="small"
        onClick={() => setOpen((o) => !o)}
        startIcon={<Iconify icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
        sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.secondary", px: 0.5 }}
      >
        {open ? "Hide environment values" : "Environment values (optional)"}
      </Button>
      {open && (
        <Stack spacing={1.5} sx={{ mt: 1.25 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Credentials the sandbox needs to run your code. Values stay in this browser session, are sent only for preflight and run execution, and are never written to jobs, logs, or artifacts.
          </Typography>
          <Box>
            <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 0.5 }}>
              <Label>Paste .env contents</Label>
              <Button
                size="small"
                disabled={upload.isPending}
                onClick={() => fileRef.current?.click()}
                startIcon={<Iconify icon="solar:upload-linear" width={12} />}
                sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.secondary", px: 0.5 }}
              >
                Upload credential file
              </Button>
              <input
                ref={fileRef}
                type="file"
                hidden
                accept="application/json,.json"
                onChange={(e) => {
                  onFile(e.target.files?.[0]);
                  e.target.value = "";
                }}
              />
            </Stack>
            <TextField
              fullWidth
              multiline
              minRows={3}
              value={envText}
              onChange={(e) => onEnvText?.(e.target.value)}
              placeholder={"OPENAI_API_KEY=…\nDATABASE_URL=…"}
              sx={{ "& .MuiInputBase-input": { typography: "s2", fontFamily: "ui-monospace, Menlo, monospace" } }}
            />
          </Box>
          {secretFiles.map((f) => (
            <Alert
              key={f.secret_ref}
              severity="success"
              variant="outlined"
              onClose={() => removeSecretFile(f.secret_ref)}
              sx={{ typography: "s3", py: 0.25 }}
            >
              {f.name} uploaded · mounted per run, never written to the job
            </Alert>
          ))}
          <Field
            label="Additional egress domains"
            placeholder="api.example.com, turn.example.com"
            value={egress}
            onChange={onEgress}
            mono
            helper="Comma or newline-separated public hostnames for hardcoded APIs / TURN endpoints. Everything else is firewalled at the sandbox."
          />
        </Stack>
      )}
    </Box>
  );
}
EnvironmentValues.propTypes = {
  envText: PropTypes.string,
  onEnvText: PropTypes.func,
  egress: PropTypes.string,
  onEgress: PropTypes.func,
  secretFiles: PropTypes.arrayOf(
    PropTypes.shape({
      name: PropTypes.string,
      size: PropTypes.number,
      secret_ref: PropTypes.string,
    }),
  ),
  onSecretFiles: PropTypes.func,
};
