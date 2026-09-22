import PropTypes from "prop-types";
import { useRef, useState } from "react";
import { Box, Stack, Typography, Button, TextField, Alert } from "@mui/material";
import Iconify from "src/components/iconify";
import { useUploadSecretFile } from "src/api/simulate-environments/environments";
import Label from "../components/Label";
import Field from "../components/Field";

// `secret_ref` is an object and cannot key a list; alias and name cover older drafts.
const secretFileKey = (f) => f?.secret_ref?.key || f?.environment_name || f?.name;

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

  // Never read the file's bytes into the browser — the raw File goes to the upload
  // endpoint and only the returned reference is kept; contents are mounted per run.
  // `environment_name` rides along: that alias is the key `secret_refs` needs.
  const onFile = (file) => {
    if (!file) return;
    upload.mutate(
      { file },
      {
        onSuccess: ({ secret_ref, environment_name, name, size }) =>
          onSecretFiles?.((prev) => [
            ...(prev ?? []),
            { name, size, secret_ref, environment_name },
          ]),
      },
    );
  };

  const removeSecretFile = (key) =>
    onSecretFiles?.((prev) => (prev ?? []).filter((f) => secretFileKey(f) !== key));

  return (
    <Box>
      {/* Credential-file upload sits at the toggle's level and stays visible even
          when the (optional) env-values body is collapsed: a source that needs a
          credential file must never be gated behind an "optional" accordion. */}
      <Stack direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
        <Button
          size="small"
          onClick={() => setOpen((o) => !o)}
          startIcon={<Iconify icon={open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"} width={12} />}
          sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.secondary", px: 0.5 }}
        >
          {open ? "Hide environment values" : "Environment values (optional)"}
        </Button>
        <Button
          size="small"
          disabled={upload.isPending}
          onClick={() => fileRef.current?.click()}
          startIcon={<Iconify icon="solar:upload-linear" width={12} />}
          sx={{ typography: "s3", fontWeight: "fontWeightSemiBold", color: "text.secondary", px: 0.5, flexShrink: 0 }}
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

      {/* Uploaded files echo here, always visible, so an upload made while the
          body is collapsed still confirms. */}
      {secretFiles.length > 0 && (
        <Stack spacing={1} sx={{ mt: 1 }}>
          {secretFiles.map((f) => (
            <Alert
              key={secretFileKey(f)}
              severity="success"
              variant="outlined"
              onClose={() => removeSecretFile(secretFileKey(f))}
              sx={{
                typography: "s3",
                py: 0.5,
                alignItems: "center",
                // Center the leading check, the text, and the close X on one line,
                // and shrink both icons to match the s3 body.
                "& .MuiAlert-icon": {
                  py: 0,
                  mr: 1,
                  alignItems: "center",
                  "& .MuiSvgIcon-root": { fontSize: 18 },
                },
                "& .MuiAlert-message": { py: 0 },
                "& .MuiAlert-action": {
                  py: 0,
                  mr: 0,
                  alignItems: "center",
                  "& .MuiSvgIcon-root": { fontSize: 18 },
                },
              }}
            >
              {f.name} uploaded · mounted per run, never written to the job
            </Alert>
          ))}
        </Stack>
      )}

      {open && (
        <Stack spacing={1.5} sx={{ mt: 1.25 }}>
          <Typography sx={{ typography: "s3", color: "text.subtitle" }}>
            Credentials the sandbox needs to run your code. Values stay in this browser session, are sent only for preflight and run execution, and are never written to jobs, logs, or artifacts.
          </Typography>
          <Box>
            <Box sx={{ mb: 0.5 }}>
              <Label>Paste .env contents</Label>
            </Box>
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
      // Older drafts persisted a bare string ref.
      secret_ref: PropTypes.oneOfType([PropTypes.string, PropTypes.object]),
      environment_name: PropTypes.string,
    }),
  ),
  onSecretFiles: PropTypes.func,
};
